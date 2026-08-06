from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from multimodal_v1.public_datasets_v3 import (
    AIOps2020Adapter, EvaluationProtocol, NORMALIZED_CACHE_VERSION,
    ProtocolPlanner, RCAEvalAdapter,
)


def write(path: Path, value: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return path


@pytest.fixture
def rcaeval_root(tmp_path):
    case = tmp_path / "RE2" / "RE2-OB" / "cartservice_delay_1"
    write(case / "inject_time.txt", "1705354566\n")
    write(case / "metrics.json", json.dumps({
        "cartservice_cpu": [[1705354565, 2.5], [1705354566, 8.0]],
        "frontend_latency-90": [[1705354566, .8]],
    }))
    write(case / "logs.csv",
          "time,timestamp,container_name,message,level,req_path,error,cluster_id,log_template,new_field\n"
          "21:24,1705354566000000000,cartservice,failed request,error,/cart,Boom,1,failed request,preserved\n")
    write(case / "traces.csv",
          "time,traceID,spanID,serviceName,methodName,operationName,startTimeMillis,startTime,duration,statusCode,parentSpanID\n"
          "21:24,46d5d1e1fc25a09db4a81d4d105cee3d,de79080386c4c5c3,cartservice,GetCart,cart/GetCart,1705354566000,1705354566000000,186,2,433c41ba31b519dd\n")
    return tmp_path


@pytest.fixture
def aiops_root(tmp_path):
    root = tmp_path / "aiops2020"
    write(root / "故障整理（预赛）.csv",
          "故障时间,故障类型,故障位置,批次\n1705354566,cpu,docker_006,one\n")
    write(root / "业务指标" / "aiops-esb.csv",
          "timestamp,serviceName,success_rate,request_count\n"
          "1705354566,osb_001,0.8,20\n")
    write(root / "平台指标" / "docker.csv",
          "itemid,name,timestamp,value,cmdb_id,unit\n"
          "42,cpu_usage,1705354566000,91.2,docker_006,percent\n")
    write(root / "调用链指标" / "trace.csv",
          "timestamp,traceid,id,pid,cmdb_id,operationName,duration,statusCode,peer\n"
          "1705354566000000,46d5d1e1fc25a09db4a81d4d105cee3d,de79080386c4c5c3,433c41ba31b519dd,docker_006,checkout,120,ERROR,docker_007\n"
          "1705354567000000,legacy-trace,legacy-span,legacy-parent,docker_007,pay,50,OK,docker_008\n")
    return root


def test_rcaeval_detects_real_documented_schema_and_normalizes_modalities(rcaeval_root):
    adapter = RCAEvalAdapter(rcaeval_root)
    incident = adapter.list_incidents()[0]
    events = adapter.normalize_events(incident.incident_id)
    report = adapter.validate()

    assert report.valid
    assert {event.signal_type.value for event in events} == {"metric", "log", "trace"}
    trace = next(event for event in events if event.signal_type.value == "trace")
    assert (trace.trace_id, trace.span_id, trace.parent_span_id) == (
        "46d5d1e1fc25a09db4a81d4d105cee3d", "de79080386c4c5c3",
        "433c41ba31b519dd")
    log = next(event for event in events if event.signal_type.value == "log")
    assert log.attributes["body"] == "failed request"
    logs_schema = next(item for item in report.schema_report.files if item.kind == "logs")
    assert logs_schema.unmapped_fields == ("new_field",)
    assert trace.attributes["duration_ms"] == pytest.approx(.186)
    assert trace.attributes["duration_raw_unit"] == "microsecond"


def test_rcaeval_manifest_preserves_case_fault_service_window_and_checksums(rcaeval_root):
    adapter = RCAEvalAdapter(rcaeval_root)
    incident = adapter.list_incidents()[0]
    manifest = adapter.load_incident(incident.incident_id)

    assert manifest.source_system == "RE2-OB"
    assert manifest.source_case.endswith("cartservice_delay_1")
    assert manifest.fault_service == "cartservice"
    assert manifest.fault_type == "delay"
    assert manifest.analysis_window.start < manifest.analysis_window.end
    for relative, checksum in manifest.source_files_sha256.items():
        assert hashlib.sha256((rcaeval_root / relative).read_bytes()).hexdigest() == checksum


def test_rcaeval_supports_official_nested_service_fault_instance_layout(tmp_path):
    case = tmp_path / "RE1" / "RE1-TT" / "ts-order-service_cpu" / "2"
    write(case / "inject_time.txt", "1705354566")
    write(case / "metrics.json", json.dumps({"ts-order-service_cpu": [[1705354566, 1]]}))
    incident = RCAEvalAdapter(tmp_path).list_incidents()[0]
    assert incident.fault_service == "ts-order-service"
    assert incident.fault_type == "cpu"


def test_rcaeval_reads_official_parquet_layout(tmp_path):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    case = tmp_path / "RE1" / "RE1-OB" / "cartservice_cpu" / "1"
    case.mkdir(parents=True)
    write(case / "inject_time.txt", "1705354566")
    pd.DataFrame({"time": [1705354566], "cartservice_cpu": [7.5]}).to_parquet(
        case / "metrics.parquet", index=False)
    adapter = RCAEvalAdapter(tmp_path)
    incident = adapter.list_incidents()[0]
    events = adapter.normalize_events(incident.incident_id)
    assert len(events) == 1
    assert events[0].metric_name == "cartservice_cpu"
    assert events[0].metric_value == 7.5


def test_rcaeval_cache_is_local_and_checksum_keyed(rcaeval_root, tmp_path):
    cache = tmp_path / "cache"
    adapter = RCAEvalAdapter(rcaeval_root, cache_dir=cache)
    incident = adapter.list_incidents()[0]
    first = adapter.normalize_events(incident.incident_id)
    assert len(tuple(cache.glob("*.json"))) == 1
    second = adapter.normalize_events(incident.incident_id)
    assert [event.to_dict() for event in first] == [event.to_dict() for event in second]


def test_rcaeval_invalid_ids_are_preserved_and_flagged(rcaeval_root):
    trace_file = next(rcaeval_root.rglob("traces.csv"))
    write(trace_file,
          "traceID,spanID,parentSpanID,serviceName,startTime,duration,operationName\n"
          "legacy-trace,legacy-span,legacy-parent,cartservice,1705354566000000,10,op\n")
    adapter = RCAEvalAdapter(rcaeval_root)
    event = next(item for item in adapter.normalize_events(adapter.list_incidents()[0].incident_id)
                 if item.signal_type.value == "trace")
    assert event.trace_id is event.span_id is event.parent_span_id is None
    assert event.attributes["rcaeval.raw_trace_id"] == "legacy-trace"
    assert {"trace_id", "span_id"} <= set(event.missing_fields)
    assert "legacy_or_invalid_trace_id" in event.data_quality_flags


def test_aiops2020_maps_fault_business_platform_and_call_chain(aiops_root):
    adapter = AIOps2020Adapter(aiops_root)
    incident = adapter.list_incidents()[0]
    events = adapter.normalize_events(incident.incident_id)
    truth = adapter.get_ground_truth(incident.incident_id)

    assert adapter.validate().valid
    assert truth["fault_location"] == "docker_006"
    assert truth["fault_type"] == "cpu"
    assert sum(event.signal_type.value == "metric" for event in events) == 3
    assert sum(event.signal_type.value == "trace" for event in events) == 2
    trace = next(event for event in events if event.trace_id)
    assert trace.span_id == "de79080386c4c5c3"
    legacy = next(event for event in events
                  if event.attributes.get("aiops2020.raw_span_id") == "legacy-span")
    assert legacy.span_id is None and "span_id" in legacy.missing_fields
    assert "unknown_trace_duration_unit" in legacy.data_quality_flags
    assert "duration_ms" not in legacy.attributes


def test_aiops2020_schema_report_preserves_unmapped_fields(aiops_root):
    report = AIOps2020Adapter(aiops_root).validate().schema_report
    faults = next(item for item in report.files if item.kind == "faults")
    traces = next(item for item in report.files if item.kind == "traces")
    assert faults.unmapped_fields == ("批次",)
    assert traces.unmapped_fields == ("peer",)
    assert all(len(item.checksum_sha256) == 64 for item in report.files)


def test_invalid_source_schema_is_reported_not_guessed(tmp_path):
    write(tmp_path / "fault.csv", "when,where,what\n1,a,b\n")
    report = AIOps2020Adapter(tmp_path).validate()
    assert not report.valid
    assert all(any(semantic in error for error in report.errors)
               for semantic in ("time", "location", "type"))


def test_configured_checksum_mismatch_fails_validation(aiops_root):
    adapter = AIOps2020Adapter(
        aiops_root, expected_checksums={"故障整理（预赛）.csv": "0" * 64})
    report = adapter.validate()
    assert not report.valid
    assert report.errors[-1] == "Checksum SHA-256 divergente: 故障整理（预赛）.csv"


class Publisher:
    def __init__(self): self.records = []
    def send(self, topic, value, *, key, headers):
        self.records.append((topic, json.loads(value), key, headers))


@pytest.mark.parametrize("adapter_fixture", ("rcaeval_root", "aiops_root"))
def test_replay_to_kafka_is_optional_and_uses_discriminated_topics(request, adapter_fixture):
    root = request.getfixturevalue(adapter_fixture)
    adapter = RCAEvalAdapter(root) if adapter_fixture == "rcaeval_root" else AIOps2020Adapter(root)
    incident = adapter.list_incidents()[0]
    publisher = Publisher()
    result = adapter.replay_to_kafka(incident.incident_id, publisher)
    assert result.attempted == result.confirmed == len(publisher.records) == \
        len(adapter.normalize_events(incident.incident_id))
    assert result.failed == 0
    assert all(record[1]["schema_version"] == "unified-event-v1" for record in publisher.records)


def test_evaluation_protocols_are_incident_grouped_and_deterministic():
    from multimodal_v1.public_datasets_v3 import IncidentDescriptor
    incidents = tuple(IncidentDescriptor(f"i{index}", source, system, f"case-{index}", "/tmp")
                      for index, (source, system) in enumerate((
                          ("rcaeval", "OB"), ("rcaeval", "OB"), ("rcaeval", "TT"),
                          ("aiops", "AIOps2020"), ("aiops", "AIOps2020"))))
    within = ProtocolPlanner.plan(incidents, EvaluationProtocol.WITHIN_DATASET, seed=7)
    assert within == ProtocolPlanner.plan(incidents, "within-dataset", seed=7)
    assert set(within.train_incident_ids).isdisjoint(within.test_incident_ids)
    cross_system = ProtocolPlanner.plan(incidents, "cross-system", held_out_system="TT")
    assert cross_system.test_incident_ids == ("i2",)
    loso = ProtocolPlanner.plan_all_folds(incidents)
    assert len(loso.folds) == 3
    assert {item for fold in loso.folds for item in fold.test_incident_ids} == \
        {item.incident_id for item in incidents}
    cross_source = ProtocolPlanner.plan(incidents, "cross-source", held_out_source="aiops")
    assert cross_source.test_incident_ids == ("i3", "i4")


def test_cache_identity_covers_window_aliases_sources_but_not_cache_directory(aiops_root, tmp_path):
    incident_id = AIOps2020Adapter(aiops_root).list_incidents()[0].incident_id
    broad = AIOps2020Adapter(aiops_root, cache_dir=tmp_path / "a")
    same = AIOps2020Adapter(aiops_root, cache_dir=tmp_path / "b")
    narrow = AIOps2020Adapter(aiops_root, cache_dir=tmp_path / "a",
                              pre_fault=__import__("datetime").timedelta(seconds=1))
    aliases = AIOps2020Adapter(aiops_root, cache_dir=tmp_path / "a",
                               column_aliases={"trace": {"operation": ("custom",)}})
    assert broad._cache_key(incident_id) == same._cache_key(incident_id)
    assert broad._cache_key(incident_id) != narrow._cache_key(incident_id)
    assert broad._cache_key(incident_id) != aliases._cache_key(incident_id)
    before = broad._cache_key(incident_id)
    path = aiops_root / "平台指标" / "docker.csv"
    path.write_text(path.read_text() + "43,cpu_usage,1705354566000,1,docker_006,percent\n")
    assert before != broad._cache_key(incident_id)


def test_cache_envelope_is_verified_and_reused_deterministically(aiops_root, tmp_path):
    cache = tmp_path / "cache"
    adapter = AIOps2020Adapter(aiops_root, cache_dir=cache)
    incident_id = adapter.list_incidents()[0].incident_id
    first = adapter.normalize_events(incident_id)
    target = next(cache.glob("*.json"))
    envelope = json.loads(target.read_text())
    assert envelope["cache_version"] == NORMALIZED_CACHE_VERSION
    assert envelope["event_count"] == len(first)
    assert adapter.normalize_events(incident_id) == first
    envelope["events"][0]["event_name"] = "tampered"
    target.write_text(json.dumps(envelope))
    with pytest.raises(RuntimeError, match="events_sha256"):
        adapter.normalize_events(incident_id)


def test_old_cache_version_is_rejected(aiops_root, tmp_path):
    adapter = AIOps2020Adapter(aiops_root, cache_dir=tmp_path)
    incident_id = adapter.list_incidents()[0].incident_id
    adapter.normalize_events(incident_id)
    target = next(tmp_path.glob("*.json"))
    envelope = json.loads(target.read_text()); envelope["cache_version"] = "old"
    target.write_text(json.dumps(envelope))
    with pytest.raises(RuntimeError, match="cache_version"):
        adapter.normalize_events(incident_id)


def test_aiops_semantic_validation_rejects_uninterpreted_or_malformed_files(tmp_path):
    write(tmp_path / "fault.csv", "timestamp,fault_type,fault_location\n1705354566,cpu,x\n")
    write(tmp_path / "平台指标" / "foo.csv", "timestamp,cmdb_id,foo,bar\n1705354566,x,1,2\n")
    write(tmp_path / "调用链指标" / "trace.csv", "timestamp,serviceName\n1705354566,x\n")
    report = AIOps2020Adapter(tmp_path).validate()
    assert not report.valid
    assert any("nenhum campo métrico" in error for error in report.errors)
    trace = next(item for item in report.schema_report.files if item.kind == "traces")
    assert trace.rejected_row_count == 1
    assert trace.rejection_reasons["missing_traceid"] == 1
    metric = next(item for item in report.schema_report.files if item.kind == "platform_metrics")
    assert set(metric.classification.rejected_fields) == {"bar", "foo"}


@pytest.mark.parametrize("content,reason", (
    ("name,value,cmdb_id\ncpu,1,x\n", "missing_timestamp"),
    ("timestamp,name,value,cmdb_id\n1705354566,cpu,not-a-number,x\n", "invalid_metric_value"),
    ("timestamp,name,value\n1705354566,cpu,1\n", "missing_service"),
))
def test_long_metric_semantic_requirements_are_enforced(tmp_path, content, reason):
    write(tmp_path / "fault.csv", "timestamp,fault_type,fault_location\n1705354566,cpu,x\n")
    write(tmp_path / "平台指标" / "platform.csv", content)
    report = AIOps2020Adapter(tmp_path).validate()
    item = next(entry for entry in report.schema_report.files if entry.kind == "platform_metrics")
    assert not report.valid
    assert item.rejected_row_count == 1
    assert item.rejection_reasons[reason] == 1


def test_wide_explicit_metric_must_have_numeric_value(tmp_path):
    write(tmp_path / "fault.csv", "timestamp,fault_type,fault_location\n1705354566,cpu,x\n")
    write(tmp_path / "业务指标" / "business.csv",
          "timestamp,serviceName,success_rate\n1705354566,x,not-numeric\n")
    report = AIOps2020Adapter(tmp_path).validate()
    item = next(entry for entry in report.schema_report.files if entry.kind == "business_metrics")
    assert not report.valid
    assert item.rejection_reasons["no_numeric_interpreted_metric"] == 1


def test_wide_unknown_numeric_label_requires_explicit_policy(tmp_path):
    write(tmp_path / "fault.csv", "timestamp,fault_type,fault_location\n1705354566,cpu,x\n")
    write(tmp_path / "业务指标" / "business.csv",
          "timestamp,serviceName,unknown_label\n1705354566,x,9\n")
    rejected = AIOps2020Adapter(tmp_path).validate()
    assert not rejected.valid
    accepted = AIOps2020Adapter(
        tmp_path, wide_metric_fields={"business.csv": ("unknown_label",)}).validate()
    assert accepted.valid


def test_adapter_trace_latency_reaches_assembler_graph_and_features(rcaeval_root, aiops_root):
    from multimodal_v1.evidence import IncidentEvidenceAssembler
    from multimodal_v1.event_graph import EventGraphBuilder
    from multimodal_v1.features_v3 import RCAFeatureExtractorV3

    adapters = ((RCAEvalAdapter(rcaeval_root), .186),
                (AIOps2020Adapter(aiops_root, duration_units={"duration": "ms"}), 120.0))
    for adapter, expected in adapters:
        incident_id = adapter.list_incidents()[0].incident_id
        trace = next(event for event in adapter.normalize_events(incident_id)
                     if event.signal_type.value == "trace" and event.span_id)
        assembler = IncidentEvidenceAssembler(); decision = assembler.ingest(trace)
        bundle = next(item for item in assembler.active_bundles()
                      if item.incident_id == decision.incident_id)
        graph = EventGraphBuilder().build(bundle)
        vector = RCAFeatureExtractorV3().extract(bundle, graph)[0]
        assert vector.values["latency_mean"] == pytest.approx(expected)
        assert vector.values["latency_p95"] == pytest.approx(expected)


def test_loso_folds_validate_coverage_duplicates_and_single_system():
    from multimodal_v1.public_datasets_v3 import IncidentDescriptor
    values = tuple(IncidentDescriptor(f"id-{n}", "source", system, str(n), "/tmp")
                   for n, system in enumerate(("a", "a", "b", "c")))
    first = ProtocolPlanner.plan_all_folds(values)
    assert first == ProtocolPlanner.plan_all_folds(values)
    assert len(first.folds) == 3
    assert sorted(item for fold in first.folds for item in fold.test_incident_ids) == \
        sorted(item.incident_id for item in values)
    assert all(set(fold.train_incident_ids).isdisjoint(fold.test_incident_ids)
               for fold in first.folds)
    with pytest.raises(ValueError, match="duplicado"):
        ProtocolPlanner.plan_all_folds(values + (values[0],))
    with pytest.raises(ValueError, match="dois sistemas"):
        ProtocolPlanner.plan_all_folds(values[:2])
    with pytest.raises(ValueError, match="ausente"):
        ProtocolPlanner.plan(values, "cross-system", held_out_system="missing")
    with pytest.raises(ValueError, match="held_out_system"):
        ProtocolPlanner.plan(values, "cross-system")
    manifest = ProtocolPlanner.manifest(values, "leave-one-system-out")
    assert manifest.fingerprint == ProtocolPlanner.manifest(
        values, "leave-one-system-out").fingerprint
    assert manifest.version == "public-dataset-protocol-v2"


@pytest.mark.parametrize("raw,unit,expected_flag", (
    ("-1", "ms", "negative_trace_duration"),
    ("nan", "ms", "invalid_trace_duration"),
    ("inf", "ms", "invalid_trace_duration"),
    ("1", None, "unknown_trace_duration_unit"),
))
def test_duration_policy_rejects_or_flags_unusable_values(raw, unit, expected_flag):
    from multimodal_v1.public_datasets_v3.common import duration_attributes
    factors = {"ms": 1.0}
    attributes, flags = duration_attributes(
        raw, column="elapsedTime", unit=unit, factor_to_ms=factors.get(unit))
    assert "duration_ms" not in attributes
    assert expected_flag in flags


def test_manifest_has_auditable_hashes_window_and_counts(rcaeval_root):
    adapter = RCAEvalAdapter(rcaeval_root)
    manifest = adapter.load_incident(adapter.list_incidents()[0].incident_id)
    assert len(manifest.fingerprint) == 64
    assert manifest.adapter_version == "rcaeval-adapter-v3"
    assert manifest.window_policy == "source-prewindowed-v1"
    assert manifest.observed_interval is not None
    assert sum(manifest.event_count_by_modality.values()) == manifest.total_event_count
    assert all(len(value) == 64 for value in (
        manifest.adapter_configuration_sha256, manifest.cache_key,
        manifest.normalized_events_sha256, manifest.ground_truth_sha256,
        manifest.schema_report_sha256))


class FailedAck:
    def get(self): raise RuntimeError("broker rejected")


class FailingPublisher:
    def send(self, *args, **kwargs): return FailedAck()


def test_replay_acknowledgement_failure_policy(rcaeval_root):
    adapter = RCAEvalAdapter(rcaeval_root)
    incident_id = adapter.list_incidents()[0].incident_id
    result = adapter.replay_to_kafka(
        incident_id, FailingPublisher(), failure_policy="continue")
    assert result.attempted == result.failed > 0 and result.confirmed == 0
    with pytest.raises(RuntimeError, match="fail-fast"):
        adapter.replay_to_kafka(incident_id, FailingPublisher())


def test_rcaeval_without_inject_time_uses_raw_interval_without_recursion(tmp_path):
    case = tmp_path / "RE1" / "RE1-OB" / "cartservice_cpu" / "1"
    write(case / "metrics.json", json.dumps({
        "cartservice_cpu": [[1705354500, 1], [1705354560, 2]]}))
    adapter = RCAEvalAdapter(tmp_path, cache_dir=tmp_path / "cache")
    incident = adapter.list_incidents()[0]
    first_key = adapter._cache_key(incident)
    events = adapter.normalize_events(incident.incident_id)
    window = adapter.get_analysis_window(incident.incident_id)
    assert len(events) == 2 and first_key == adapter._cache_key(incident)
    assert window.start.timestamp() == 1705354500
    assert window.end.timestamp() == 1705354560
    assert adapter.load_incident(incident.incident_id).window_policy == "raw-observed-interval-v1"


def test_rcaeval_without_any_valid_timestamp_fails_clearly(tmp_path):
    case = tmp_path / "RE1" / "RE1-OB" / "cartservice_cpu" / "1"
    write(case / "metrics.json", json.dumps({"cartservice_cpu": [["bad", 1]]}))
    adapter = RCAEvalAdapter(tmp_path)
    with pytest.raises(ValueError, match="nenhum timestamp fonte válido"):
        adapter.get_analysis_window(adapter.list_incidents()[0].incident_id)


@pytest.mark.parametrize("target,replacement,reason", (
    ("metrics.json", {"cartservice_cpu": [["bad", 1]]}, "invalid_metric_timestamp"),
    ("metrics.json", {"cartservice_cpu": [[1705354566, "bad"]]}, "invalid_metric_value"),
))
def test_rcaeval_metric_json_semantics_match_validation(rcaeval_root, target, replacement, reason):
    path = next(rcaeval_root.rglob(target)); path.write_text(json.dumps(replacement))
    report = RCAEvalAdapter(rcaeval_root).validate()
    metric = next(item for item in report.schema_report.files if item.kind == "metrics")
    assert not report.valid and metric.rejected_row_count == 1
    assert metric.rejection_reasons[reason] == 1


@pytest.mark.parametrize("kind,content,reason", (
    ("logs.csv", "timestamp,container_name,message\nbad,cart,x\n", "invalid_log_timestamp"),
    ("traces.csv", "startTime,serviceName,traceID,spanID\nbad,cart,a,b\n", "invalid_trace_start_time"),
))
def test_rcaeval_log_and_trace_invalid_timestamps_are_reported(rcaeval_root, kind, content, reason):
    write(next(rcaeval_root.rglob(kind)), content)
    report = RCAEvalAdapter(rcaeval_root).validate()
    item = next(entry for entry in report.schema_report.files
                if entry.kind == ("logs" if kind.startswith("logs") else "traces"))
    assert not report.valid and item.rejection_reasons[reason] == 1


def test_rcaeval_wide_row_event_count_is_real_point_count(tmp_path):
    case = tmp_path / "RE1" / "RE1-OB" / "cartservice_cpu" / "1"
    write(case / "inject_time.txt", "1705354566")
    write(case / "metrics.csv", "time,a_cpu,b_cpu,c_cpu\n1705354566,1,2,3\n")
    report = RCAEvalAdapter(tmp_path).validate()
    metric = report.schema_report.files[0]
    assert report.valid and metric.row_count == 1 and metric.event_count == 3
    assert len(RCAEvalAdapter(tmp_path).normalize_events(
        RCAEvalAdapter(tmp_path).list_incidents()[0].incident_id)) == 3


def test_rcaeval_parquet_uses_same_cell_validation(tmp_path):
    pd = pytest.importorskip("pandas"); pytest.importorskip("pyarrow")
    case = tmp_path / "RE1" / "RE1-OB" / "cartservice_cpu" / "1"; case.mkdir(parents=True)
    write(case / "inject_time.txt", "1705354566")
    pd.DataFrame({"time": [1705354566], "cartservice_cpu": ["bad"]}).to_parquet(
        case / "metrics.parquet", index=False)
    report = RCAEvalAdapter(tmp_path).validate(); metric = report.schema_report.files[0]
    assert not report.valid and metric.rejected_row_count == 1
    assert metric.rejection_reasons["invalid_metric_cell:cartservice_cpu"] == 1


def test_rcaeval_manifest_counts_only_current_case(tmp_path):
    def case(name, points):
        root = tmp_path / "RE1" / "RE1-OB" / name / "1"
        write(root / "inject_time.txt", "1705354566")
        write(root / "metrics.json", json.dumps({f"{name}_cpu": points}))
    case("service-a_cpu", [[1705354566, 1]])
    adapter = RCAEvalAdapter(tmp_path); first = adapter.list_incidents()[0]
    before = adapter.load_incident(first.incident_id)
    case("service-b_cpu", [[1705354566, 1], [1705354567, 2], [1705354568, 3]])
    after = RCAEvalAdapter(tmp_path).load_incident(first.incident_id)
    second = RCAEvalAdapter(tmp_path).load_incident(RCAEvalAdapter(tmp_path).list_incidents()[1].incident_id)
    assert before.total_event_count == after.total_event_count == before.considered_row_count == 1
    assert second.total_event_count == second.considered_row_count == 3
    assert before.accepted_row_count == after.accepted_row_count
    assert before.incident_normalization_report_sha256 == after.incident_normalization_report_sha256
    assert before.dataset_schema_report_sha256 != after.dataset_schema_report_sha256


def test_aiops_manifests_have_incident_specific_window_counts(aiops_root):
    fault = aiops_root / "故障整理（预赛）.csv"
    write(fault, "故障时间,故障类型,故障位置\n1705354566,cpu,docker_006\n1705358166,mem,docker_007\n")
    adapter = AIOps2020Adapter(aiops_root, strict=False)
    first, second = adapter.list_incidents()
    one, two = adapter.load_incident(first.incident_id), adapter.load_incident(second.incident_id)
    assert one.total_event_count > 0 and two.total_event_count == 0
    assert one.accepted_row_count > 0 and two.accepted_row_count == 0
    assert two.excluded_by_window_count > 0
    assert one.considered_row_count == one.accepted_row_count + one.rejected_row_count + one.excluded_by_window_count
    assert two.considered_row_count == two.accepted_row_count + two.rejected_row_count + two.excluded_by_window_count


def test_aiops_strict_and_permissive_without_telemetry(tmp_path):
    write(tmp_path / "fault.csv", "timestamp,fault_type,fault_location\n1705354566,cpu,x\n")
    strict = AIOps2020Adapter(tmp_path).validate()
    permissive = AIOps2020Adapter(tmp_path, strict=False).validate()
    assert not strict.valid and any("Nenhum CSV" in item for item in strict.errors)
    assert permissive.valid and any("Nenhum CSV" in item for item in permissive.warnings)
    with pytest.raises(ValueError, match="zero eventos"):
        AIOps2020Adapter(tmp_path).load_incident("aiops2020:0")


def test_protocol_manifest_rejects_false_semantics_and_loso_seed_is_irrelevant():
    from multimodal_v1.public_datasets_v3 import (
        IncidentDescriptor, ProtocolPlanManifest, ProtocolSplit)
    values = (IncidentDescriptor("a", "src1", "sys1", "a", "/tmp"),
              IncidentDescriptor("b", "src2", "sys2", "b", "/tmp"))
    left = ProtocolPlanner.plan_all_folds(values, seed=1)
    right = ProtocolPlanner.plan_all_folds(values, seed=999)
    assert left.fingerprint == right.fingerprint
    index = {"a": {"system": "sys1", "source": "src1"},
             "b": {"system": "sys2", "source": "src2"}}
    wrong_protocol = ProtocolSplit("cross-source", ("a",), ("b",), "src2")
    with pytest.raises(ValueError, match="Protocolo do fold"):
        ProtocolPlanManifest("cross-system", ("a", "b"), ("sys1", "sys2"),
                             ("src1", "src2"), (wrong_protocol,), index)
    wrong_holdout = ProtocolSplit("cross-system", ("a",), ("b",), "sys1")
    with pytest.raises(ValueError, match="held_out"):
        ProtocolPlanManifest("cross-system", ("a", "b"), ("sys1", "sys2"),
                             ("src1", "src2"), (wrong_holdout,), index)


def test_cache_rejects_non_sha_and_path_traversal(tmp_path):
    from multimodal_v1.public_datasets_v3 import NormalizedEventCache
    cache = NormalizedEventCache(tmp_path)
    for key in ("../escape", "a" * 63, "g" * 64):
        with pytest.raises(ValueError, match="SHA-256"):
            cache.target(key)


def test_replay_result_sanitizes_publisher_errors(rcaeval_root):
    class SecretAck:
        def get(self): raise RuntimeError("Authorization: Bearer secret-token")
    class SecretPublisher:
        def send(self, *args, **kwargs): return SecretAck()
    adapter = RCAEvalAdapter(rcaeval_root)
    result = adapter.replay_to_kafka(
        adapter.list_incidents()[0].incident_id, SecretPublisher(), failure_policy="continue")
    assert result.failed == len(result.errors)
    assert all("secret-token" not in error for error in result.errors)


def test_replay_result_contract_rejects_inconsistent_or_boolean_counts():
    from multimodal_v1.public_datasets_v3 import ReplayResult
    with pytest.raises(ValueError): ReplayResult(True, 1, 0, ())
    with pytest.raises(ValueError): ReplayResult(2, 1, 0, ())


@pytest.mark.parametrize("filename,content", (
    ("metrics.csv", "time,cartservice_cpu\n"),
    ("metrics.json", "{}"),
))
def test_rcaeval_empty_required_metrics_fail_all_strict_entrypoints(tmp_path, filename, content):
    case = tmp_path / "RE1" / "RE1-OB" / "cartservice_cpu" / "1"
    write(case / "inject_time.txt", "1705354566")
    write(case / filename, content)
    cache = tmp_path / "cache"
    adapter = RCAEvalAdapter(tmp_path, cache_dir=cache)
    incident_id = adapter.list_incidents()[0].incident_id
    assert not adapter.validate().valid
    with pytest.raises(ValueError, match="zero (eventos|métricas)"):
        adapter.normalize_events(incident_id)
    with pytest.raises(ValueError, match="zero (eventos|métricas)"):
        adapter.load_incident(incident_id)
    assert not cache.exists() or not tuple(cache.glob("*.json"))


def test_rcaeval_empty_parquet_fails_strict(tmp_path):
    pd = pytest.importorskip("pandas"); pytest.importorskip("pyarrow")
    case = tmp_path / "RE1" / "RE1-OB" / "cartservice_cpu" / "1"
    case.mkdir(parents=True); write(case / "inject_time.txt", "1705354566")
    pd.DataFrame(columns=("time", "cartservice_cpu")).to_parquet(
        case / "metrics.parquet", index=False)
    adapter = RCAEvalAdapter(tmp_path)
    assert not adapter.validate().valid
    with pytest.raises(ValueError, match="zero eventos"):
        adapter.normalize_events(adapter.list_incidents()[0].incident_id)


def test_rcaeval_permissive_empty_manifest_is_explicit_and_not_usable(tmp_path):
    case = tmp_path / "RE1" / "RE1-OB" / "cartservice_cpu" / "1"
    write(case / "inject_time.txt", "1705354566"); write(case / "metrics.json", "{}")
    adapter = RCAEvalAdapter(tmp_path, strict=False)
    report = adapter.validate(); incident_id = adapter.list_incidents()[0].incident_id
    assert report.valid and report.warnings and adapter.normalize_events(incident_id) == ()
    manifest = adapter.load_incident(incident_id)
    assert manifest.normalization_mode == "permissive"
    assert manifest.empty_incident_reason and not manifest.usable_for_benchmark


def test_rcaeval_partial_wide_row_has_cell_and_row_accounting(tmp_path):
    case = tmp_path / "RE1" / "RE1-OB" / "cartservice_cpu" / "1"
    write(case / "inject_time.txt", "1705354566")
    write(case / "metrics.csv", "time,cart_cpu,payment_cpu\n1705354566,1,bad\n")
    permissive = RCAEvalAdapter(tmp_path, strict=False)
    report = permissive.validate(); item = report.schema_report.files[0]
    assert len(permissive.normalize_events(permissive.list_incidents()[0].incident_id)) == 1
    assert item.wide_row_policy == "partial-wide-row-v1"
    assert item.rows_partially_accepted == item.accepted_row_count == 1
    assert item.rejected_row_count == 0
    assert item.accepted_metric_cell_count == item.rejected_metric_cell_count == 1
    with pytest.raises(ValueError, match="rejeições detectadas"):
        RCAEvalAdapter(tmp_path).normalize_events(
            RCAEvalAdapter(tmp_path).list_incidents()[0].incident_id)


def test_protocol_contracts_defensively_copy_and_match_fold_parameters():
    from multimodal_v1.public_datasets_v3 import ProtocolPlanManifest, ProtocolSplit
    train, test = ["a"], ["b"]
    split = ProtocolSplit("within-dataset", train, test, seed=7, test_fraction=.5)
    train.append("b"); test.append("a")
    assert split.train_incident_ids == ("a",) and split.test_incident_ids == ("b",)
    index = {"a": {"system": "s1", "source": "x"},
             "b": {"system": "s2", "source": "y"}}
    manifest = ProtocolPlanManifest("within-dataset", ["b", "a"], ["s2", "s1"],
                                    ["y", "x"], [split], index, 7, .5)
    index["a"]["system"] = "mutated"
    assert manifest.incident_index["a"]["system"] == "s1"
    same = ProtocolPlanManifest("within-dataset", ["a", "b"], ["s1", "s2"],
                                ["x", "y"], [split],
                                {"b": {"source": "y", "system": "s2"},
                                 "a": {"source": "x", "system": "s1"}}, 7, .5)
    assert manifest.fingerprint == same.fingerprint
    with pytest.raises(ValueError, match="divergem do fold"):
        ProtocolPlanManifest("within-dataset", ("a", "b"), ("s1", "s2"),
                             ("x", "y"), (split,), same.incident_index, 8, .5)
    with pytest.raises(ValueError, match="divergem do fold"):
        ProtocolPlanManifest("within-dataset", ("a", "b"), ("s1", "s2"),
                             ("x", "y"), (split,), same.incident_index, 7, .25)


def test_holdout_protocols_reject_unused_parameters():
    from multimodal_v1.public_datasets_v3 import ProtocolSplit, ReplayResult
    with pytest.raises(ValueError, match="não usam"):
        ProtocolSplit("cross-system", ("a",), ("b",), "s2", seed=1)
    with pytest.raises(ValueError, match="não usam"):
        ProtocolSplit("leave-one-system-out", ("a",), ("b",), "s2", test_fraction=.2)
    with pytest.raises(ValueError): ReplayResult(1, 0, 1, ())
    assert ReplayResult(1, 0, 1, ["failure"]).errors == ("failure",)


def test_aiops_long_partial_loss_is_enforced_before_cache_and_manifested(tmp_path):
    write(tmp_path / "fault.csv", "timestamp,fault_type,fault_location\n1705354566,cpu,x\n")
    write(tmp_path / "平台指标" / "platform.csv",
          "timestamp,name,value,cmdb_id\n1705354566,cpu,1,x\n1705354567,cpu,bad,x\n")
    cache = tmp_path / "cache"; strict = AIOps2020Adapter(tmp_path, cache_dir=cache)
    incident_id = strict.list_incidents()[0].incident_id
    assert not strict.validate().valid
    with pytest.raises(ValueError, match="linhas rejeitadas"):
        strict.normalize_events(incident_id)
    with pytest.raises(ValueError, match="linhas rejeitadas"):
        strict.load_incident(incident_id)
    assert not cache.exists() or not tuple(cache.glob("*.json"))
    permissive = AIOps2020Adapter(tmp_path, strict=False)
    assert len(permissive.normalize_events(incident_id)) == 1
    manifest = permissive.load_incident(incident_id)
    assert manifest.rejected_row_count == 1 and not manifest.usable_for_benchmark
    assert manifest.empty_incident_reason and manifest.normalization_mode == "permissive"


def test_aiops_partial_wide_row_accounts_cells(tmp_path):
    write(tmp_path / "fault.csv", "timestamp,fault_type,fault_location\n1705354566,cpu,x\n")
    write(tmp_path / "业务指标" / "business.csv",
          "timestamp,serviceName,success_rate,request_count\n1705354566,x,0.9,bad\n")
    strict = AIOps2020Adapter(tmp_path)
    incident_id = strict.list_incidents()[0].incident_id
    with pytest.raises(ValueError, match="células métricas rejeitadas"):
        strict.normalize_events(incident_id)
    permissive = AIOps2020Adapter(tmp_path, strict=False)
    assert len(permissive.normalize_events(incident_id)) == 1
    report = next(item for item in permissive.validate().schema_report.files
                  if item.kind == "business_metrics")
    assert report.wide_row_policy == "partial-wide-row-v1"
    assert report.rows_partially_accepted == report.accepted_row_count == 1
    assert report.accepted_metric_cell_count == report.rejected_metric_cell_count == 1
    assert not permissive.load_incident(incident_id).usable_for_benchmark


def test_aiops_trace_quality_flags_propagate_to_reports_and_manifest(tmp_path):
    write(tmp_path / "fault.csv", "timestamp,fault_type,fault_location\n1705354566,cpu,x\n")
    write(tmp_path / "平台指标" / "platform.csv",
          "timestamp,name,value,cmdb_id\n1705354566,cpu,1,x\n")
    write(tmp_path / "调用链指标" / "trace.csv",
          "timestamp,traceid,id,pid,cmdb_id,duration\n"
          "1705354566,legacy-trace,legacy-span,legacy-parent,x,5\n")
    adapter = AIOps2020Adapter(tmp_path)
    incident_id = adapter.list_incidents()[0].incident_id
    event = next(item for item in adapter.normalize_events(incident_id)
                 if item.signal_type.value == "trace")
    expected = {"unknown_trace_duration_unit", "missing_normalized_duration",
                "legacy_or_invalid_trace_id", "legacy_or_invalid_span_id",
                "legacy_or_invalid_parent_span_id"}
    assert expected.issubset(event.data_quality_flags)
    report = next(item for item in adapter.validate().schema_report.files if item.kind == "traces")
    assert expected.issubset(report.quality_issue_reasons)
    manifest = adapter.load_incident(incident_id)
    trace_quality = next(values for path, values in manifest.quality_issue_reasons_by_file.items()
                         if path.endswith("trace.csv"))
    assert expected.issubset(trace_quality)


def _rcaeval_case(root, suite, *, logs=False, traces=False):
    case = root / suite.split("-")[0] / suite / "cartservice_cpu" / "1"
    write(case / "inject_time.txt", "1705354566")
    write(case / "metrics.json", json.dumps({"cartservice_cpu": [[1705354566, 1]]}))
    if logs:
        write(case / "logs.csv", "timestamp,container_name,message\n1705354566,cart,x\n")
    if traces:
        write(case / "traces.csv",
              "startTime,serviceName,traceID,spanID\n1705354566,cart,46d5d1e1fc25a09db4a81d4d105cee3d,de79080386c4c5c3\n")
    return case


@pytest.mark.parametrize("suite,logs,traces,valid", (
    ("RE1-OB", False, False, True),
    ("RE2-OB", False, False, False),
    ("RE2-OB", True, False, False),
    ("RE2-SS", True, False, True),
    ("RE3-TT", True, True, True),
))
def test_rcaeval_official_modality_matrix(tmp_path, suite, logs, traces, valid):
    _rcaeval_case(tmp_path, suite, logs=logs, traces=traces)
    adapter = RCAEvalAdapter(tmp_path)
    report = adapter.validate(); incident_id = adapter.list_incidents()[0].incident_id
    assert report.valid is valid
    if valid:
        manifest = adapter.load_incident(incident_id)
        assert not manifest.missing_expected_modalities
        assert manifest.modality_policy_version == "rcaeval-suite-modality-policy-v1"
    else:
        with pytest.raises(ValueError, match="modalidade esperada ausente"):
            adapter.normalize_events(incident_id)


def test_rcaeval_unknown_suite_is_nonofficial_and_unusable(tmp_path):
    case = tmp_path / "CUSTOM" / "cartservice_cpu" / "1"
    write(case / "inject_time.txt", "1705354566")
    write(case / "metrics.json", json.dumps({"cartservice_cpu": [[1705354566, 1]]}))
    strict = RCAEvalAdapter(tmp_path)
    with pytest.raises(ValueError, match="sem política oficial"):
        strict.normalize_events(strict.list_incidents()[0].incident_id)
    permissive = RCAEvalAdapter(tmp_path, strict=False)
    manifest = permissive.load_incident(permissive.list_incidents()[0].incident_id)
    assert not manifest.usable_for_benchmark and manifest.expected_modalities == ()


def test_contract_wide_and_manifest_strict_invariants(rcaeval_root):
    from multimodal_v1.public_datasets_v3 import SchemaFileReport
    checksum = "0" * 64
    with pytest.raises(ValueError, match="row_count"):
        SchemaFileReport("x", "metrics", ("time", "cpu"), 1, checksum,
                         accepted_row_count=1, event_count=1,
                         wide_row_policy="partial-wide-row-v1",
                         rows_fully_accepted=0, accepted_metric_cell_count=1)
    with pytest.raises(ValueError, match="event_count"):
        SchemaFileReport("x", "metrics", ("time", "cpu"), 1, checksum,
                         accepted_row_count=1, event_count=1,
                         wide_row_policy="partial-wide-row-v1",
                         rows_fully_accepted=1, accepted_metric_cell_count=0)
    for invalid in (-1, True):
        with pytest.raises(ValueError, match="quality_issue_reasons"):
            SchemaFileReport("x", "traces", (), 0, checksum,
                             quality_issue_reasons={"bad": invalid})
    adapter = RCAEvalAdapter(rcaeval_root)
    manifest = adapter.load_incident(adapter.list_incidents()[0].incident_id)
    source = next(iter(manifest.source_files_sha256))
    with pytest.raises(ValueError, match="strict não pode conter rejeições"):
        replace(manifest, rejection_reasons_by_file={source: {"bad": 1}})
    with pytest.raises(ValueError, match="strict deve ser utilizável"):
        replace(manifest, usable_for_benchmark=False)


def test_incident_manifest_rejects_impossible_strict_states(rcaeval_root):
    adapter = RCAEvalAdapter(rcaeval_root)
    manifest = adapter.load_incident(adapter.list_incidents()[0].incident_id)
    with pytest.raises(ValueError):
        replace(manifest, rejected_row_count=1, considered_row_count=manifest.considered_row_count + 1,
                rejection_reasons_by_file={}, normalization_accounting_by_file={})
    with pytest.raises(ValueError, match="omitir modalidade"):
        replace(manifest, expected_modalities=manifest.expected_modalities + ("business_event",),
                missing_expected_modalities=("business_event",))
    with pytest.raises(ValueError, match="empty_incident_reason"):
        replace(manifest, empty_incident_reason="impossível em strict")


@pytest.mark.parametrize("field", (
    "expected_modalities", "observed_modalities",
    "missing_expected_modalities", "unexpected_modalities",
))
def test_incident_manifest_rejects_duplicate_modality_collections(rcaeval_root, field):
    adapter = RCAEvalAdapter(rcaeval_root)
    manifest = adapter.load_incident(adapter.list_incidents()[0].incident_id)
    value = getattr(manifest, field)
    duplicate = value + ((value[0] if value else "business_event"),) * 2
    with pytest.raises(ValueError, match="duplicatas"):
        replace(manifest, **{field: duplicate})


def test_schema_file_report_reconciles_cell_level_rejections():
    from multimodal_v1.public_datasets_v3 import SchemaClassification, SchemaFileReport
    base = dict(relative_path="wide.csv", kind="business_metrics",
                columns=("time", "success_rate", "request_count"), row_count=1,
                classification=SchemaClassification(identity_fields=("time",),
                    interpreted_metric_fields=("success_rate", "request_count")),
                checksum_sha256="0" * 64, accepted_row_count=1, event_count=1,
                wide_row_policy="partial-wide-row-v1", rows_partially_accepted=1,
                accepted_metric_cell_count=1)
    with pytest.raises(ValueError, match="cell-level"):
        SchemaFileReport(**base, rejected_metric_cell_count=1)
    with pytest.raises(ValueError, match="células aceitas e rejeitadas"):
        SchemaFileReport(**base, rejected_metric_cell_count=0)
    with pytest.raises(ValueError, match="cell-level"):
        SchemaFileReport(**base, rejected_metric_cell_count=1,
                         rejection_reasons={"invalid_metric_cell:success_rate": 1,
                                            "invalid_metric_cell:request_count": 1})
    valid = SchemaFileReport(**base, rejected_metric_cell_count=1,
                             rejection_reasons={"invalid_metric_cell:request_count": 1})
    assert valid.rows_partially_accepted == 1 and valid.rejected_metric_cell_count == 1


def _accounting_objects(manifest):
    from multimodal_v1.public_datasets_v3 import NormalizedFileAccounting
    return {path: NormalizedFileAccounting.from_dict(value)
            for path, value in manifest.normalization_accounting_by_file.items()}


def test_normalized_file_accounting_contract_and_round_trip():
    from multimodal_v1.public_datasets_v3 import NormalizedFileAccounting
    item = NormalizedFileAccounting("metrics.csv", "metrics", "metric", 2, 2, 0, 1, 0,
                                    3, 3, 1, 0, 1)
    assert NormalizedFileAccounting.from_dict(item.to_dict()) == item
    assert json.loads(json.dumps(item.to_dict())) == item.to_dict()
    with pytest.raises(ValueError, match="path traversal"):
        replace(item, relative_path="../metrics.csv")
    with pytest.raises(ValueError, match="partially_accepted"):
        replace(item, partially_accepted_rows=3)
    with pytest.raises(ValueError):
        replace(item, kind="logs", signal_type="log")
    with pytest.raises(ValueError, match="rejected_row_units"):
        replace(item, rejected_row_units=1)
    with pytest.raises(ValueError, match="rejected_cell_units"):
        replace(item, rejected_cell_units=0)


@pytest.mark.parametrize("field", (
    "total_rows", "accepted_rows", "rejected_rows", "partially_accepted_rows",
    "excluded_rows", "produced_events", "accepted_metric_cells",
    "rejected_metric_cells", "rejected_row_units", "rejected_cell_units",
))
@pytest.mark.parametrize("invalid", (True, -1))
def test_normalized_file_accounting_rejects_boolean_or_negative_counts(field, invalid):
    from multimodal_v1.public_datasets_v3 import NormalizedFileAccounting
    values = dict(relative_path="trace.csv", kind="traces", signal_type="trace",
                  total_rows=1, accepted_rows=1, rejected_rows=0,
                  partially_accepted_rows=0, excluded_rows=0, produced_events=1,
                  accepted_metric_cells=0, rejected_metric_cells=0,
                  rejected_row_units=0, rejected_cell_units=0)
    values[field] = invalid
    with pytest.raises(ValueError): NormalizedFileAccounting(**values)


def test_manifest_accounting_round_trip_and_fingerprint_stability(rcaeval_root, aiops_root):
    from multimodal_v1.public_datasets_v3 import IncidentManifest, NormalizedFileAccounting
    manifests = []
    for adapter in (RCAEvalAdapter(rcaeval_root),
                    AIOps2020Adapter(aiops_root, duration_units={"duration": "ms"})):
        manifest = adapter.load_incident(adapter.list_incidents()[0].incident_id)
        payload = manifest.to_dict(); fingerprint = payload.pop("fingerprint")
        rebuilt = IncidentManifest.from_dict(payload)
        assert rebuilt.to_dict() == manifest.to_dict()
        assert rebuilt.fingerprint == fingerprint == manifest.fingerprint
        assert json.loads(json.dumps(manifest.to_dict())) == manifest.to_dict()
        manifests.append(manifest)
    source = {path: dict(value) for path, value in manifests[0].normalization_accounting_by_file.items()}
    rebuilt_before = IncidentManifest.from_dict(manifests[0].to_dict())
    source[next(iter(source))]["accepted_rows"] = 999
    assert rebuilt_before.fingerprint == manifests[0].fingerprint
    first = next(iter(manifests[0].normalization_accounting_by_file.values()))
    changed = NormalizedFileAccounting.from_dict(first)
    assert changed.to_dict() == dict(first)


def test_manifest_rejects_missing_or_mismatched_normalized_file_accounting(rcaeval_root):
    manifest = RCAEvalAdapter(rcaeval_root).load_incident(
        RCAEvalAdapter(rcaeval_root).list_incidents()[0].incident_id)
    accounting = _accounting_objects(manifest); paths = manifest.normalized_source_files
    with pytest.raises(ValueError):
        replace(manifest, normalization_accounting_by_file={})
    with pytest.raises(ValueError, match="normalized_source_files"):
        replace(manifest, normalized_source_files=())
    missing = dict(accounting); missing.pop(paths[0])
    with pytest.raises(ValueError, match="exatamente"):
        replace(manifest, normalization_accounting_by_file=missing)
    with pytest.raises(ValueError, match="exatamente"):
        replace(manifest, normalization_accounting_by_file=accounting | {
            "extra.csv": replace(next(iter(accounting.values())), relative_path="extra.csv")})
    with pytest.raises(ValueError, match="source_files_sha256"):
        replace(manifest, normalized_source_files=paths + ("extra.csv",),
                normalization_accounting_by_file=accounting | {
                    "extra.csv": replace(next(iter(accounting.values())), relative_path="extra.csv")})
    with pytest.raises(ValueError, match="path traversal"):
        replace(next(iter(accounting.values())), relative_path="../escape.csv")


def test_manifest_reconciles_every_global_accounting_dimension(rcaeval_root):
    manifest = RCAEvalAdapter(rcaeval_root).load_incident(
        RCAEvalAdapter(rcaeval_root).list_incidents()[0].incident_id)
    accounting = _accounting_objects(manifest)
    path = next(path for path, value in accounting.items() if value.signal_type == "metric")
    item = accounting[path]
    mutations = (
        replace(item, accepted_rows=item.accepted_rows - 1,
                excluded_rows=item.excluded_rows + 1),
        replace(item, accepted_rows=item.accepted_rows - 1,
                rejected_rows=item.rejected_rows + 1, rejected_row_units=item.rejected_row_units + 1),
        replace(item, accepted_rows=item.accepted_rows - 1,
                excluded_rows=item.excluded_rows + 1),
        replace(item, produced_events=item.produced_events + 1,
                accepted_metric_cells=item.accepted_metric_cells + 1),
    )
    for changed in mutations:
        with pytest.raises(ValueError):
            replace(manifest, normalization_accounting_by_file=accounting | {path: changed})
    with pytest.raises(ValueError):
        replace(manifest, event_count_by_modality={"metric": manifest.total_event_count - 1,
                                                   "log": 1})


def test_manifest_rejects_reason_unit_mismatches_and_strict_partial(aiops_root):
    permissive = AIOps2020Adapter(aiops_root, strict=False)
    manifest = permissive.load_incident(permissive.list_incidents()[0].incident_id)
    accounting = _accounting_objects(manifest); path = next(iter(accounting)); item = accounting[path]
    with pytest.raises(ValueError):
        replace(manifest, rejection_reasons_by_file={path: {"invalid_metric_cell:x": 1}})
    rejected = replace(item, accepted_rows=max(0, item.accepted_rows - 1),
                       rejected_rows=item.rejected_rows + 1,
                       rejected_row_units=item.rejected_row_units + 1)
    with pytest.raises(ValueError):
        replace(manifest, normalization_accounting_by_file=accounting | {path: rejected})
    partial_adapter_root = aiops_root.parent / "partial-contract"
    write(partial_adapter_root / "fault.csv",
          "timestamp,fault_type,fault_location\n1705354566,cpu,x\n")
    write(partial_adapter_root / "业务指标" / "business.csv",
          "timestamp,serviceName,success_rate,request_count\n1705354566,x,0.9,bad\n")
    adapter = AIOps2020Adapter(partial_adapter_root, strict=False)
    partial = adapter.load_incident(adapter.list_incidents()[0].incident_id)
    assert not partial.usable_for_benchmark and partial.empty_incident_reason
    with pytest.raises(ValueError): replace(partial, normalization_mode="strict")


def test_manifest_modality_policy_mutations_fail(rcaeval_root):
    manifest = RCAEvalAdapter(rcaeval_root).load_incident(
        RCAEvalAdapter(rcaeval_root).list_incidents()[0].incident_id)
    mutations = (
        {"expected_modalities": ()},
        {"modality_policy_status": "nonofficial"},
        {"modality_policy_status": "unknown"},
        {"observed_modalities": ("metric",)},
        {"expected_modalities": manifest.expected_modalities + ("business_event",),
         "missing_expected_modalities": ("business_event",)},
    )
    for values in mutations:
        with pytest.raises(ValueError): replace(manifest, **values)
    with pytest.raises(ValueError):
        replace(manifest, normalization_mode="permissive", modality_policy_status="nonofficial",
                usable_for_benchmark=True)


def test_permissive_integrity_and_empty_accounting_policy(rcaeval_root):
    strict = RCAEvalAdapter(rcaeval_root).load_incident(
        RCAEvalAdapter(rcaeval_root).list_incidents()[0].incident_id)
    intact = replace(strict, normalization_mode="permissive")
    assert intact.usable_for_benchmark and intact.empty_incident_reason is None
    with pytest.raises(ValueError, match="íntegro"):
        replace(intact, empty_incident_reason="motivo indevido")
    empty = replace(strict, normalization_mode="permissive", modalities=(),
                    event_count_by_modality={}, observed_modalities=(),
                    expected_modalities=("metric",), missing_expected_modalities=("metric",),
                    unexpected_modalities=(), total_event_count=0, accepted_row_count=0,
                    rejected_row_count=0, considered_row_count=0,
                    normalized_source_files=(), normalization_accounting_by_file={},
                    rejection_reasons_by_file={}, quality_issue_reasons_by_file={},
                    unmapped_fields={},
                    usable_for_benchmark=False, empty_incident_reason="sem eventos")
    assert empty.normalization_accounting_by_file == {}


def test_schema_report_layout_policy_mutations_fail():
    from multimodal_v1.public_datasets_v3 import SchemaClassification, SchemaFileReport
    base = dict(relative_path="wide.csv", kind="metrics", columns=("time", "a", "b"),
                classification=SchemaClassification(identity_fields=("time",),
                    interpreted_metric_fields=("a", "b")),
                row_count=1, checksum_sha256="0" * 64, accepted_row_count=1,
                event_count=1, rejection_reasons={"invalid_metric_cell:b": 1},
                wide_row_policy="partial-wide-row-v1", layout_policy="partial-wide-row-v1",
                rows_partially_accepted=1, accepted_metric_cell_count=1,
                rejected_metric_cell_count=1)
    assert SchemaFileReport(**base).layout_policy == "partial-wide-row-v1"
    for mutation in (
        {"wide_row_policy": None, "layout_policy": "non-metric-v1"},
        {"rows_partially_accepted": 2},
        {"accepted_metric_cell_count": 2},
        {"rejected_metric_cell_count": 0},
        {"rejection_reasons": {"quality_only": 1}},
    ):
        with pytest.raises(ValueError): SchemaFileReport(**(base | mutation))
    for mutation in ({"row_count": True}, {"event_count": -1},
                     {"rejection_reasons": {"": 1}},
                     {"quality_issue_reasons": {"bad": True}}):
        with pytest.raises(ValueError): SchemaFileReport(**(base | mutation))


def test_remaining_contract_mutation_matrix_scenarios(tmp_path, rcaeval_root, aiops_root):
    from multimodal_v1.public_datasets_v3 import NormalizedFileAccounting
    for kind, signal, field in (("traces", "trace", "accepted_metric_cells"),
                                ("logs", "log", "rejected_metric_cells")):
        values = dict(relative_path=f"{kind}.csv", kind=kind, signal_type=signal,
                      total_rows=1, accepted_rows=1, rejected_rows=0,
                      partially_accepted_rows=0, excluded_rows=0, produced_events=1,
                      accepted_metric_cells=0, rejected_metric_cells=0,
                      rejected_row_units=0, rejected_cell_units=0)
        values[field] = 1
        if field == "rejected_metric_cells": values["rejected_cell_units"] = 1
        with pytest.raises(ValueError): NormalizedFileAccounting(**values)

    strict = RCAEvalAdapter(rcaeval_root).load_incident(
        RCAEvalAdapter(rcaeval_root).list_incidents()[0].incident_id)
    accounting = _accounting_objects(strict)
    metric_path = next(path for path, item in accounting.items() if item.signal_type == "metric")
    metric = accounting[metric_path]
    rejected = replace(metric, accepted_rows=metric.accepted_rows - 1,
                       rejected_rows=1, rejected_row_units=1)
    with pytest.raises(ValueError):
        replace(strict, normalization_accounting_by_file=accounting | {metric_path: rejected})
    with pytest.raises(ValueError):
        replace(strict, rejection_reasons_by_file={metric_path: {"row_without_valid_metric": 1}})

    partial_root = tmp_path / "partial-matrix"
    write(partial_root / "fault.csv", "timestamp,fault_type,fault_location\n1705354566,cpu,x\n")
    write(partial_root / "业务指标" / "business.csv",
          "timestamp,serviceName,success_rate,request_count\n1705354566,x,0.9,bad\n")
    partial_adapter = AIOps2020Adapter(partial_root, strict=False)
    partial = partial_adapter.load_incident(partial_adapter.list_incidents()[0].incident_id)
    with pytest.raises(ValueError): replace(partial, usable_for_benchmark=True)
    with pytest.raises(ValueError): replace(partial, empty_incident_reason=None)

    unknown_root = tmp_path / "unknown-matrix" / "CUSTOM" / "cart_cpu" / "1"
    write(unknown_root / "inject_time.txt", "1705354566")
    write(unknown_root / "metrics.json", json.dumps({"cart_cpu": [[1705354566, 1]]}))
    unknown_adapter = RCAEvalAdapter(tmp_path / "unknown-matrix", strict=False)
    unknown = unknown_adapter.load_incident(unknown_adapter.list_incidents()[0].incident_id)
    with pytest.raises(ValueError): replace(unknown, empty_incident_reason=None)

    first = AIOps2020Adapter(aiops_root, duration_units={"duration": "ms"}).load_incident("aiops2020:0")
    second = AIOps2020Adapter(aiops_root, duration_units={"duration": "ms"}).load_incident("aiops2020:0")
    assert first.fingerprint == second.fingerprint
    accounts = _accounting_objects(first)
    metrics = [(path, item) for path, item in accounts.items()
               if item.signal_type == "metric" and item.produced_events > 0]
    left_path, left = metrics[0]; right_path, right = metrics[1]
    shifted = accounts | {
        left_path: replace(left, total_rows=left.total_rows - 1,
                           accepted_rows=left.accepted_rows - 1,
                           produced_events=left.produced_events - 1,
                           accepted_metric_cells=left.accepted_metric_cells - 1),
        right_path: replace(right, total_rows=right.total_rows + 1,
                            accepted_rows=right.accepted_rows + 1,
                            produced_events=right.produced_events + 1,
                            accepted_metric_cells=right.accepted_metric_cells + 1),
    }
    changed = replace(first, normalization_mode="permissive", usable_for_benchmark=False,
                      empty_incident_reason="accounting alterado", normalization_accounting_by_file=shifted)
    assert changed.fingerprint != first.fingerprint


# Gate final de adulteração dos contratos públicos v3.
def test_manifest_rejects_all_canonical_path_collisions(rcaeval_root):
    adapter = RCAEvalAdapter(rcaeval_root)
    manifest = adapter.load_incident(adapter.list_incidents()[0].incident_id)
    path = next(iter(manifest.normalized_source_files))
    alias = path.replace("/", "\\")
    source_value = manifest.source_files_sha256[path]
    accounting_value = manifest.normalization_accounting_by_file[path]
    mutations = (
        {"source_files_sha256": {path: source_value, alias: source_value}},
        {"normalized_source_files": (path, alias)},
        {"normalization_accounting_by_file": {path: accounting_value, alias: accounting_value}},
        {"rejection_reasons_by_file": {path: {}, alias: {}}},
        {"quality_issue_reasons_by_file": {path: {}, alias: {}}},
        {"unmapped_fields": {path: (), alias: ()}},
    )
    for mutation in mutations:
        with pytest.raises(ValueError, match="duplicados após canonicalização"):
            replace(manifest, **mutation)


@pytest.mark.parametrize("invalid", ("/absolute.csv", "case/../metrics.json", "case//metrics.json"))
def test_contract_rejects_noncanonical_relative_paths(invalid):
    from multimodal_v1.public_datasets_v3.contracts import canonical_relative_path
    with pytest.raises(ValueError):
        canonical_relative_path(invalid, "test_path")


def test_valid_paths_are_canonical_sorted_and_stable():
    from multimodal_v1.public_datasets_v3.contracts import canonical_unique_paths
    assert canonical_unique_paths(["z/file.csv", "a\\file.csv"], "paths") == (
        "a/file.csv", "z/file.csv")


def test_incident_manifest_from_dict_authenticates_fingerprint(rcaeval_root):
    from multimodal_v1.public_datasets_v3 import IncidentManifest
    adapter = RCAEvalAdapter(rcaeval_root)
    manifest = adapter.load_incident(adapter.list_incidents()[0].incident_id)
    payload = manifest.to_dict()
    without = dict(payload); without.pop("fingerprint")
    assert IncidentManifest.from_dict(without).fingerprint == manifest.fingerprint
    assert IncidentManifest.from_dict(payload).fingerprint == manifest.fingerprint
    malformed = dict(payload); malformed["fingerprint"] = "ABC"
    with pytest.raises(ValueError, match="fingerprint"):
        IncidentManifest.from_dict(malformed)
    divergent = dict(payload); divergent["fingerprint"] = "0" * 64
    with pytest.raises(ValueError, match="divergente"):
        IncidentManifest.from_dict(divergent)
    tampered = json.loads(json.dumps(payload)); tampered["source_case"] += "-tampered"
    with pytest.raises(ValueError, match="divergente"):
        IncidentManifest.from_dict(tampered)
    round_trip = IncidentManifest.from_dict(json.loads(json.dumps(payload)))
    assert round_trip.to_dict() == payload


@pytest.mark.parametrize("rejected, reasons, valid", (
    (1, {"invalid_row": 1}, True),
    (1, {"invalid_a": 1, "invalid_b": 1}, False),
    (1, {"invalid_row": 999}, False),
    (2, {"invalid_row": 1}, False),
    (1, {}, False),
    (0, {"invalid_row": 1}, False),
    (1, {"invalid_metric_cell:cpu": 1}, False),
))
def test_schema_file_report_reconciles_exact_row_reason_units(rejected, reasons, valid):
    from multimodal_v1.public_datasets_v3 import SchemaClassification, SchemaFileReport
    values = dict(relative_path="logs.csv", kind="logs", columns=("timestamp",),
                  classification=SchemaClassification(identity_fields=("timestamp",)),
                  row_count=rejected, checksum_sha256="0" * 64,
                  rejected_row_count=rejected, rejection_reasons=reasons)
    if valid:
        assert SchemaFileReport(**values).rejected_row_count == 1
    else:
        with pytest.raises(ValueError, match="row-level"):
            SchemaFileReport(**values)


def test_quality_issue_cannot_substitute_rejection_reason():
    from multimodal_v1.public_datasets_v3 import SchemaFileReport
    with pytest.raises(ValueError, match="row-level"):
        SchemaFileReport("logs.csv", "logs", (), 1, "0" * 64,
                         rejected_row_count=1, quality_issue_reasons={"bad_id": 1})


def test_fully_rejected_wide_row_reconciles_row_and_cell_dimensions():
    from multimodal_v1.public_datasets_v3 import SchemaClassification, SchemaFileReport
    report = SchemaFileReport(
        "metrics.csv", "metrics", ("time", "a", "b"), 1, "0" * 64,
        classification=SchemaClassification(identity_fields=("time",),
            interpreted_metric_fields=("a", "b")),
        rejected_row_count=1,
        rejection_reasons={"row_without_valid_metric": 1,
                           "invalid_metric_cell:a": 1,
                           "invalid_metric_cell:b": 1},
        wide_row_policy="partial-wide-row-v1", rows_fully_rejected=1,
        rejected_metric_cell_count=2)
    assert report.rejected_row_count == 1 and report.rejected_metric_cell_count == 2


@pytest.mark.parametrize("field", (
    "incident_id", "source_dataset", "source_system", "source_case", "adapter_version",
    "window_policy", "window_decision", "modality_policy_version",
))
def test_incident_manifest_rejects_empty_identity_fields(rcaeval_root, field):
    adapter = RCAEvalAdapter(rcaeval_root)
    manifest = adapter.load_incident(adapter.list_incidents()[0].incident_id)
    with pytest.raises(ValueError, match=field):
        replace(manifest, **{field: "  "})


def test_incident_manifest_enforces_official_schema_version(rcaeval_root):
    from multimodal_v1.public_datasets_v3 import INCIDENT_MANIFEST_SCHEMA_VERSION
    adapter = RCAEvalAdapter(rcaeval_root)
    manifest = adapter.load_incident(adapter.list_incidents()[0].incident_id)
    assert manifest.schema_version == INCIDENT_MANIFEST_SCHEMA_VERSION
    with pytest.raises(ValueError, match="schema_version"):
        replace(manifest, schema_version="incident-manifest-v999")


def test_schema_reports_are_defensively_immutable_and_canonical():
    from multimodal_v1.public_datasets_v3 import (
        SchemaClassification, SchemaFileReport, SchemaReport, ValidationReport)
    classification_input = ["service", "timestamp"]
    columns_input = ["timestamp", "service"]
    classification = SchemaClassification(identity_fields=classification_input)
    file_report = SchemaFileReport("case\\logs.csv", "logs", columns_input, 0, "0" * 64,
                                   classification=classification)
    files_input = [file_report]
    errors_input = ["error"]
    warnings_input = ["warning"]
    report = SchemaReport("dataset", files_input, datetime.now(timezone.utc))
    validation = ValidationReport(False, errors_input, warnings_input, report)
    fingerprint = report.fingerprint
    classification_input.append("mutated")
    columns_input.append("mutated")
    files_input.clear(); errors_input.append("mutated"); warnings_input.clear()
    assert classification.identity_fields == ("service", "timestamp")
    assert file_report.columns == ("timestamp", "service")
    assert file_report.relative_path == "case/logs.csv"
    assert report.files == (file_report,) and report.fingerprint == fingerprint
    assert validation.errors == ("error",) and validation.warnings == ("warning",)
    equivalent = SchemaReport("dataset", [file_report], report.generated_at)
    assert equivalent.fingerprint == report.fingerprint


def test_schema_report_rejects_duplicate_canonical_paths():
    from multimodal_v1.public_datasets_v3 import SchemaFileReport, SchemaReport
    left = SchemaFileReport("case/logs.csv", "logs", (), 0, "0" * 64)
    right = SchemaFileReport("case\\logs.csv", "logs", (), 0, "0" * 64)
    with pytest.raises(ValueError, match="duplicado"):
        SchemaReport("dataset", [left, right], datetime.now(timezone.utc))


@pytest.mark.parametrize("start,end,error", (
    (None, datetime.now(timezone.utc), TypeError),
    (datetime.now(timezone.utc), None, TypeError),
    ("invalid", datetime.now(timezone.utc), TypeError),
    (datetime.now(timezone.utc), "invalid", TypeError),
    (datetime.now(), datetime.now(), ValueError),
))
def test_analysis_window_rejects_invalid_types_and_naive_values(start, end, error):
    from multimodal_v1.public_datasets_v3 import AnalysisWindow
    with pytest.raises(error):
        AnalysisWindow(start, end)


def test_analysis_window_rejects_inversion_and_normalizes_to_utc():
    from multimodal_v1.public_datasets_v3 import AnalysisWindow
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="preceder"):
        AnalysisWindow(now, now - timedelta(seconds=1))
    assert AnalysisWindow(now, now).start.tzinfo is timezone.utc
    local = timezone(timedelta(hours=-3))
    converted = AnalysisWindow(now.astimezone(local), (now + timedelta(seconds=1)).astimezone(local))
    assert converted.start.tzinfo is timezone.utc and converted.start == now


