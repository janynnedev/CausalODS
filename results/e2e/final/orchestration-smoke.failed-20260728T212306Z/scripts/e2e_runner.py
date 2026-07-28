from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
import uuid

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


REPOSITORY_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)

RESULTS_DIRECTORY = (
    REPOSITORY_ROOT
    / "results"
    / "e2e"
)

ORDER_URL = (
    "http://localhost:8081/orders"
)

SERVICE_BASE_URLS = {
    "order-service": (
        "http://localhost:8081"
    ),
    "payment-service": (
        "http://localhost:8082"
    ),
}

OPENSEARCH_URL = (
    "http://localhost:9200"
)

RCA_INDEX = (
    "causalods-rca-results"
)

INCIDENT_INDEX = (
    "causalods-incidents"
)

CORRELATION_CONTAINER = (
    "correlation-engine"
)


@dataclass(frozen=True)
class Scenario:

    name: str

    fault_target: (
        str | None
    )

    fault_mode: str

    expected_http_family: int

    expected_top_service: (
        str | None
    )

    expected_reliable: bool

    expected_incident: bool

    fault_rate: float = 1.0

    fault_delay_ms: int = 0

    fault_seed: int = 42

    expected_min_request_duration_ms: (
        float | None
    ) = None


SCENARIOS = {
    "healthy": Scenario(
        name="healthy",
        fault_target=None,
        fault_mode="none",
        fault_rate=0.0,
        fault_delay_ms=0,
        fault_seed=42,
        expected_http_family=2,
        expected_top_service=None,
        expected_reliable=False,
        expected_incident=False,
    ),

    "payment_failure": Scenario(
        name="payment_failure",
        fault_target=(
            "payment-service"
        ),
        fault_mode="error",
        fault_rate=1.0,
        fault_delay_ms=0,
        fault_seed=42,
        expected_http_family=5,
        expected_top_service=(
            "payment-service"
        ),
        expected_reliable=True,
        expected_incident=True,
    ),

    "payment_latency": Scenario(
        name="payment_latency",
        fault_target=(
            "payment-service"
        ),
        fault_mode="latency",
        fault_rate=1.0,
        fault_delay_ms=1500,
        fault_seed=42,
        expected_http_family=2,
        expected_top_service=(
            "payment-service"
        ),
        expected_reliable=True,
        expected_incident=True,
        expected_min_request_duration_ms=(
            1200.0
        ),
    ),

    "order_failure": Scenario(
        name="order_failure",
        fault_target=(
            "order-service"
        ),
        fault_mode="error",
        fault_rate=1.0,
        fault_delay_ms=0,
        fault_seed=42,
        expected_http_family=5,
        expected_top_service=(
            "order-service"
        ),
        expected_reliable=True,
        expected_incident=True,
    ),

    "order_latency": Scenario(
        name="order_latency",
        fault_target=(
            "order-service"
        ),
        fault_mode="latency",
        fault_rate=1.0,
        fault_delay_ms=1500,
        fault_seed=42,
        expected_http_family=2,
        expected_top_service=(
            "order-service"
        ),
        expected_reliable=True,
        expected_incident=True,
        expected_min_request_duration_ms=(
            1200.0
        ),
    ),
}


@dataclass
class ScenarioResult:

    run_id: str
    scenario: str
    iteration: int
    started_at: str

    finished_at: str | None = None
    duration_ms: float | None = None

    runtime_model_version: str | None = None
    model_version: str | None = None
    pipeline_version: str | None = None

    fault_target: str | None = None
    fault_mode: str | None = None
    fault_rate: float | None = None
    fault_delay_ms: int | None = None
    fault_seed: int | None = None

    http_status: int | None = None
    request_duration_ms: float | None = None

    rca_count_before: int | None = None
    rca_count_after: int | None = None

    incident_count_before: int | None = None
    incident_count_after: int | None = None

    rca_result_id: str | None = None
    trace_id: str | None = None

    top_service: str | None = None
    confidence: float | None = None
    is_reliable: bool | None = None

    incident_id: str | None = None
    incident_root_cause: str | None = None

    restart_count_before: int | None = None
    restart_count_after: int | None = None

    passed: bool = False

    failures: list[str] = field(
        default_factory=list
    )

def utc_now() -> datetime:

    return datetime.now(
        timezone.utc
    )


def iso_now() -> str:

    return utc_now().isoformat()


def parse_json(
    raw_body: str,
) -> Any:

    if not raw_body.strip():
        return None

    try:
        return json.loads(
            raw_body
        )

    except json.JSONDecodeError:
        return raw_body


