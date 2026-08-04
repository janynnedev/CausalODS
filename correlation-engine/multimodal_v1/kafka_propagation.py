from __future__ import annotations

from collections.abc import Iterable, Mapping

from multimodal_v1.identity import (
    CORRELATION_ATTRIBUTE,
    PropagationContext,
    normalize_distributed_id,
)


_ALLOWED_HEADERS = (
    "traceparent",
    "tracestate",
    "baggage",
    "business-correlation-id",
)
_SENSITIVE_MARKERS = (
    "authorization", "cookie", "token", "password", "secret",
    "email", "phone", "cpf", "ssn",
)


class KafkaContextPropagator:
    """Inject/extract W3C para headers kafka-python, sem tocar no payload."""

    def inject(self, context: PropagationContext) -> list[tuple[str, bytes]]:
        headers: list[tuple[str, bytes]] = []
        correlation_id, flags = normalize_distributed_id(context.correlation_id)
        if flags:
            correlation_id = None
        values = {
            "traceparent": context.traceparent,
            "tracestate": context.tracestate,
            "baggage": self._controlled_baggage(context.baggage, correlation_id),
            "business-correlation-id": correlation_id,
        }
        for key in _ALLOWED_HEADERS:
            value = values[key]
            if value:
                headers.append((key, value.encode("utf-8")))
        return headers

    def extract(self, headers: Iterable[tuple[str, bytes]] | None) -> PropagationContext:
        decoded: dict[str, str] = {}
        quality_flags: list[str] = []
        for raw_key, raw_value in headers or ():
            key = str(raw_key).lower()
            if key not in _ALLOWED_HEADERS or raw_value is None:
                continue
            try:
                decoded[key] = raw_value.decode("utf-8")
            except (AttributeError, UnicodeDecodeError):
                quality_flags.append("invalid_kafka_context_header")
        correlation_id, id_flags = normalize_distributed_id(
            decoded.get("business-correlation-id")
            or self._correlation_from_baggage(decoded.get("baggage"))
        )
        quality_flags.extend(id_flags)
        return PropagationContext(
            traceparent=decoded.get("traceparent"),
            tracestate=decoded.get("tracestate"),
            baggage=decoded.get("baggage"),
            correlation_id=correlation_id,
            quality_flags=tuple(dict.fromkeys(quality_flags)),
        )

    def build_span_links(self, contexts: Iterable[PropagationContext]) -> tuple[dict[str, str], ...]:
        """Representa fan-out/fan-in/batching sem forçar parentesco incorreto."""
        links = []
        for context in contexts:
            if context.traceparent:
                link = {"traceparent": context.traceparent}
                if context.tracestate:
                    link["tracestate"] = context.tracestate
                links.append(link)
        return tuple(links)

    def _controlled_baggage(self, baggage: str | None, correlation_id: str | None) -> str | None:
        entries: list[str] = []
        if baggage:
            for raw_entry in baggage.split(","):
                name, separator, value = raw_entry.strip().partition("=")
                if separator and name == CORRELATION_ATTRIBUTE:
                    normalized, _ = normalize_distributed_id(value)
                    if normalized:
                        entries.append(f"{CORRELATION_ATTRIBUTE}={normalized}")
        if correlation_id:
            canonical = f"{CORRELATION_ATTRIBUTE}={correlation_id}"
            if canonical not in entries:
                entries.append(canonical)
        return ",".join(entries) or None

    def _correlation_from_baggage(self, baggage: str | None) -> str | None:
        if not baggage:
            return None
        for raw_entry in baggage.split(","):
            name, separator, value = raw_entry.strip().partition("=")
            if separator and name == CORRELATION_ATTRIBUTE:
                return value
        return None


def contains_sensitive_header(headers: Iterable[tuple[str, bytes]]) -> bool:
    return any(
        any(marker in str(key).lower() for marker in _SENSITIVE_MARKERS)
        for key, _ in headers
    )