@pytest.mark.parametrize("field,value", (
    ("analysis_window", None), ("analysis_window", "invalid"),
    ("observed_interval", "invalid"), ("usable_for_benchmark", 1),
    ("usable_for_benchmark", 0), ("fault_type", ""),
    ("fault_service", "   "),
))
def test_incident_manifest_rejects_invalid_public_field_types(rcaeval_root, field, value):
    adapter = RCAEvalAdapter(rcaeval_root)
    manifest = adapter.load_incident(adapter.list_incidents()[0].incident_id)
    with pytest.raises((TypeError, ValueError), match=field):
        replace(manifest, **{field: value})


def test_incident_manifest_accepts_valid_windows_and_optional_none(rcaeval_root):
    from multimodal_v1.public_datasets_v3 import AnalysisWindow
    adapter = RCAEvalAdapter(rcaeval_root)
    manifest = adapter.load_incident(adapter.list_incidents()[0].incident_id)
    assert isinstance(manifest.analysis_window, AnalysisWindow)
    assert replace(manifest, observed_interval=None).observed_interval is None
    with pytest.raises(TypeError, match="analysis_window"):
        replace(manifest, analysis_window=object())


@pytest.mark.parametrize("missing", ("analysis_window", "incident_id", "schema_version",
                                      "source_files_sha256"))
