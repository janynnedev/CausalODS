import json
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jsonschema import Draft202012Validator, FormatChecker

from models.span import Span
from multimodal_v1.contracts import DerivedEvidence, SignalType, UnifiedEvent
from multimodal_v1.span_adapter import SpanToUnifiedEventAdapter


UTC_TIME = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
SCHEMA_DIR = Path(__file__).parents[1] / "multimodal_v1/schemas"


def load_schema(name):
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def validate_with_available_validator(instance, schema):
    """Usa jsonschema quando disponível e um gate estrutural no ambiente mínimo."""
    if Draft202012Validator is not None:
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(
            schema, format_checker=FormatChecker()
        )
        validator.validate(instance)
        return

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    required = set(schema["required"])
    properties = schema["properties"]
    if not required <= set(instance):
        raise ValueError("Campos obrigatórios ausentes no fallback do schema.")
    unknown = set(instance) - set(properties)
    if unknown:
        raise ValueError(f"Campos desconhecidos no schema: {sorted(unknown)}")
    version = properties["schema_version"]["const"]
    if instance["schema_version"] != version:
        raise ValueError("schema_version não corresponde ao const do schema.")
    timestamp_fields = (
        ("event_timestamp", "observed_timestamp", "ingested_timestamp")
        if version == "unified-event-v1"
        else ("inferred_at",)
    )
    for name in timestamp_fields:
        value = instance.get(name)
        if value is None:
            continue
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(f"{name} não possui timezone.")
    if version == "derived-evidence-v1":
        sources = instance["source_event_ids"]
        if not sources or len(sources) != len(set(sources)):
            raise ValueError("source_event_ids inválido.")
        confidence = instance["confidence"]
        if confidence is not None and not 0 <= confidence <= 1:
            raise ValueError("confidence inválida.")
    status = instance.get("status_code")
    if isinstance(status, bool):
        raise ValueError("status_code booleano.")


def event(**overrides):
    payload = {
        "event_id": "evt-001",
        "event_name": "order.created",
        "event_type": "domain_event",
        "signal_type": "business_event",
        "event_timestamp": UTC_TIME,
        "business_operation": "create_order",
        "attributes": {"nested": {"items": [1, 2]}},
    }
    payload.update(overrides)
    return UnifiedEvent(**payload)


def test_round_trip_and_deep_immutability():
    original = event()
    restored = UnifiedEvent.from_json(original.to_json())
    assert restored == original
    with pytest.raises(FrozenInstanceError):
        original.event_name = "changed"
    with pytest.raises(TypeError):
        original.attributes["new"] = True
    with pytest.raises(TypeError):
        original.attributes["nested"]["items"] = ()


@pytest.mark.parametrize("signal", list(SignalType))
def test_all_modalities_are_accepted(signal):
    extra = {}
    if signal is SignalType.TRACE:
        extra = {"trace_id": "a" * 32, "span_id": "b" * 16}
    elif signal is SignalType.METRIC:
        extra = {"metric_name": "http.duration", "metric_value": 12.4}
    elif signal is SignalType.BUSINESS_EVENT:
        extra = {"business_operation": "pay"}
    current = event(signal_type=signal, **extra)
    assert current.signal_type is signal


def test_modality_missing_fields_are_reported_not_rejected():
    current = event(signal_type="metric", business_operation=None)
    assert current.missing_fields == ("metric_name", "metric_value")
    assert "missing_modality_fields" in current.data_quality_flags


def test_incomplete_trace_and_metric_pass_input_schema():
    schema = load_schema("unified-event-v1.schema.json")
    incomplete_trace = event(
        signal_type="trace", business_operation=None
    )
    incomplete_metric = event(
        signal_type="metric", business_operation=None
    )
    assert incomplete_trace.missing_fields == ("trace_id", "span_id")
    assert incomplete_metric.missing_fields == ("metric_name", "metric_value")
    validate_with_available_validator(incomplete_trace.to_dict(), schema)
    validate_with_available_validator(incomplete_metric.to_dict(), schema)


def test_metric_values_and_trace_flags_are_validated():
    with pytest.raises(ValueError, match="finito"):
        event(signal_type="metric", metric_name="cpu", metric_value=float("nan"))
    with pytest.raises(ValueError, match="trace_flags"):
        event(trace_flags=256)


