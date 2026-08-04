from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from multimodal_v1.contracts import SignalType, UnifiedEvent
from multimodal_v1.identity import normalize_distributed_id
from multimodal_v1.ingestion.base import EventNormalizer
from multimodal_v1.ingestion.context import NormalizationContext
from multimodal_v1.ingestion.errors import IngestionError, UnknownBusinessSchemaError
from multimodal_v1.ingestion.otlp import timing_quality


class BusinessEventNormalizer(EventNormalizer):
    INPUT_SCHEMA_VERSION = "business-event-v1"

    def __init__(self) -> None:
        path = Path(__file__).resolve().parents[1] / "schemas" / "business-event-v1.schema.json"
        self._schema = json.loads(path.read_text(encoding="utf-8"))
        self._validator = Draft202012Validator(
            self._schema, format_checker=FormatChecker()
        )

    def decode(self, payload: bytes):
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exception:
            raise IngestionError("Business event não é JSON UTF-8 válido.") from exception
        if not isinstance(decoded, dict):
            raise IngestionError("Business event deve ser um objeto JSON.")
        return decoded

    def normalize(self, decoded, context: NormalizationContext):
        if decoded.get("schema_version") != self.INPUT_SCHEMA_VERSION:
            raise UnknownBusinessSchemaError(
                f"Schema de business event desconhecido: {decoded.get('schema_version')!r}."
            )
        errors = sorted(self._validator.iter_errors(decoded), key=lambda item: list(item.path))
        if errors:
            raise IngestionError(f"Business event inválido: {errors[0].message}")

        correlation_id, identity_flags = normalize_distributed_id(
            decoded["correlation_id"]
        )
        timestamp = decoded["event_timestamp"]
        # UnifiedEvent faz a validação timezone-aware e normaliza para UTC.
        attributes = dict(decoded.get("attributes", {}))
        if decoded.get("source_reference") is not None:
            attributes["reported_source_reference"] = decoded["source_reference"]
        if decoded.get("source_dataset") is not None:
            attributes["reported_source_dataset"] = decoded["source_dataset"]
        provisional = UnifiedEvent(
            event_id=decoded["event_id"], event_name=decoded["event_name"],
            event_type=decoded["event_name"], signal_type=SignalType.BUSINESS_EVENT,
            event_timestamp=timestamp, observed_timestamp=context.ingested_timestamp,
            ingested_timestamp=context.ingested_timestamp,
            correlation_id=correlation_id,
            business_entity_type=decoded.get("business_entity_type")
                or decoded["event_name"].split(".", 1)[0],
            business_entity_id=decoded["business_entity_id"],
            business_operation=decoded["business_operation"],
            business_outcome=decoded["business_outcome"],
            service_name=decoded["service_name"],
            attributes=attributes,
            source_reference=context.source_reference,
            source_dataset=context.source_dataset,
            data_quality_flags=identity_flags,
        )
        late, timing_flags = timing_quality(
            provisional.event_timestamp, context.ingested_timestamp,
            late_threshold_ms=context.late_threshold_ms,
            future_tolerance_ms=context.future_tolerance_ms,
        )
        payload = provisional.to_dict()
        payload["late_arrival_ms"] = late
        flags = list(payload["data_quality_flags"])
        flags.extend(timing_flags)
        payload["data_quality_flags"] = tuple(dict.fromkeys(flags))
        return (UnifiedEvent.from_dict(payload),)