def test_incident_manifest_from_dict_requires_explicit_fields(rcaeval_root, missing):
    from multimodal_v1.public_datasets_v3 import IncidentManifest
    adapter = RCAEvalAdapter(rcaeval_root)
    payload = adapter.load_incident(adapter.list_incidents()[0].incident_id).to_dict()
    payload.pop(missing)
    with pytest.raises(ValueError, match=missing):
        IncidentManifest.from_dict(payload)


def test_incident_manifest_complete_payload_round_trip_stays_authenticated(rcaeval_root):
    from multimodal_v1.public_datasets_v3 import IncidentManifest
    adapter = RCAEvalAdapter(rcaeval_root)
    manifest = adapter.load_incident(adapter.list_incidents()[0].incident_id)
    payload = manifest.to_dict(); without = dict(payload); without.pop("fingerprint")
    assert IncidentManifest.from_dict(without).fingerprint == manifest.fingerprint
    assert IncidentManifest.from_dict(payload).fingerprint == manifest.fingerprint


@pytest.mark.parametrize("valid", (0, 1))
def test_validation_report_rejects_integer_booleans(valid):
    from multimodal_v1.public_datasets_v3 import ValidationReport
    with pytest.raises(TypeError, match="valid"):
        ValidationReport(valid, () if valid else ("error",))