def test_legacy_ids_are_accepted_and_flagged():
    current = event(
        signal_type="trace", trace_id="public-trace-17", span_id="span-A"
    )
    assert "legacy_trace_id_format" in current.data_quality_flags
    assert "legacy_span_id_format" in current.data_quality_flags
    with pytest.raises(ValueError, match="caracteres"):
        event(event_id="invalid id")


def test_timestamps_are_utc_and_naive_is_rejected():
    offset = timezone(timedelta(hours=-3))
    current = event(event_timestamp=datetime(2026, 8, 3, 9, 0, tzinfo=offset))
    assert current.event_timestamp == UTC_TIME
    assert current.to_dict()["event_timestamp"].endswith("Z")
    with pytest.raises(ValueError, match="timezone UTC"):
        event(event_timestamp=datetime(2026, 8, 3, 12, 0))


def test_input_schema_accepts_timezone_offset_before_python_normalization():
    schema = load_schema("unified-event-v1.schema.json")
    raw = event().to_dict()
    raw["event_timestamp"] = "2026-08-03T09:00:00-03:00"
    validate_with_available_validator(raw, schema)
    normalized = UnifiedEvent.from_dict(raw)
    assert normalized.event_timestamp == UTC_TIME
    assert normalized.to_dict()["event_timestamp"] == "2026-08-03T12:00:00Z"


def test_json_is_deterministic_and_has_no_causal_parent_field():
    first = event(attributes={"z": 1, "a": 2}).to_json()
    second = event(attributes={"a": 2, "z": 1}).to_json()
    assert first == second
    assert "causal_parent_event_ids" not in json.loads(first)


def test_unknown_and_invalid_schema_are_rejected():
    payload = event().to_dict()
    payload["unknown"] = True
    with pytest.raises(ValueError, match="desconhecidos"):
        UnifiedEvent.from_dict(payload)
    with pytest.raises(ValueError, match="schema_version inválido"):
        event(schema_version="unified-event-v2")
    with pytest.raises(ValueError, match="JSON"):
        UnifiedEvent.from_json("{")
    with pytest.raises(ValueError, match="event_id"):
        event(event_id=None)


@pytest.mark.parametrize(
    "field,value",
    [
        ("attributes", {1: "numeric"}),
        ("attributes", {"nested": {1: "numeric"}}),
        ("resource_attributes", {1: "numeric"}),
        ("instrumentation_scope", {1: "numeric"}),
        ("span_links", ({"nested": {1: "numeric"}},)),
    ],
)
def test_non_string_json_keys_are_rejected_recursively(field, value):
    with pytest.raises(ValueError, match="chaves.*strings"):
        event(**{field: value})


def test_boolean_status_code_is_rejected():
    with pytest.raises(ValueError, match="não booleano"):
        event(status_code=True)


def test_span_v2_adapter_preserves_trace_contract():
    span = Span(
        trace_id="a" * 32,
        span_id="b" * 16,
        parent_span_id=None,
        service_name="payment-service",
        operation_name="POST /payments",
        start_time=1_754_224_000_000_000_000,
        end_time=1_754_224_000_100_000_000,
        duration_ns=100_000_000,
        status_code="ERROR",
    )
    observed = datetime.fromtimestamp(
        span.start_time / 1_000_000_000, tz=timezone.utc
    ) + timedelta(milliseconds=50)
    adapted = SpanToUnifiedEventAdapter().adapt(
        span, observed_timestamp=observed, ingested_timestamp=observed
    )
    assert adapted.signal_type is SignalType.TRACE
    assert adapted.trace_id == span.trace_id
    assert adapted.span_id == span.span_id
    assert adapted.service_name == span.service_name
    assert adapted.attributes["duration_ns"] == span.duration_ns
    assert adapted.late_arrival_ms == pytest.approx(50)


def test_span_adapter_rejects_non_span():
    with pytest.raises(ValueError, match="models.span.Span"):
        SpanToUnifiedEventAdapter().adapt({})