def run_command(
    arguments: list[str],
    *,
    environment: (
        dict[str, str] | None
    ) = None,
) -> str:

    completed = subprocess.run(
        arguments,
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    if completed.returncode != 0:

        raise RuntimeError(
            "Comando falhou:\n"
            f"{' '.join(arguments)}\n\n"
            f"STDOUT:\n{completed.stdout}\n\n"
            f"STDERR:\n{completed.stderr}"
        )

    return completed.stdout.strip()


def read_rca_runtime_version() -> str:

    value = run_command(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            CORRELATION_CONTAINER,
            "printenv",
            "RCA_MODEL_VERSION",
        ]
    ).strip()

    if value not in {
        "v1",
        "v2",
    }:

        raise RuntimeError(
            "RCA_MODEL_VERSION inválida no "
            f"{CORRELATION_CONTAINER}: "
            f"{value!r}."
        )

    return value


def container_logs_since(
    container_name: str,
    since: str,
) -> str:
    """
    Lê stdout e stderr do container a partir de um instante RFC3339.
    """

    completed = subprocess.run(
        [
            "docker",
            "logs",
            "--since",
            since,
            container_name,
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    if completed.returncode != 0:

        raise RuntimeError(
            "Não foi possível ler os logs "
            f"do container {container_name!r}.\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    return "\n".join(
        part
        for part in (
            completed.stdout,
            completed.stderr,
        )
        if part
    )

def wait_for_order_trace_id(
    *,
    scenario_name: str,
    run_id: str,
    started_at: str,
    timeout_seconds: float = 15.0,
) -> str:
    """
    Localiza nos logs do order-service o trace_id associado
    ao produto único usado pelo cenário E2E.
    """

    expected_product = (
        f"e2e-{scenario_name}-{run_id}"
    )

    deadline = (
        time.monotonic()
        + timeout_seconds
    )

    while time.monotonic() < deadline:

        raw_logs = container_logs_since(
            container_name="order-service",
            since=started_at,
        )

        for line in reversed(
            raw_logs.splitlines()
        ):

            parsed = parse_json(
                line
            )

            if not isinstance(
                parsed,
                dict,
            ):
                continue

            message = str(
                parsed.get(
                    "message",
                    "",
                )
            )

            if expected_product not in message:
                continue

            trace_id = parsed.get(
                "trace_id"
            )

            if trace_id:

                trace_id = str(
                    trace_id
                )

                print(
                    (
                        "Order trace resolved"
                        f" | run_id={run_id}"
                        f" | trace_id={trace_id}"
                    ),
                    flush=True,
                )

                return trace_id

        time.sleep(0.5)

    raise TimeoutError(
        "Não foi possível localizar o trace_id "
        "do pedido experimental nos logs do "
        "order-service. "
        f"Produto esperado={expected_product!r}."
    )

def http_request(
    *,
    method: str,
    url: str,
    payload: Any = None,
    allow_http_error: bool = False,
    timeout_seconds: float = 10.0,
) -> tuple[int, Any]:

    body = None

    headers = {
        "Accept": "application/json",
    }

    if payload is not None:

        body = json.dumps(
            payload
        ).encode(
            "utf-8"
        )

        headers[
            "Content-Type"
        ] = "application/json"

    request = Request(
        url=url,
        data=body,
        headers=headers,
        method=method,
    )

    try:

        with urlopen(
            request,
            timeout=timeout_seconds,
        ) as response:

            raw_body = (
                response.read()
                .decode(
                    "utf-8",
                    errors="replace",
                )
            )

            return (
                response.status,
                parse_json(
                    raw_body
                ),
            )

    except HTTPError as exception:

        raw_body = (
            exception.read()
            .decode(
                "utf-8",
                errors="replace",
            )
        )

        if allow_http_error:

            return (
                exception.code,
                parse_json(
                    raw_body
                ),
            )

        raise RuntimeError(
            f"HTTP {exception.code} "
            f"ao acessar {url}: "
            f"{raw_body}"
        ) from exception

    except (
        URLError,
        ConnectionResetError,
        TimeoutError,
        OSError,
    ) as exception:

        raise RuntimeError(
            f"Não foi possível acessar "
            f"{url}: {type(exception).__name__}: "
            f"{exception}"
        ) from exception

def wait_for_container_running(
    container_name: str,
    timeout_seconds: float = 30.0,
) -> None:

    deadline = (
        time.monotonic()
        + timeout_seconds
    )

    last_state = None

    while time.monotonic() < deadline:

        try:

            last_state = run_command(
                [
                    "docker",
                    "inspect",
                    "-f",
                    "{{.State.Status}}",
                    container_name,
                ]
            )

            if last_state == "running":
                return

        except RuntimeError:

            last_state = "not-found"

        time.sleep(0.5)

    raise TimeoutError(
        f"O container {container_name!r} "
        "não entrou no estado running. "
        f"Último estado={last_state!r}."
    )

def wait_for_service_ready(
    service_name: str,
    *,
    max_attempts: int = 60,
    interval_seconds: float = 1.0,
    request_timeout_seconds: float = 2.0,
) -> None:
    """
    Aguarda o serviço responder semanticamente como UP.

    A verificação usa o endpoint Actuator do próprio serviço.
    """

    if service_name not in SERVICE_BASE_URLS:

        raise ValueError(
            "Serviço não configurado para readiness: "
            f"{service_name!r}."
        )

    health_url = (
        f"{SERVICE_BASE_URLS[service_name].rstrip('/')}"
        "/actuator/health"
    )

    last_status: int | None = None
    last_payload: Any = None
    last_error: str | None = None

    for attempt in range(
        1,
        max_attempts + 1,
    ):

        try:

            status, payload = http_request(
                method="GET",
                url=health_url,
                allow_http_error=True,
                timeout_seconds=(
                    request_timeout_seconds
                ),
            )

            last_status = status
            last_payload = payload

            application_status = (
                payload.get("status")
                if isinstance(
                    payload,
                    dict,
                )
                else None
            )

            if (
                status == 200
                and application_status == "UP"
            ):

                print(
                    (
                        f"{service_name} ready"
                        f" | attempts={attempt}"
                        " | check=http"
                        f" | status={status}"
                        " | application_status="
                        f"{application_status}"
                    ),
                    flush=True,
                )

                return

            last_error = (
                "healthcheck ainda não está UP"
                f" | http_status={status}"
                " | application_status="
                f"{application_status}"
            )

        except RuntimeError as exception:

            last_error = (
                f"{type(exception).__name__}: "
                f"{exception}"
            )

        time.sleep(
            interval_seconds
        )

    raise TimeoutError(
        f"{service_name} não ficou "
        "funcionalmente pronto. "
        f"url={health_url}; "
        f"tentativas={max_attempts}; "
        f"último_status={last_status!r}; "
        f"último_payload={last_payload!r}; "
        f"último_erro={last_error!r}."
    )


def read_fault_configuration(
    service_name: str,
) -> dict[str, str] | None:
    """
    Lê a configuração interna CAUSALODS_FAULT_* do container.

    Os nomes externos ORDER/PAYMENT existem apenas no Compose.
    Dentro de cada container os nomes permanecem genéricos.
    """

    try:

        output = run_command(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                service_name,
                "sh",
                "-lc",
                (
                    "printf '%s|%s|%s|%s' "
                    "\"$CAUSALODS_FAULT_MODE\" "
                    "\"$CAUSALODS_FAULT_RATE\" "
                    "\"$CAUSALODS_FAULT_DELAY_MS\" "
                    "\"$CAUSALODS_FAULT_SEED\""
                ),
            ]
        )

    except RuntimeError:

        return None

    parts = output.split("|")

    if len(parts) != 4:
        return None

    return {
        "mode": parts[0],
        "rate": parts[1],
        "delay_ms": parts[2],
        "seed": parts[3],
    }


def recreate_service(
    *,
    service_name: str,
    environment: dict[str, str],
) -> None:

    run_command(
        [
            "docker",
            "compose",
            "up",
            "-d",
            "--force-recreate",
            "--no-deps",
            service_name,
        ],
        environment=environment,
    )

    wait_for_container_running(
        service_name
    )

    wait_for_service_ready(
        service_name
    )


def configure_faults(
    *,
    target: str | None,
    mode: str,
    rate: float,
    delay_ms: int,
    seed: int,
) -> None:
    """
    Configura a falha em somente um serviço e mantém o outro em none.

    O Compose mapeia:
      CAUSALODS_ORDER_FAULT_*   -> order-service/CAUSALODS_FAULT_*
      CAUSALODS_PAYMENT_FAULT_* -> payment-service/CAUSALODS_FAULT_*
    """

    allowed_targets = {
        None,
        "order-service",
        "payment-service",
    }

    if target not in allowed_targets:

        raise ValueError(
            "Alvo de falha inválido: "
            f"{target!r}."
        )

    allowed_modes = {
        "none",
        "latency",
        "error",
        "latency_error",
    }

    if mode not in allowed_modes:

        raise ValueError(
            "Modo de falha inválido: "
            f"{mode!r}."
        )

    if target is None and mode != "none":

        raise ValueError(
            "Cenário sem alvo deve usar "
            "fault_mode='none'."
        )

    order_mode = (
        mode
        if target == "order-service"
        else "none"
    )

    payment_mode = (
        mode
        if target == "payment-service"
        else "none"
    )

    order_rate = (
        rate
        if target == "order-service"
        else 0.0
    )

    order_delay_ms = (
        delay_ms
        if target == "order-service"
        else 0
    )

    payment_rate = (
        rate
        if target == "payment-service"
        else 0.0
    )

    payment_delay_ms = (
        delay_ms
        if target == "payment-service"
        else 0
    )

    environment = os.environ.copy()

    environment.update(
        {
            "CAUSALODS_ORDER_FAULT_MODE":
                order_mode,
            "CAUSALODS_ORDER_FAULT_RATE":
                str(order_rate),
            "CAUSALODS_ORDER_FAULT_DELAY_MS":
                str(order_delay_ms),
            "CAUSALODS_ORDER_FAULT_SEED":
                str(seed),

            "CAUSALODS_PAYMENT_FAULT_MODE":
                payment_mode,
            "CAUSALODS_PAYMENT_FAULT_RATE":
                str(payment_rate),
            "CAUSALODS_PAYMENT_FAULT_DELAY_MS":
                str(payment_delay_ms),
            "CAUSALODS_PAYMENT_FAULT_SEED":
                str(seed),
        }
    )

    desired_configurations = {
        "payment-service": {
            "mode": payment_mode,
            "rate": str(payment_rate),
            "delay_ms": str(payment_delay_ms),
            "seed": str(seed),
        },
        "order-service": {
            "mode": order_mode,
            "rate": str(order_rate),
            "delay_ms": str(order_delay_ms),
            "seed": str(seed),
        },
    }

    changed_services: list[str] = []

    # payment-service vem primeiro para que o order-service
    # nunca seja liberado antes do downstream necessário.
    for service_name in (
        "payment-service",
        "order-service",
    ):

        current_configuration = (
            read_fault_configuration(
                service_name
            )
        )

        desired_configuration = (
            desired_configurations[
                service_name
            ]
        )

        if (
            current_configuration
            == desired_configuration
        ):

            print(
                (
                    "Fault configuration unchanged"
                    f" | service={service_name}"
                    f" | mode="
                    f"{desired_configuration['mode']}"
                ),
                flush=True,
            )

            continue

        recreate_service(
            service_name=service_name,
            environment=environment,
        )

        changed_services.append(
            service_name
        )

    for (
        service_name,
        expected_configuration,
    ) in desired_configurations.items():

        configured = (
            read_fault_configuration(
                service_name
            )
        )

        if configured != expected_configuration:

            raise RuntimeError(
                "Configuração de falha incorreta "
                f"no {service_name}. "
                f"Esperado={expected_configuration}; "
                f"encontrado={configured}."
            )

    print(
        (
            "Faults configured"
            f" | target={target}"
            f" | requested_mode={mode}"
            f" | order_mode={order_mode}"
            f" | payment_mode={payment_mode}"
            f" | target_rate={rate}"
            f" | target_delay_ms={delay_ms}"
            f" | seed={seed}"
            " | recreated="
            f"{changed_services or ['none']}"
        ),
        flush=True,
    )


def refresh_index(
    index_name: str,
) -> None:

    http_request(
        method="POST",
        url=(
            f"{OPENSEARCH_URL}/"
            f"{index_name}/_refresh"
        ),
    )


def index_count(
    index_name: str,
) -> int:

    _, payload = http_request(
        method="GET",
        url=(
            f"{OPENSEARCH_URL}/"
            f"{index_name}/_count"
        ),
    )

    return int(
        payload.get(
            "count",
            0,
        )
    )


def latest_document_since(
    *,
    index_name: str,
    timestamp_field: str,
    started_at: str,
) -> dict[str, Any] | None:

    _, payload = http_request(
        method="POST",
        url=(
            f"{OPENSEARCH_URL}/"
            f"{index_name}/_search"
        ),
        payload={
            "size": 1,

            "sort": [
                {
                    timestamp_field: {
                        "order": "desc",
                    }
                }
            ],

            "query": {
                "range": {
                    timestamp_field: {
                        "gte": started_at,
                    }
                }
            },
        },
    )

    hits = (
        payload
        .get("hits", {})
        .get("hits", [])
    )

    if not hits:
        return None

    return dict(
        hits[0].get(
            "_source",
            {},
        )
    )

def document_by_trace_id(
    *,
    index_name: str,
    trace_id: str,
) -> dict[str, Any] | None:
    """
    Busca um documento específico pelo trace_id.

    Consulta tanto trace_id quanto trace_id.keyword para ser
    compatível com diferentes mappings do OpenSearch.
    """

    _, payload = http_request(
        method="POST",
        url=(
            f"{OPENSEARCH_URL}/"
            f"{index_name}/_search"
        ),
        payload={
            "size": 1,

            "query": {
                "bool": {
                    "should": [
                        {
                            "term": {
                                "trace_id": trace_id,
                            }
                        },
                        {
                            "term": {
                                "trace_id.keyword":
                                    trace_id,
                            }
                        },
                    ],
                    "minimum_should_match": 1,
                }
            },
        },
    )

    hits = (
        payload
        .get("hits", {})
        .get("hits", [])
    )

    if not hits:
        return None

    document = dict(
        hits[0].get(
            "_source",
            {},
        )
    )

    document["_document_id"] = (
        hits[0].get(
            "_id"
        )
    )

    return document

def wait_for_rca_result_by_trace_id(
    *,
    trace_id: str,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """
    Aguarda o RCAResult correspondente ao trace experimental.
    """

    deadline = (
        time.monotonic()
        + timeout_seconds
    )

    while time.monotonic() < deadline:

        refresh_index(
            RCA_INDEX
        )

        document = document_by_trace_id(
            index_name=RCA_INDEX,
            trace_id=trace_id,
        )

        if document is not None:

            print(
                (
                    "RCAResult matched"
                    f" | trace_id={trace_id}"
                    " | document_id="
                    f"{document.get('_document_id')}"
                ),
                flush=True,
            )

            return document

        time.sleep(1.0)

    raise TimeoutError(
        "O RCAResult do trace experimental "
        "não apareceu no OpenSearch. "
        f"trace_id={trace_id!r}."
    )

def wait_for_count_increase(
    *,
    index_name: str,
    previous_count: int,
    timeout_seconds: float = 30.0,
) -> int:

    deadline = (
        time.monotonic()
        + timeout_seconds
    )

    current_count = (
        previous_count
    )

    while time.monotonic() < deadline:

        refresh_index(
            index_name
        )

        current_count = index_count(
            index_name
        )

        if current_count > previous_count:

            return current_count

        time.sleep(1.0)

    raise TimeoutError(
        f"O índice {index_name!r} "
        "não recebeu um novo documento. "
        f"Contagem anterior={previous_count}; "
        f"contagem atual={current_count}."
    )

def wait_for_index_quiet(
    *,
    index_name: str,
    quiet_seconds: float = 6.0,
    timeout_seconds: float = 30.0,
) -> int:
    """
    Aguarda a contagem do índice permanecer estável.

    O período de silêncio deve ser maior que o intervalo normal
    entre a finalização dos spans de um mesmo cenário.
    """

    deadline = (
        time.monotonic()
        + timeout_seconds
    )

    refresh_index(
        index_name
    )

    last_count = index_count(
        index_name
    )

    last_change_at = (
        time.monotonic()
    )

    while time.monotonic() < deadline:

        time.sleep(1.0)

        refresh_index(
            index_name
        )

        current_count = index_count(
            index_name
        )

        if current_count != last_count:

            last_count = current_count

            last_change_at = (
                time.monotonic()
            )

            continue

        if (
            time.monotonic()
            - last_change_at
            >= quiet_seconds
        ):

            return current_count

    raise TimeoutError(
        f"O índice {index_name!r} "
        "não estabilizou dentro do tempo limite. "
        f"Última contagem={last_count}."
    )

def container_restart_count(
    container_name: str,
) -> int:

    output = run_command(
        [
            "docker",
            "inspect",
            "-f",
            "{{.RestartCount}}",
            container_name,
        ]
    )

    return int(output)


def send_order_request(
    scenario_name: str,
    run_id: str,
    *,
    timeout_seconds: float = 15.0,
) -> tuple[int, float]:

    request_started_at = (
        time.monotonic()
    )

    status, response = http_request(
        method="POST",
        url=ORDER_URL,
        payload={
            "product": (
                f"e2e-{scenario_name}-"
                f"{run_id}"
            ),
            "quantity": 2,
        },
        allow_http_error=True,
        timeout_seconds=timeout_seconds,
    )

    request_duration_ms = (
        time.monotonic()
        - request_started_at
    ) * 1000.0

    print(
        (
            "Order request"
            f" | scenario={scenario_name}"
            f" | http_status={status}"
            f" | duration_ms="
            f"{request_duration_ms:.3f}"
            f" | response={response}"
        ),
        flush=True,
    )

    return (
        status,
        request_duration_ms,
    )

def evaluate(
    *,
    scenario: Scenario,
    result: ScenarioResult,
) -> list[str]:

    failures: list[str] = []

    if result.http_status is None:

        failures.append(
            "A requisição não retornou "
            "status HTTP."
        )

    elif (
        result.http_status // 100
        != scenario.expected_http_family
    ):

        failures.append(
            "Família HTTP inesperada: "
            f"status={result.http_status}; "
            "família esperada="
            f"{scenario.expected_http_family}xx."
        )

    if (
        scenario.expected_min_request_duration_ms
        is not None
    ):

        if result.request_duration_ms is None:

            failures.append(
                "A duração da requisição não foi "
                "registrada."
            )

        elif (
            result.request_duration_ms
            < scenario.expected_min_request_duration_ms
        ):

            failures.append(
                "Latência HTTP abaixo do mínimo "
                "esperado: observado="
                f"{result.request_duration_ms:.3f} ms; "
                "mínimo="
                f"{scenario.expected_min_request_duration_ms:.3f} ms."
            )

    if (
        result.rca_count_before is not None
        and result.rca_count_after is not None
    ):

        delta = (
            result.rca_count_after
            - result.rca_count_before
        )

        if delta < 1:

            failures.append(
                "Nenhum novo RCAResult foi criado "
                "persistido durante o cenário."
            )

    if (
        result.is_reliable
        is not scenario.expected_reliable
    ):

        failures.append(
            "Decisão de confiabilidade incorreta: "
            f"esperado={scenario.expected_reliable}; "
            f"encontrado={result.is_reliable}."
        )

    if (
        scenario.expected_top_service
        is not None
        and result.top_service
        != scenario.expected_top_service
    ):

        failures.append(
            "Causa raiz incorreta: "
            "esperado="
            f"{scenario.expected_top_service}; "
            "encontrado="
            f"{result.top_service}."
        )

    incident_created = (
        result.incident_count_before
        is not None
        and result.incident_count_after
        is not None
        and result.incident_count_after
        > result.incident_count_before
    )

    if (
        incident_created
        is not scenario.expected_incident
    ):

        failures.append(
            "Criação de incidente incorreta: "
            "esperado="
            f"{scenario.expected_incident}; "
            f"encontrado={incident_created}."
        )

    if (
        scenario.expected_incident

        and incident_created

        and scenario.expected_top_service
        is not None

        and result.incident_root_cause
        != scenario.expected_top_service
    ):

        failures.append(
            (
                "Causa raiz do incidente incorreta: "
                "esperado="
                f"{scenario.expected_top_service}; "
                "encontrado="
                f"{result.incident_root_cause}."
            )
    )

    if (
        result.restart_count_before
        is not None
        and result.restart_count_after
        is not None
        and result.restart_count_after
        > result.restart_count_before
    ):

        failures.append(
            "O correlation-engine reiniciou "
            "durante o cenário."
        )

    return failures


def run_scenario(
    scenario: Scenario,
    iteration: int,
    runtime_model_version: str,
) -> ScenarioResult:

    run_id = uuid.uuid4().hex[:12]

    started_at_datetime = (
        datetime.now(
            timezone.utc
        )
    )

    result = ScenarioResult(
        run_id=run_id,
        scenario=scenario.name,
        iteration=iteration,
        started_at=(
            started_at_datetime.isoformat()
        ),
        runtime_model_version=(
            runtime_model_version
        ),
        fault_target=scenario.fault_target,
        fault_mode=scenario.fault_mode,
        fault_rate=scenario.fault_rate,
        fault_delay_ms=scenario.fault_delay_ms,
        fault_seed=scenario.fault_seed,
    )

    print()
    print(
        "=" * 70,
        flush=True,
    )
    print(
        (
            f"Scenario: {scenario.name}"
            f" | iteration={iteration}"
            f" | run_id={run_id}"
        ),
        flush=True,
    )
    print(
        "=" * 70,
        flush=True,
    )

    try:

        configure_faults(
            target=scenario.fault_target,
            mode=scenario.fault_mode,
            rate=scenario.fault_rate,
            delay_ms=scenario.fault_delay_ms,
            seed=scenario.fault_seed,
        )

        preparation_rca_count = (
            wait_for_index_quiet(
                index_name=RCA_INDEX,
                quiet_seconds=7.0,
                timeout_seconds=30.0,
            )
        )

        print(
            (
                "Preparation traces settled"
                f" | rca_count="
                f"{preparation_rca_count}"
            ),
            flush=True,
        )

        refresh_index(
            RCA_INDEX
        )

        refresh_index(
            INCIDENT_INDEX
        )

        result.rca_count_before = (
            index_count(
                RCA_INDEX
            )
        )

        result.incident_count_before = (
            index_count(
                INCIDENT_INDEX
            )
        )

        result.restart_count_before = (
            container_restart_count(
                CORRELATION_CONTAINER
            )
        )

        request_started_at = (
            iso_now()
        )

        (
            result.http_status,
            result.request_duration_ms,
        ) = send_order_request(
            scenario_name=scenario.name,
            run_id=run_id,
            timeout_seconds=max(
                15.0,
                (
                    (
                        scenario.fault_delay_ms
                        or 0
                    )
                    / 1000.0
                ) + 5.0,
            ),
        )

        result.trace_id = (
            wait_for_order_trace_id(
                scenario_name=scenario.name,
                run_id=run_id,
                started_at=request_started_at,
            )
        )

        rca_document = (
            wait_for_rca_result_by_trace_id(
                trace_id=result.trace_id,
                timeout_seconds=30.0,
            )
        )

        result.rca_count_after = (
            wait_for_index_quiet(
                index_name=RCA_INDEX,
                quiet_seconds=6.0,
            )
        )

        new_rca_result_count = (
            result.rca_count_after
            - result.rca_count_before
        )

        if new_rca_result_count != 1:

            print(
                (
                    "Additional RCAResults observed"
                    f" | experimental_trace_id="
                    f"{result.trace_id}"
                    f" | total_new_documents="
                    f"{new_rca_result_count}"
                    " | experimental_result="
                    "matched_by_trace_id"
                ),
                flush=True,
            )

        if rca_document is None:

            raise RuntimeError(
                "O novo RCAResult não foi "
                "encontrado no OpenSearch."
            )

        result.rca_result_id = (
            rca_document.get(
                "rca_result_id"
            )
        )

        result.model_version = (
            rca_document.get(
                "model_version"
            )
        )

        result.pipeline_version = (
            rca_document.get(
                "pipeline_version"
            )
        )

        expected_pipeline_version = (
            "causalods-rca-"
            f"{runtime_model_version}"
        )

        if (
            result.pipeline_version
            != expected_pipeline_version
        ):

            raise RuntimeError(
                "A versão persistida da pipeline "
                "não corresponde ao runtime do "
                "correlation-engine. "
                f"Runtime={runtime_model_version!r}; "
                "pipeline_version="
                f"{result.pipeline_version!r}; "
                "esperado="
                f"{expected_pipeline_version!r}."
            )

        document_trace_id = (
            rca_document.get(
                "trace_id"
            )
        )

        if (
            document_trace_id is not None
            and str(document_trace_id)
            != result.trace_id
        ):

            raise RuntimeError(
                "O RCAResult recuperado possui "
                "trace_id diferente do pedido. "
                f"Pedido={result.trace_id!r}; "
                f"documento={document_trace_id!r}."
            )

        result.top_service = (
            rca_document.get(
                "top_service"
            )
        )

        confidence = (
            rca_document.get(
                "confidence"
            )
        )

        result.confidence = (
            float(confidence)
            if confidence is not None
            else None
        )

        result.is_reliable = (
            bool(
                rca_document.get(
                    "is_reliable"
                )
            )
        )

        should_wait_for_incident = (
            scenario.expected_incident
            and result.is_reliable is True
        )

        if should_wait_for_incident:

            result.incident_count_after = (
                wait_for_count_increase(
                    index_name=INCIDENT_INDEX,
                    previous_count=(
                        result.incident_count_before
                    ),
                )
            )

        else:

            # Permite observar tanto uma criação
            # indevida quanto a ausência de incidente.
            time.sleep(3.0)

            refresh_index(
                INCIDENT_INDEX
            )

            result.incident_count_after = (
                index_count(
                    INCIDENT_INDEX
                )
            )

        if (
            result.incident_count_after
            > result.incident_count_before
        ):

            incident_document = (
                latest_document_since(
                    index_name=(
                        INCIDENT_INDEX
                    ),
                    timestamp_field=(
                        "timestamp"
                    ),
                    started_at=(
                        request_started_at
                    ),
                )
            )

            if incident_document:

                result.incident_id = (
                    incident_document.get(
                        "incident_id"
                    )
                )

                root_cause = (
                    incident_document.get(
                        "root_cause",
                        {},
                    )
                )

                if isinstance(
                    root_cause,
                    dict,
                ):

                    result.incident_root_cause = (
                        root_cause.get(
                            "service"
                        )
                    )

        result.restart_count_after = (
            container_restart_count(
                CORRELATION_CONTAINER
            )
        )

        result.failures = evaluate(
            scenario=scenario,
            result=result,
        )

        result.passed = (
            len(result.failures)
            == 0
        )

    except Exception as exception:

        result.failures = [
            *(
                result.failures
                or []
            ),
            (
                f"{type(exception).__name__}: "
                f"{exception}"
            ),
        ]

        result.passed = False

    finally:

        finished_at_datetime = (
            datetime.now(
                timezone.utc
            )
        )

        result.finished_at = (
            finished_at_datetime.isoformat()
        )

        result.duration_ms = (
            finished_at_datetime
            - started_at_datetime
        ).total_seconds() * 1000.0

    status = (
        "PASSED"
        if result.passed
        else "FAILED"
    )

    print(
        (
            f"Result: {status}"
            f" | scenario={scenario.name}"
            f" | top_service="
            f"{result.top_service}"
            f" | reliable="
            f"{result.is_reliable}"
            f" | confidence="
            f"{result.confidence}"
            f" | incident="
            f"{result.incident_root_cause}"
            f" | runtime="
            f"{result.runtime_model_version}"
            f" | model_version="
            f"{result.model_version}"
            f" | pipeline_version="
            f"{result.pipeline_version}"
        ),
        flush=True,
    )

    for failure in (
        result.failures
        or []
    ):

        print(
            f"  - {failure}",
            flush=True,
        )

    return result


def save_results(
    results: list[ScenarioResult],
) -> tuple[Path, Path]:

    RESULTS_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    timestamp = (
        utc_now()
        .strftime(
            "%Y%m%dT%H%M%SZ"
        )
    )

    json_path = (
        RESULTS_DIRECTORY
        / f"e2e-{timestamp}.json"
    )

    csv_path = (
        RESULTS_DIRECTORY
        / f"e2e-{timestamp}.csv"
    )

    serialized = [
        asdict(result)
        for result in results
    ]

    json_path.write_text(
        json.dumps(
            serialized,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    csv_rows = []

    for item in serialized:

        csv_rows.append(
            {
                **item,
                "failures": " | ".join(
                    item.get(
                        "failures"
                    )
                    or []
                ),
            }
        )

    with csv_path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as csv_file:

        writer = csv.DictWriter(
            csv_file,
            fieldnames=list(
                csv_rows[0].keys()
            ),
        )

        writer.writeheader()

        writer.writerows(
            csv_rows
        )

    return (
        json_path,
        csv_path,
    )


def parse_arguments():

    parser = argparse.ArgumentParser(
        description=(
            "Executa cenários end-to-end "
            "do CausalODS."
        )
    )

    parser.add_argument(
        "--scenario",
        choices=[
            "all",
            *SCENARIOS.keys(),
        ],
        default="all",
    )

    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
    )

    parser.add_argument(
        "--expected-runtime-version",
        choices=[
            "v1",
            "v2",
        ],
        default=None,
        help=(
            "Interrompe a execução se o "
            "correlation-engine estiver usando "
            "outra RCA_MODEL_VERSION."
        ),
    )

    return parser.parse_args()


def main() -> int:

    arguments = parse_arguments()

    if arguments.repeat < 1:

        raise ValueError(
            "--repeat deve ser maior "
            "que zero."
        )

    runtime_model_version = (
        read_rca_runtime_version()
    )

    if (
        arguments.expected_runtime_version
        is not None
        and runtime_model_version
        != arguments.expected_runtime_version
    ):

        raise RuntimeError(
            "Versão RCA inesperada. "
            "correlation-engine="
            f"{runtime_model_version!r}; "
            "esperado="
            f"{arguments.expected_runtime_version!r}."
        )

    print(
        (
            "RCA runtime verified"
            f" | version={runtime_model_version}"
        ),
        flush=True,
    )

    if arguments.scenario == "all":

        selected_scenarios = list(
            SCENARIOS.values()
        )

    else:

        selected_scenarios = [
            SCENARIOS[
                arguments.scenario
            ]
        ]

    results: list[
        ScenarioResult
    ] = []

    try:

        for iteration in range(
            1,
            arguments.repeat + 1,
        ):

            for scenario in (
                selected_scenarios
            ):

                results.append(
                    run_scenario(
                        scenario=scenario,
                        iteration=iteration,
                        runtime_model_version=(
                            runtime_model_version
                        ),
                    )
                )

    finally:

        print()
        print(
            (
                "Restoring order-service and "
                "payment-service to "
                "fault_mode=none..."
            ),
            flush=True,
        )

        try:

            configure_faults(
                target=None,
                mode="none",
                rate=0.0,
                delay_ms=0,
                seed=42,
            )

        except Exception as exception:

            print(
                (
                    "Falha ao restaurar os serviços "
                    "para fault_mode=none: "
                    f"{exception}"
                ),
                flush=True,
            )

    json_path, csv_path = (
        save_results(
            results
        )
    )

    passed_count = sum(
        1
        for result in results
        if result.passed
    )

    print()
    print(
        "=" * 70,
        flush=True,
    )

    print(
        (
            f"Passed: {passed_count}"
            f"/{len(results)}"
        ),
        flush=True,
    )

    print(
        f"JSON: {json_path}",
        flush=True,
    )

    print(
        f"CSV:  {csv_path}",
        flush=True,
    )

    print(
        "=" * 70,
        flush=True,
    )

    return (
        0
        if passed_count
        == len(results)
        else 1
    )


if __name__ == "__main__":

    raise SystemExit(
        main()
    )