def test_validation_report_enforces_valid_errors_equivalence_and_nonempty_messages():
    from multimodal_v1.public_datasets_v3 import ValidationReport
    assert ValidationReport(True).valid
    assert not ValidationReport(False, ["error"]).valid
    assert ValidationReport(True, warnings=["warning"]).warnings == ("warning",)
    with pytest.raises(ValueError, match="ausência"):
        ValidationReport(True, ["error"])
    with pytest.raises(ValueError, match="ausência"):
        ValidationReport(False)
    with pytest.raises(ValueError, match="texto não vazio"):
        ValidationReport(False, [""])
    with pytest.raises(ValueError, match="texto não vazio"):
        ValidationReport(True, warnings=["   "])


@pytest.mark.parametrize("kind,layout,kwargs", (
    ("unknown", "non-metric-v1", {}),
    ("traces", "long-metric-row-v1", {}),
    ("logs", "partial-wide-row-v1", {"wide_row_policy": "partial-wide-row-v1"}),
    ("faults", "non-metric-v1", {"accepted_row_count": 1, "event_count": 1}),
    ("metrics", "non-metric-v1", {}),
))
def test_schema_file_report_rejects_incompatible_kind_layout(kind, layout, kwargs):
    from multimodal_v1.public_datasets_v3 import SchemaFileReport
    values = dict(relative_path="file.csv", kind=kind, columns=(), row_count=0,
                  checksum_sha256="0" * 64, layout_policy=layout)
    if kwargs.get("accepted_row_count"):
        values["row_count"] = 1
    with pytest.raises(ValueError):
        SchemaFileReport(**(values | kwargs))


