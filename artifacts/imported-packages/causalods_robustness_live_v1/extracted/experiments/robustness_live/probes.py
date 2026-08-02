from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from robustness_live.common import (
    CONSUMER_GROUP,
    CORRELATION_CONTAINER,
    INCIDENT_INDEX,
    KAFKA_CONTAINER,
    KAFKA_TOPIC,
    OPENSEARCH_URL,
    RCA_INDEX,
    first_value,
    http_json,
    run_command,
)


@dataclass(frozen=True)
class SearchHit:
    document_id: str
    source: dict[str, Any]


def parse_consumer_group_lag(
    output: str,
    *,
    topic: str = KAFKA_TOPIC,
) -> int:
    total = 0
    found = False
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("GROUP") or line.startswith("Consumer group"):
            continue
        columns = line.split()
        if len(columns) < 6:
            continue
        # GROUP TOPIC PARTITION CURRENT-OFFSET LOG-END-OFFSET LAG ...
        if columns[1] != topic:
            continue
        lag_raw = columns[5]
        if lag_raw == "-":
            continue
        try:
            total += int(lag_raw)
            found = True
        except ValueError:
            continue
    if not found:
        return 0
    return total


class KafkaLagProbe:
    def __init__(
        self,
        *,
        group: str = CONSUMER_GROUP,
        topic: str = KAFKA_TOPIC,
        command_runner: Callable[..., Any] = run_command,
    ) -> None:
        self.group = group
        self.topic = topic
        self.command_runner = command_runner

    def read(self) -> int:
        completed = self.command_runner(
            [
                "docker", "compose", "exec", "-T", KAFKA_CONTAINER,
                "kafka-consumer-groups",
                "--bootstrap-server", "localhost:9092",
                "--group", self.group,
                "--describe",
            ],
            check=False,
            timeout=30,
        )
        if completed.returncode != 0:
            combined = f"{completed.stdout}\n{completed.stderr}"
            if "does not exist" in combined or "no active members" in combined.lower():
                return 0
            raise RuntimeError(
                "Não foi possível ler o lag do consumer group.\n"
                f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
            )
        return parse_consumer_group_lag(completed.stdout, topic=self.topic)

    def wait_at_least(self, minimum: int, timeout_seconds: float = 30.0) -> int:
        deadline = time.monotonic() + timeout_seconds
        last = 0
        while time.monotonic() < deadline:
            last = self.read()
            if last >= minimum:
                return last
            time.sleep(0.5)
        raise TimeoutError(
            f"Lag não atingiu {minimum}. Último valor={last}."
        )

    def wait_zero(self, timeout_seconds: float = 90.0) -> int:
        deadline = time.monotonic() + timeout_seconds
        last = self.read()
        while time.monotonic() < deadline:
            last = self.read()
            if last == 0:
                return 0
            time.sleep(1.0)
        raise TimeoutError(f"Backlog não drenou. Lag final={last}.")


class OpenSearchTraceProbe:
    def __init__(self, index_name: str, *, url: str = OPENSEARCH_URL) -> None:
        self.index_name = index_name
        self.url = url.rstrip("/")

    def search(self, trace_id: str, *, size: int = 20) -> list[SearchHit]:
        _, payload = http_json(
            "POST",
            f"{self.url}/{self.index_name}/_search",
            payload={
                "size": size,
                "query": {
                    "bool": {
                        "should": [
                            {"term": {"trace_id": trace_id}},
                            {"term": {"trace_id.keyword": trace_id}},
                        ],
                        "minimum_should_match": 1,
                    }
                },
            },
            timeout=10.0,
        )
        raw_hits = ((payload or {}).get("hits") or {}).get("hits") or []
        return [
            SearchHit(
                document_id=str(hit.get("_id", "")),
                source=dict(hit.get("_source") or {}),
            )
            for hit in raw_hits
        ]

    def wait(
        self,
        trace_id: str,
        *,
        timeout_seconds: float,
        minimum_count: int = 1,
        allow_absent: bool = False,
    ) -> list[SearchHit]:
        deadline = time.monotonic() + timeout_seconds
        last_error: str | None = None
        while time.monotonic() < deadline:
            try:
                hits = self.search(trace_id)
                if len(hits) >= minimum_count:
                    return hits
                last_error = None
            except RuntimeError as exc:
                last_error = str(exc)
            time.sleep(1.0)
        if allow_absent:
            return []
        raise TimeoutError(
            f"Nenhum documento em {self.index_name!r} para trace_id={trace_id!r}. "
            f"Último erro={last_error!r}."
        )