def test_derived_evidence_is_separate_and_round_trips():
    evidence = DerivedEvidence(
        evidence_id="evidence-1",
        relation_type="precedes",
        source_event_ids=("evt-1",),
        target_event_id="evt-2",
        inferred_at=UTC_TIME,
        source_component="event-graph-v1",
        confidence=0.9,
    )
    restored = DerivedEvidence.from_dict(evidence.to_dict())
    assert restored == evidence
    assert "source_event_ids" in evidence.to_dict()
    schema = load_schema("derived-evidence-v1.schema.json")
    validate_with_available_validator(evidence.to_dict(), schema)


def test_derived_evidence_validation():
    with pytest.raises(ValueError, match="não pode ser vazio"):
        DerivedEvidence(
            evidence_id="e", relation_type="precedes", source_event_ids=(),
            target_event_id="target", inferred_at=UTC_TIME,
            source_component="test",
        )
    with pytest.raises(ValueError, match="entre 0 e 1"):
        DerivedEvidence(
            evidence_id="e", relation_type="precedes", source_event_ids=("a",),
            target_event_id="target", inferred_at=UTC_TIME,
            source_component="test", confidence=2,
        )
    with pytest.raises(ValueError, match="target_event_id"):
        DerivedEvidence(
            evidence_id="e", relation_type="precedes", source_event_ids=("a",),
            target_event_id=None, inferred_at=UTC_TIME,
            source_component="test",
        )
    with pytest.raises(ValueError, match="chaves.*strings"):
        DerivedEvidence(
            evidence_id="e", relation_type="precedes", source_event_ids=("a",),
            target_event_id="target", inferred_at=UTC_TIME,
            source_component="test", attributes={"nested": {1: "invalid"}},
        )
    with pytest.raises(ValueError, match="itens únicos"):
        DerivedEvidence(
            evidence_id="e", relation_type="precedes",
            source_event_ids=("a", "a"), target_event_id="target",
            inferred_at=UTC_TIME, source_component="test",
        )


def test_versioned_json_schema_has_expected_shape():
    schema = load_schema("unified-event-v1.schema.json")
    assert schema["$schema"].endswith("2020-12/schema")
    assert schema["properties"]["schema_version"]["const"] == "unified-event-v1"
    assert "causal_parent_event_ids" not in schema["properties"]
    assert schema["additionalProperties"] is False
    assert "allOf" not in schema
    assert "pattern" not in schema["$defs"]["utcTimestamp"]


def test_derived_evidence_schema_has_exact_contract():
    schema = load_schema("derived-evidence-v1.schema.json")
    assert schema["$schema"].endswith("2020-12/schema")
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == "derived-evidence-v1"
    assert schema["properties"]["source_event_ids"]["minItems"] == 1
    assert schema["properties"]["source_event_ids"]["uniqueItems"] is True
    assert set(schema["required"]) == set(schema["properties"])


def test_all_valid_canonical_payloads_pass_their_schemas():
    unified_schema = load_schema("unified-event-v1.schema.json")
    evidence_schema = load_schema("derived-evidence-v1.schema.json")
    for signal, extra in (
        ("trace", {}),
        ("log", {"severity": "WARN"}),
        ("metric", {}),
        ("business_event", {"business_operation": "create_order"}),
    ):
        overrides = {
            "signal_type": signal,
            "business_operation": None,
            **extra,
        }
        validate_with_available_validator(
            event(**overrides).to_dict(),
            unified_schema,
        )
    evidence = DerivedEvidence(
        evidence_id="ev-1", relation_type="correlated_with",
        source_event_ids=("evt-1",), target_event_id="evt-2",
        inferred_at=UTC_TIME, source_component="test", confidence=None,
    )
    validate_with_available_validator(evidence.to_dict(), evidence_schema)


def test_schema_rejects_unknown_field_and_invalid_confidence():
    unified_schema = load_schema("unified-event-v1.schema.json")
    evidence_schema = load_schema("derived-evidence-v1.schema.json")
    unknown = event().to_dict()
    unknown["unknown"] = True
    with pytest.raises(Exception):
        validate_with_available_validator(unknown, unified_schema)
    invalid = DerivedEvidence(
        evidence_id="ev-1", relation_type="precedes",
        source_event_ids=("evt-1",), target_event_id="evt-2",
        inferred_at=UTC_TIME, source_component="test",
    ).to_dict()
    invalid["confidence"] = 1.1
    with pytest.raises(Exception):
        validate_with_available_validator(invalid, evidence_schema)