@pytest.mark.parametrize("scope", ("dataset_schema_report", "incident_normalization_report"))
def test_schema_report_accepts_only_official_scopes(scope):
    from multimodal_v1.public_datasets_v3 import SchemaReport
    assert SchemaReport("dataset", [], datetime.now(timezone.utc), scope).report_scope == scope


def test_schema_report_rejects_unknown_scope_and_invalid_generated_at():
    from multimodal_v1.public_datasets_v3 import SchemaReport
    with pytest.raises(ValueError, match="report_scope"):
        SchemaReport("dataset", [], datetime.now(timezone.utc), "unknown")
    with pytest.raises(ValueError, match="generated_at"):
        SchemaReport("dataset", [], "invalid")
    with pytest.raises(ValueError, match="generated_at"):
        SchemaReport("dataset", [], datetime.now())


@pytest.mark.parametrize("protocol,held_out", (
    ("within-dataset", "unexpected"),
    ("cross-system", 123),
    ("cross-source", True),
    ("leave-one-system-out", "   "),
))
def test_protocol_split_rejects_invalid_held_out(protocol, held_out):
    from multimodal_v1.public_datasets_v3 import ProtocolSplit
    kwargs = {"seed": 7, "test_fraction": .5} if protocol == "within-dataset" else {}
    with pytest.raises((TypeError, ValueError), match="held_out"):
        ProtocolSplit(protocol, ("train",), ("test",), held_out, **kwargs)