class RCAResultProbe(OpenSearchTraceProbe):
    def __init__(self, *, url: str = OPENSEARCH_URL) -> None:
        super().__init__(RCA_INDEX, url=url)

    @staticmethod
    def summarize(hits: list[SearchHit]) -> dict[str, Any]:
        if not hits:
            return {
                "count": 0,
                "document_ids": [],
                "top_service": None,
                "is_reliable": None,
                "confidence": None,
                "model_version": None,
                "pipeline_version": None,
            }
        source = hits[0].source
        return {
            "count": len(hits),
            "document_ids": [hit.document_id for hit in hits],
            "top_service": first_value(source, (("top_service",), ("root_cause", "service"))),
            "is_reliable": first_value(source, (("is_reliable",), ("reliability", "is_reliable"))),
            "confidence": first_value(source, (("confidence",), ("reliability", "confidence"))),
            "model_version": source.get("model_version"),
            "pipeline_version": source.get("pipeline_version"),
        }


class IncidentProbe(OpenSearchTraceProbe):
    def __init__(self, *, url: str = OPENSEARCH_URL) -> None:
        super().__init__(INCIDENT_INDEX, url=url)

    @staticmethod
    def summarize(hits: list[SearchHit]) -> dict[str, Any]:
        if not hits:
            return {"count": 0, "document_ids": [], "root_cause": None}
        source = hits[0].source
        return {
            "count": len(hits),
            "document_ids": [hit.document_id for hit in hits],
            "root_cause": first_value(source, (("root_cause", "service"), ("root_cause",))),
        }


class ProvenanceProbe:
    def __init__(self, expected_runtime: str = "v2") -> None:
        self.expected_runtime = expected_runtime

    def validate(self, hits: list[SearchHit]) -> tuple[bool | None, dict[str, Any]]:
        if not hits:
            return None, {
                "expected_model_version": f"xgboost-rca-{self.expected_runtime}",
                "expected_pipeline_version": f"causalods-rca-{self.expected_runtime}",
                "actual_model_version": None,
                "actual_pipeline_version": None,
            }
        source = hits[0].source
        actual_model = source.get("model_version")
        actual_pipeline = source.get("pipeline_version")
        expected_model = f"xgboost-rca-{self.expected_runtime}"
        expected_pipeline = f"causalods-rca-{self.expected_runtime}"
        valid = actual_model == expected_model and actual_pipeline == expected_pipeline
        return valid, {
            "expected_model_version": expected_model,
            "expected_pipeline_version": expected_pipeline,
            "actual_model_version": actual_model,
            "actual_pipeline_version": actual_pipeline,
        }


class RecoveryProbe:
    @staticmethod
    def container_state(container_name: str) -> str:
        return run_command(
            ["docker", "inspect", "-f", "{{.State.Status}}", container_name],
            timeout=15,
        ).stdout.strip()

    @staticmethod
    def restart_count(container_name: str = CORRELATION_CONTAINER) -> int:
        raw = run_command(
            ["docker", "inspect", "-f", "{{.RestartCount}}", container_name],
            timeout=15,
        ).stdout.strip()
        return int(raw)

    @staticmethod
    def started_at(container_name: str = CORRELATION_CONTAINER) -> str:
        return run_command(
            ["docker", "inspect", "-f", "{{.State.StartedAt}}", container_name],
            timeout=15,
        ).stdout.strip()

    @staticmethod
    def wait_container_running(container_name: str, timeout_seconds: float = 90.0) -> None:
        deadline = time.monotonic() + timeout_seconds
        last = "unknown"
        while time.monotonic() < deadline:
            try:
                last = RecoveryProbe.container_state(container_name)
                if last == "running":
                    return
            except RuntimeError:
                last = "not-found"
            time.sleep(1.0)
        raise TimeoutError(
            f"Container {container_name!r} não ficou running. Estado={last!r}."
        )

    @staticmethod
    def wait_started_at_changed(
        previous_started_at: str,
        *,
        container_name: str = CORRELATION_CONTAINER,
        timeout_seconds: float = 90.0,
    ) -> str:
        deadline = time.monotonic() + timeout_seconds
        last = previous_started_at
        while time.monotonic() < deadline:
            try:
                last = RecoveryProbe.started_at(container_name)
                if last and last != previous_started_at:
                    RecoveryProbe.wait_container_running(container_name, timeout_seconds=timeout_seconds)
                    return last
            except RuntimeError:
                pass
            time.sleep(1.0)
        raise TimeoutError(
            f"StartedAt de {container_name!r} não mudou. Valor={last!r}."
        )

    @staticmethod
    def wait_opensearch(timeout_seconds: float = 120.0) -> None:
        deadline = time.monotonic() + timeout_seconds
        last_error: str | None = None
        while time.monotonic() < deadline:
            try:
                status, payload = http_json("GET", OPENSEARCH_URL, timeout=3.0)
                if status == 200 and isinstance(payload, dict):
                    return
            except RuntimeError as exc:
                last_error = str(exc)
            time.sleep(1.0)
        raise TimeoutError(f"OpenSearch não recuperou. Último erro={last_error!r}.")