def test_protocol_split_normalizes_valid_holdout_and_planner_is_stable():
    from multimodal_v1.public_datasets_v3 import IncidentDescriptor, ProtocolPlanner, ProtocolSplit
    assert ProtocolSplit("cross-system", ("a",), ("b",), " system ").held_out == "system"
    incidents = (IncidentDescriptor("a", "source", "left", "case-a", "/a"),
                 IncidentDescriptor("b", "source", "right", "case-b", "/b"))
    assert ProtocolPlanner.plan(incidents, "cross-system", held_out_system="right").test_incident_ids == ("b",)


def test_protocol_plan_manifest_rejects_non_split_fold():
    from multimodal_v1.public_datasets_v3 import ProtocolPlanManifest
    with pytest.raises(TypeError, match="ProtocolSplit"):
        ProtocolPlanManifest("cross-system", ("a", "b"), ("left", "right"),
                             ("source",), (object(),),
                             {"a": {"system": "left", "source": "source"},
                              "b": {"system": "right", "source": "source"}})


def test_replay_result_rejects_empty_error_and_defensively_copies():
    from multimodal_v1.public_datasets_v3 import ReplayResult
    errors = ["failure"]
    result = ReplayResult(1, 0, 1, errors); errors.append("mutated")
    assert result.errors == ("failure",)
    with pytest.raises(ValueError, match="texto não vazio"):
        ReplayResult(1, 0, 1, [""])



def _complete_classification(columns, metric_fields=()):
    from multimodal_v1.public_datasets_v3 import SchemaClassification
    metric_fields = tuple(metric_fields)
    return SchemaClassification(
        identity_fields=tuple(item for item in columns if item not in metric_fields),
        interpreted_metric_fields=metric_fields)


@pytest.mark.parametrize("values,valid", (
    (dict(total_rows=1, accepted_rows=1, rejected_rows=0, partially_accepted_rows=0,
          produced_events=1, accepted_metric_cells=1, rejected_metric_cells=1,
          rejected_row_units=0, rejected_cell_units=1), False),
    (dict(total_rows=1, accepted_rows=1, rejected_rows=0, partially_accepted_rows=1,
          produced_events=1, accepted_metric_cells=1, rejected_metric_cells=1,
          rejected_row_units=0, rejected_cell_units=1), True),
    (dict(total_rows=1, accepted_rows=0, rejected_rows=1, partially_accepted_rows=0,
          produced_events=0, accepted_metric_cells=0, rejected_metric_cells=2,
          rejected_row_units=1, rejected_cell_units=2), True),
))
def test_accounting_links_rejected_cells_to_partial_or_rejected_rows(values, valid):
    from multimodal_v1.public_datasets_v3 import NormalizedFileAccounting
    values |= dict(relative_path="metrics.csv", kind="metrics", signal_type="metric",
                   excluded_rows=0)
    if valid:
        assert NormalizedFileAccounting(**values).rejected_metric_cells > 0
    else:
        with pytest.raises(ValueError, match="linha parcial ou rejeitada"):
            NormalizedFileAccounting(**values)


def test_accounting_still_rejects_cells_for_logs_and_traces():
    from multimodal_v1.public_datasets_v3 import NormalizedFileAccounting
    for kind, signal in (("logs", "log"), ("traces", "trace")):
        with pytest.raises(ValueError):
            NormalizedFileAccounting("file.csv", kind, signal, 1, 0, 1, 0, 0,
                                     0, 0, 1, 1, 1)


@pytest.mark.parametrize("kind,accepted,events", (
    ("logs", 1, 999), ("logs", 1, 0), ("traces", 1, 0), ("traces", 1, 2),
))
def test_nonmetric_reports_require_one_event_per_accepted_row(kind, accepted, events):
    from multimodal_v1.public_datasets_v3 import SchemaFileReport
    with pytest.raises(ValueError, match="accepted_row_count"):
        SchemaFileReport("file.csv", kind, ("time",), 1, "0" * 64,
                         classification=_complete_classification(("time",)),
                         accepted_row_count=accepted, event_count=events)


@pytest.mark.parametrize("layout", ("long-metric-row-v1", "metric-json-point-v1"))
def test_long_and_json_metric_reports_require_one_to_one_rows_events(layout):
    from multimodal_v1.public_datasets_v3 import SchemaFileReport
    with pytest.raises(ValueError, match="1:1"):
        SchemaFileReport("metric.csv", "metrics", ("time", "value"), 1, "0" * 64,
                         classification=_complete_classification(("time", "value"), ("value",)),
                         accepted_row_count=1, event_count=2,
                         accepted_metric_cell_count=2, layout_policy=layout)


def test_wide_report_allows_multiple_events_per_row():
    from multimodal_v1.public_datasets_v3 import SchemaFileReport
    report = SchemaFileReport(
        "wide.csv", "metrics", ("time", "a", "b", "c"), 1, "0" * 64,
        classification=_complete_classification(("time", "a", "b", "c"), ("a", "b", "c")),
        accepted_row_count=1, event_count=3, accepted_metric_cell_count=3,
        wide_row_policy="partial-wide-row-v1", rows_fully_accepted=1)
    assert report.event_count == 3


@pytest.mark.parametrize("kwargs,conflict", (
    (dict(identity_fields=("x",), unmapped_fields=("x",)), "x"),
    (dict(interpreted_metric_fields=("x",), rejected_fields=("x",)), "x"),
    (dict(preserved_metadata_fields=("x",), unmapped_fields=("x",)), "x"),
))
def test_schema_classification_categories_are_mutually_exclusive(kwargs, conflict):
    from multimodal_v1.public_datasets_v3 import SchemaClassification
    with pytest.raises(ValueError, match=conflict):
        SchemaClassification(**kwargs)


def test_schema_classification_accepts_five_disjoint_categories():
    from multimodal_v1.public_datasets_v3 import SchemaClassification
    value = SchemaClassification(("id",), ("metric",), ("metadata",), ("unknown",), ("bad",))
    assert len(set().union(*map(set, value.to_dict().values()))) == 5


def test_schema_file_report_reconciles_classification_with_columns():
    from multimodal_v1.public_datasets_v3 import SchemaClassification, SchemaFileReport
    base = dict(relative_path="logs.csv", kind="logs", columns=("time", "body"),
                row_count=0, checksum_sha256="0" * 64)
    with pytest.raises(ValueError, match="unmapped_fields diverge"):
        SchemaFileReport(**base, unmapped_fields=("body",),
                         classification=SchemaClassification(identity_fields=("time",),
                                                             unmapped_fields=()))
    with pytest.raises(ValueError, match="ausentes"):
        SchemaFileReport(**base, classification=SchemaClassification(
            identity_fields=("time", "missing"), unmapped_fields=("body",)),
            unmapped_fields=("body",))
    with pytest.raises(ValueError, match="não classificados"):
        SchemaFileReport(**base, classification=SchemaClassification(identity_fields=("time",)))
    valid = SchemaFileReport(**base, classification=SchemaClassification(
        identity_fields=("time",), preserved_metadata_fields=("body",)))
    assert valid.columns == ("time", "body")


def test_protocol_split_canonicalizes_ids_before_uniqueness_and_disjointness():
    from multimodal_v1.public_datasets_v3 import ProtocolSplit
    with pytest.raises(ValueError, match="duplicados"):
        ProtocolSplit("within-dataset", ("a", " a "), ("b",), seed=1, test_fraction=.5)
    with pytest.raises(ValueError, match="disjuntos"):
        ProtocolSplit("within-dataset", (" a ",), ("a",), seed=1, test_fraction=.5)
    with pytest.raises(ValueError, match="texto não vazio"):
        ProtocolSplit("within-dataset", ("   ",), ("b",), seed=1, test_fraction=.5)
    split = ProtocolSplit("within-dataset", (" a ",), (" b ",), seed=1, test_fraction=.5)
    assert split.train_incident_ids == ("a",) and split.test_incident_ids == ("b",)


def test_incident_descriptor_types_and_text_normalization():
    from multimodal_v1.public_datasets_v3 import IncidentDescriptor
    with pytest.raises(ValueError, match="fault_type"):
        IncidentDescriptor("i", "d", "s", "c", "p", fault_type=1)
    with pytest.raises(ValueError, match="fault_service"):
        IncidentDescriptor("i", "d", "s", "c", "p", fault_service=[])
    with pytest.raises(ValueError, match="fault_type"):
        IncidentDescriptor("i", "d", "s", "c", "p", fault_type="   ")
    item = IncidentDescriptor(" i ", " d ", " s ", " c ", " p ", " f ", " svc ")
    assert (item.incident_id, item.source_dataset, item.system, item.case, item.path,
            item.fault_type, item.fault_service) == ("i", "d", "s", "c", "p", "f", "svc")


@pytest.mark.parametrize("missing", ("fault_type", "fault_service"))
def test_manifest_from_dict_requires_nullable_fault_fields(rcaeval_root, missing):
    from multimodal_v1.public_datasets_v3 import IncidentManifest
    adapter = RCAEvalAdapter(rcaeval_root); payload = adapter.load_incident(
        adapter.list_incidents()[0].incident_id).to_dict(); payload.pop(missing)
    with pytest.raises(ValueError, match=missing): IncidentManifest.from_dict(payload)


@pytest.mark.parametrize("window,expected", (
    ({"end": "2026-01-01T00:00:00Z"}, "start"),
    ({"start": "2026-01-01T00:00:00Z"}, "end"),
    ({"start": "invalid", "end": "2026-01-01T00:00:00Z"}, "inválido"),
))
def test_manifest_from_dict_reports_contextual_window_errors(rcaeval_root, window, expected):
    from multimodal_v1.public_datasets_v3 import IncidentManifest
    adapter = RCAEvalAdapter(rcaeval_root); payload = adapter.load_incident(
        adapter.list_incidents()[0].incident_id).to_dict(); payload["analysis_window"] = window
    with pytest.raises(ValueError, match=expected): IncidentManifest.from_dict(payload)


def test_manifest_from_dict_rejects_unknown_fields(rcaeval_root):
    from multimodal_v1.public_datasets_v3 import IncidentManifest
    adapter = RCAEvalAdapter(rcaeval_root); payload = adapter.load_incident(
        adapter.list_incidents()[0].incident_id).to_dict(); payload["unknown"] = True
    with pytest.raises(ValueError, match="desconhecidos"): IncidentManifest.from_dict(payload)
