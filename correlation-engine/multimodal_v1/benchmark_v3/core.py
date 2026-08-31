from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import platform
import random
import statistics
import time
import tracemalloc
import shutil
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping

import joblib
import numpy as np
from multimodal_v1.canonical import canonical_sha256
from sklearn.base import BaseEstimator
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import confusion_matrix, f1_score, precision_score, recall_score
from sklearn.model_selection import ParameterGrid
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


BENCHMARK_VERSION = "rca-model-benchmark-v3"
MODEL_NAMES = (
    "temporal-baseline", "max-zscore", "first-anomalous",
    "logistic-regression", "random-forest", "xgboost-rca-v3", "mlp-light-v3",
)
ML_MODELS = frozenset({"logistic-regression", "random-forest", "xgboost-rca-v3", "mlp-light-v3"})
MAX_COMMON_TUNING_BUDGET = 6
FINAL_FIT_POLICY = "final-fit-train-only-v1"
DEFAULT_SELECTION_METRIC = "validation_mrr"
SHA256_LENGTH = 64
RECIPROCAL_RANK_TOLERANCE = 1e-12


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class BenchmarkRow:
    incident_id: str
    service_name: str
    split: str
    features: tuple[float | None, ...]
    target: bool

    def __post_init__(self) -> None:
        for field_name in ("incident_id", "service_name"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} deve ser string não vazia.")
        if self.split not in {"train", "validation", "test"}:
            raise ValueError("Split desconhecido.")
        if not isinstance(self.target, bool):
            raise TypeError("target deve ser booleano explícito.")
        if not isinstance(self.features, tuple):
            raise TypeError("features deve ser tuple imutável.")
        for value in self.features:
            if value is None or isinstance(value, bool):
                continue
            if not isinstance(value, (int, float)):
                raise TypeError("Features aceitam somente int, float, bool ou None.")
            if not math.isfinite(value):
                raise ValueError("Features numéricas devem ser finitas.")


@dataclass(frozen=True)
class BenchmarkDataset:
    rows: tuple[BenchmarkRow, ...]
    feature_names: tuple[str, ...]
    dataset_hash: str
    feature_schema: Mapping[str, Any]

    def __post_init__(self) -> None:
        schema = json.loads(canonical_json(_thaw(self.feature_schema)))
        object.__setattr__(self, "feature_schema", _deep_freeze(schema))

    @classmethod
    def from_json(cls, path: str | Path) -> "BenchmarkDataset":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or "dataset_sha256" not in payload:
            raise ValueError("Dataset científico requer dataset_sha256 verificável.")
        declared_dataset_hash = payload["dataset_sha256"]
        unhashed = dict(payload)
        del unhashed["dataset_sha256"]
        if declared_dataset_hash != sha256(unhashed):
            raise ValueError("dataset_sha256 diverge do conteúdo canônico.")
        manifest = payload.get("manifest")
        if not isinstance(manifest, dict):
            raise ValueError("Dataset requer manifest objeto.")
        names = tuple(manifest.get("feature_names", ()))
        if not names:
            raise ValueError("Dataset não contém feature_names no manifesto.")
        if manifest.get("base_schema_version") != "rca-features-v3":
            raise ValueError("base_schema_version deve ser rca-features-v3.")
        if manifest.get("column_count") != len(names):
            raise ValueError("column_count diverge de feature_names.")
        for field, json_field in (("registry_sha256", "registry_json"),
                                  ("feature_view_sha256", "feature_view_json")):
            try:
                calculated = sha256(json.loads(manifest[json_field]))
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError(f"Manifesto inválido em {json_field}.") from exc
            if manifest.get(field) != calculated:
                raise ValueError(f"{field} diverge do conteúdo declarado.")
        if manifest.get("feature_fingerprint") != manifest.get("feature_view_sha256"):
            raise ValueError("feature_fingerprint diverge da feature view canônica.")
        raw_rows = payload.get("rows")
        if not isinstance(raw_rows, list):
            raise ValueError("rows deve ser array.")
        if manifest.get("rows_sha256") != sha256(raw_rows):
            raise ValueError("rows_sha256 diverge das linhas.")
        rows = []
        for item in raw_rows:
            vector, target = item.get("vector", {}), item.get("target")
            if not isinstance(target, dict) or target.get("label_status") != "labeled":
                raise ValueError("Benchmark supervisionado requer target labeled em todas as linhas.")
            values = vector.get("features", {})
            if not isinstance(values, dict):
                raise ValueError("features deve ser objeto.")
            missing, extra = set(names) - set(values), set(values) - set(names)
            if missing or extra:
                raise ValueError(f"Features divergem do manifesto: missing={sorted(missing)}, extra={sorted(extra)}.")
            if tuple(vector.get("feature_names", ())) != names:
                raise ValueError("feature_names do vetor diverge do manifesto.")
            if vector.get("feature_fingerprint") != manifest.get("feature_fingerprint"):
                raise ValueError("feature_fingerprint do vetor diverge do manifesto.")
            if vector.get("ablation_id") != manifest.get("ablation_id"):
                raise ValueError("ablation_id do vetor diverge do manifesto.")
            if target.get("incident_id") != vector.get("incident_id"):
                raise ValueError("target.incident_id diverge de vector.incident_id.")
            if target.get("service_name") != vector.get("service_name"):
                raise ValueError("target.service_name diverge de vector.service_name.")
            root = target.get("is_root_cause")
            if not isinstance(root, bool):
                raise ValueError("is_root_cause deve ser booleano explícito.")
            rows.append(BenchmarkRow(
                vector["incident_id"], vector["service_name"], item["split"],
                tuple(values[name] for name in names), root,
            ))
        canonical_rows = sorted(raw_rows, key=lambda item: (
            item["split"], item["vector"]["incident_id"], item["vector"]["service_name"]))
        if raw_rows != canonical_rows:
            raise ValueError("Linhas do dataset não estão em ordem canônica.")
        expected_split_ids = manifest.get("split_incident_ids")
        if not isinstance(expected_split_ids, dict):
            raise ValueError("split_incident_ids ausente ou inválido.")
        for split in ("train", "validation", "test"):
            split_rows = [item for item in raw_rows if item.get("split") == split]
            if manifest.get("split_sha256", {}).get(split) != sha256(split_rows):
                raise ValueError(f"split_sha256 diverge para {split}.")
            actual_ids = sorted({item["vector"]["incident_id"] for item in split_rows})
            if actual_ids != sorted(expected_split_ids.get(split, ())):
                raise ValueError(f"IDs declarados divergem no split {split}.")
        binding_policy = manifest.get("baseline_binding_policy", "split-train-exact")
        binding_provenance = manifest.get("baseline_binding_provenance", {})
        if binding_policy == "split-train-exact":
            try:
                baseline_payload = json.loads(manifest["baseline_json"])
                baseline_ids = set(baseline_payload["training_incident_ids"])
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError("Baseline serializado inválido.") from exc
            if manifest.get("baseline_sha256") != sha256(baseline_payload):
                raise ValueError("baseline_sha256 diverge do baseline serializado.")
            if baseline_ids != set(expected_split_ids["train"]) or binding_provenance:
                raise ValueError("Baseline deve corresponder exatamente ao split train.")
        elif binding_policy == "frozen-parent-train":
            required = {"official_parent_baseline_sha256", "parent_base_key",
                        "parent_split_sha256", "scientific_tranche_id",
                        "scientific_tranche_sha256", "parent_training_incident_ids_sha256",
                        "parent_training_incident_count", "baseline_binding_sha256"}
            if not isinstance(binding_provenance, dict) or set(binding_provenance) != required:
                raise ValueError("Proveniência frozen-parent-train incompleta.")
            if manifest.get("baseline_json") is not None:
                raise ValueError("frozen-parent-train não pode embutir baseline_json.")
            reference = manifest.get("baseline_reference")
            reference_required = {"schema_version", "path", "baseline_sha256",
                                  "parent_base_key", "parent_split_sha256",
                                  "parent_training_incident_ids_sha256",
                                  "parent_training_incident_count", "preprocessing_version"}
            if not isinstance(reference, dict) or set(reference) != reference_required \
                    or reference.get("schema_version") != "frozen-parent-baseline-reference-v1":
                raise ValueError("Referência frozen-parent-train incompleta.")
            if (not isinstance(reference.get("path"), str) or reference["path"].startswith("/")
                    or ".." in reference["path"].split("/")):
                raise ValueError("Referência frozen-parent-train insegura.")
            if reference["baseline_sha256"] != manifest.get("baseline_sha256"):
                raise ValueError("SHA da referência de baseline diverge do manifesto.")
            baseline_ids = set(manifest.get("baseline_parent_training_incident_ids", ()))
            if len(baseline_ids) != reference["parent_training_incident_count"] \
                    or canonical_sha256(sorted(baseline_ids)) != reference[
                        "parent_training_incident_ids_sha256"]:
                raise ValueError("IDs compactos do baseline pai divergem da referência.")
            if not set(expected_split_ids["train"]) <= baseline_ids:
                raise ValueError("Tranche train não pertence integralmente ao baseline pai.")
            if (set(expected_split_ids["validation"]) | set(expected_split_ids["test"])) & baseline_ids:
                raise ValueError("Validation/test da tranche sobrepõem o baseline pai.")
            if binding_provenance["official_parent_baseline_sha256"] != manifest["baseline_sha256"]:
                raise ValueError("Baseline oficial diverge da proveniência.")
            if binding_provenance["parent_training_incident_ids_sha256"] != sha256(
                    sorted(baseline_ids)) or binding_provenance[
                        "parent_training_incident_count"] != len(baseline_ids):
                raise ValueError("Identidade do treino do baseline pai diverge.")
            from multimodal_v1.features_v3.dataset import baseline_binding_sha256
            if binding_provenance["baseline_binding_sha256"] != baseline_binding_sha256(
                    binding_provenance, expected_split_ids):
                raise ValueError("Binding canônico frozen-parent-train diverge.")
            total = sum(len(expected_split_ids[name]) for name in ("train", "validation", "test"))
            expected_fractions = {name: len(expected_split_ids[name]) / total
                                  for name in ("train", "validation", "test")}
            fractions = manifest.get("split_fractions", {})
            if any(not math.isclose(fractions.get(name, -1), expected_fractions[name],
                                    rel_tol=1e-12, abs_tol=1e-12)
                   for name in expected_fractions):
                raise ValueError("Frações frozen-parent-train divergem dos splits.")
        else:
            raise ValueError("baseline_binding_policy inválida.")
        if manifest.get("row_count") != len(rows):
            raise ValueError("row_count diverge das linhas.")
        if manifest.get("incident_count") != len({row.incident_id for row in rows}):
            raise ValueError("incident_count diverge das linhas.")
        distribution = {"positive": 0, "negative": 0, "unknown": 0,
                        "excluded": 0, "unlabeled": 0}
        for item in raw_rows:
            target = item.get("target")
            if target is None:
                distribution["unlabeled"] += 1
            elif target.get("label_status") == "labeled":
                distribution["positive" if target.get("is_root_cause") else "negative"] += 1
            else:
                distribution[target.get("label_status")] += 1
        if manifest.get("label_distribution") != distribution:
            raise ValueError("label_distribution diverge das linhas.")
        dataset = cls(tuple(rows), names, declared_dataset_hash, {
            "base_schema_version": manifest.get("base_schema_version"),
            "feature_view_json": manifest.get("feature_view_json"),
            "feature_view_sha256": manifest.get("feature_view_sha256"),
            "column_count": manifest.get("column_count"),
            "ablation_id": manifest.get("ablation_id"),
            "registry_json": manifest.get("registry_json"),
            "rows_sha256": manifest.get("rows_sha256"),
            "registry_sha256": manifest.get("registry_sha256"),
            "split_sha256": manifest.get("split_sha256"),
            "feature_fingerprint": manifest.get("feature_fingerprint"),
            "feature_names": list(names),
            "baseline_binding_policy": binding_policy,
            "baseline_binding_provenance": binding_provenance,
            "baseline_sha256": manifest.get("baseline_sha256"),
            "baseline_reference": manifest.get("baseline_reference", {}),
        })
        dataset.validate()
        return dataset

    def validate(self) -> None:
        if not self.rows:
            raise ValueError("Dataset vazio.")
        if (not self.feature_names or len(self.feature_names) != len(set(self.feature_names))
                or any(not isinstance(name, str) or not name.strip() for name in self.feature_names)):
            raise ValueError("feature_names deve conter nomes únicos não vazios.")
        if (not isinstance(self.dataset_hash, str) or len(self.dataset_hash) != SHA256_LENGTH
                or any(char not in "0123456789abcdef" for char in self.dataset_hash)):
            raise ValueError("dataset_hash deve ser SHA-256 hexadecimal.")
        keys = [(row.incident_id, row.service_name) for row in self.rows]
        if len(keys) != len(set(keys)):
            raise ValueError("Linha duplicada por incidente/serviço.")
        splits: dict[str, set[str]] = {"train": set(), "validation": set(), "test": set()}
        for row in self.rows:
            if row.split not in splits:
                raise ValueError("Split desconhecido.")
            if len(row.features) != len(self.feature_names):
                raise ValueError("Largura da linha diverge do feature schema.")
            splits[row.split].add(row.incident_id)
        incidents = {row.incident_id for row in self.rows}
        for incident in incidents:
            incident_rows = [row for row in self.rows if row.incident_id == incident]
            if len({row.split for row in incident_rows}) != 1:
                raise ValueError("Todas as linhas do incidente devem pertencer ao mesmo split.")
            if not any(row.target for row in incident_rows):
                raise ValueError("Todo incidente requer ao menos uma causa raiz positiva.")
        if any(splits[left] & splits[right] for left, right in (
            ("train", "validation"), ("train", "test"), ("validation", "test")
        )):
            raise ValueError("Leakage: incident_id aparece em mais de um split.")

    def select(self, split: str) -> tuple[np.ndarray, np.ndarray, tuple[BenchmarkRow, ...]]:
        rows = tuple(row for row in self.rows if row.split == split)
        return (np.asarray([[np.nan if value is None else value for value in row.features]
                            for row in rows], dtype=float),
                np.asarray([row.target for row in rows], dtype=int), rows)


@dataclass(frozen=True)
class NormalizedIncidentPrediction:
    incident_id: str
    actual_services: tuple[str, ...]
    ranking: tuple[Mapping[str, Any], ...]
    first_root_rank: int
    reciprocal_rank: float
    top1_correct: bool
    top3_correct: bool


def validate_incident_prediction(item: Mapping[str, Any]) -> NormalizedIncidentPrediction:
    if not isinstance(item, Mapping):
        raise TypeError("Previsão de incidente deve ser objeto.")
    incident_id = item.get("incident_id")
    if not isinstance(incident_id, str) or not incident_id.strip():
        raise ValueError("incident_id da previsão deve ser texto não vazio.")
    actual = item.get("actual_services")
    if (not isinstance(actual, list) or not actual
            or any(not isinstance(value, str) or not value.strip() for value in actual)
            or actual != sorted(actual) or len(actual) != len(set(actual))):
        raise ValueError("actual_services deve ser lista não vazia, única e ordenada.")
    raw_ranking = item.get("ranking")
    if not isinstance(raw_ranking, list) or not raw_ranking:
        raise ValueError("ranking deve ser lista não vazia.")
    ranking = []
    services = set()
    for candidate in raw_ranking:
        if not isinstance(candidate, Mapping):
            raise TypeError("Candidato do ranking deve ser objeto.")
        service = candidate.get("service_name")
        if not isinstance(service, str) or not service.strip():
            raise ValueError("service_name do ranking deve ser texto não vazio.")
        if service in services:
            raise ValueError("service_name duplicado no ranking.")
        services.add(service)
        score = candidate.get("score")
        if (isinstance(score, bool) or not isinstance(score, (int, float))
                or not math.isfinite(score) or not 0 <= score <= 1):
            raise ValueError("score deve ser numérico finito em [0,1].")
        root = candidate.get("is_root_cause")
        if not isinstance(root, bool):
            raise TypeError("is_root_cause do ranking deve ser booleano.")
        ranking.append({"service_name": service, "score": float(score),
                        "is_root_cause": root})
    expected_order = sorted(ranking, key=lambda value: (-value["score"], value["service_name"]))
    if ranking != expected_order:
        raise ValueError("ranking não está em ordem canônica.")
    roots = {candidate["service_name"] for candidate in ranking
             if candidate["is_root_cause"]}
    if roots != set(actual):
        raise ValueError("actual_services diverge das linhas is_root_cause do ranking.")
    if not set(actual) <= services:
        raise ValueError("Causa real ausente do ranking.")
    first_rank = min(index for index, candidate in enumerate(ranking, 1)
                     if candidate["service_name"] in roots)
    reciprocal = 1 / first_rank
    if "reciprocal_rank" in item:
        serialized = item["reciprocal_rank"]
        if (isinstance(serialized, bool) or not isinstance(serialized, (int, float))
                or not math.isfinite(serialized)
                or not math.isclose(serialized, reciprocal, rel_tol=0,
                                    abs_tol=RECIPROCAL_RANK_TOLERANCE)):
            raise ValueError("reciprocal_rank serializado diverge do ranking reconstruído.")
    return NormalizedIncidentPrediction(
        incident_id, tuple(actual), tuple(MappingProxyType(value) for value in ranking),
        first_rank, reciprocal, first_rank == 1, first_rank <= 3)


class HeuristicEstimator:
    def __init__(self, name: str, feature_names: tuple[str, ...]):
        if name not in MODEL_NAMES[:3]:
            raise ValueError("Baseline desconhecido.")
        self.name, self.feature_names = name, feature_names
        self.index = {name: index for index, name in enumerate(feature_names)}

    def fit(self, _x, _y):
        return self

    def _value(self, row, name, default=0.0):
        index = self.index.get(name)
        if index is None or not math.isfinite(row[index]):
            return default
        return float(row[index])

    def predict_proba(self, matrix):
        scores = []
        for row in matrix:
            distance = self._value(row, "distance_to_first_failure", -1)
            if self.name == "temporal-baseline":
                score = 1 / (1 + distance) if distance >= 0 else 0.0
            elif self.name == "max-zscore":
                values = [abs(self._value(row, name)) for name in (
                    "latency_zscore", "cpu_zscore", "memory_zscore")]
                score = min(1.0, max(values, default=0.0) / 12.0)
            else:
                anomalous = max(
                    self._value(row, "error_span_ratio"),
                    float(self._value(row, "exception_present")),
                    float(self._value(row, "business_failure_count") > 0),
                    float(self._value(row, "anomalous_metric_count") > 0),
                )
                score = (1 / (1 + distance) if distance >= 0 else 0.0) * bool(anomalous)
            scores.append(max(0.0, min(1.0, score)))
        scores = np.asarray(scores)
        return np.column_stack((1 - scores, scores))


@dataclass(frozen=True)
class BenchmarkRuntimePolicy:
    warmup_iterations: int = 2
    batch_repetitions: int = 5
    single_row_repetitions: int = 20
    collect_python_memory: bool = True
    version: str = "benchmark-runtime-policy-v1"

    def __post_init__(self) -> None:
        for name in ("warmup_iterations", "batch_repetitions", "single_row_repetitions"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} deve ser inteiro positivo.")
        if not isinstance(self.collect_python_memory, bool):
            raise TypeError("collect_python_memory deve ser booleano.")

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version, "warmup_iterations": self.warmup_iterations,
                "batch_repetitions": self.batch_repetitions,
                "single_row_repetitions": self.single_row_repetitions,
                "collect_python_memory": self.collect_python_memory}


def _model_and_grid(name: str, seed: int):
    common = [("imputer", SimpleImputer(strategy="median", keep_empty_features=True))]
    if name == "logistic-regression":
        model = Pipeline(common + [("scale", StandardScaler()),
                                   ("model", LogisticRegression(max_iter=500, random_state=seed))])
        grid = {"model__C": (.1, 1.0, 10.0), "model__class_weight": (None, "balanced")}
    elif name == "random-forest":
        model = Pipeline(common + [("model", RandomForestClassifier(random_state=seed, n_jobs=1))])
        grid = {"model__n_estimators": (80, 160), "model__max_depth": (None, 8, 16)}
    elif name == "mlp-light-v3":
        model = Pipeline(common + [("scale", StandardScaler()),
                                   ("model", MLPClassifier(max_iter=250, early_stopping=False,
                                                           random_state=seed))])
        grid = {"model__hidden_layer_sizes": ((32,), (64,), (32, 16)),
                "model__alpha": (1e-4, 1e-3)}
    elif name == "xgboost-rca-v3":
        from xgboost import XGBClassifier
        model = Pipeline(common + [("model", XGBClassifier(
            random_state=seed, n_jobs=1, eval_metric="logloss", tree_method="hist"))])
        grid = {"model__n_estimators": (80, 160), "model__max_depth": (3, 6),
                "model__learning_rate": (.05, .1)}
    else:
        raise ValueError(f"Modelo desconhecido: {name}.")
    return model, tuple(ParameterGrid(grid))


def positive_scores(model, matrix: np.ndarray) -> np.ndarray:
    probabilities = model.predict_proba(matrix)
    scores = np.asarray(probabilities[:, 1], dtype=float)
    if scores.shape != (len(matrix),):
        raise RuntimeError("Quantidade de scores diverge da quantidade de linhas.")
    if not np.all(np.isfinite(scores)) or np.any(scores < 0) or np.any(scores > 1):
        raise RuntimeError("Scores devem ser finitos no intervalo [0,1].")
    return scores


def _ranking_metrics(scores: np.ndarray, rows: tuple[BenchmarkRow, ...]) -> dict[str, float]:
    by_incident: dict[str, list[tuple[float, BenchmarkRow]]] = {}
    for score, row in zip(scores, rows):
        by_incident.setdefault(row.incident_id, []).append((float(score), row))
    reciprocal = []
    for candidates in by_incident.values():
        ranked = sorted(candidates, key=lambda item: (-item[0], item[1].service_name))
        positive = {row.service_name for _, row in ranked if row.target}
        ranks = [index for index, (_, row) in enumerate(ranked, 1)
                 if row.service_name in positive]
        reciprocal.append(0.0 if not ranks else 1 / min(ranks))
    truth = np.asarray([row.target for row in rows], dtype=int)
    predicted = scores >= .5
    return {"validation_mrr": statistics.fmean(reciprocal),
            "validation_top_1": statistics.fmean(value == 1 for value in reciprocal),
            "validation_macro_f1": f1_score(
                truth, predicted, average="macro", zero_division=0)}


def _validate_fitted_pipeline(model: Pipeline, feature_count: int,
                              x_reference: np.ndarray) -> None:
    if model.n_features_in_ != feature_count:
        raise RuntimeError("Pipeline ajustado com largura diferente do schema.")
    transformed = model.named_steps["imputer"].transform(x_reference)
    if transformed.shape[1] != feature_count:
        raise RuntimeError("Imputer removeu colunas do feature schema.")


def tune_model(name: str, dataset: BenchmarkDataset, *, seed: int, budget: int,
               selection_metric: str = DEFAULT_SELECTION_METRIC):
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 1:
        raise ValueError("tuning budget deve ser inteiro positivo.")
    if budget > MAX_COMMON_TUNING_BUDGET:
        raise ValueError(f"tuning budget comum máximo é {MAX_COMMON_TUNING_BUDGET}.")
    x_train, y_train, _ = dataset.select("train")
    x_validation, y_validation, _ = dataset.select("validation")
    if not len(x_train) or len(set(y_train)) < 2:
        raise ValueError("Treino requer linhas e ambas as classes.")
    if selection_metric not in {"validation_mrr", "validation_top_1", "validation_macro_f1"}:
        raise ValueError("selection_metric inválida.")
    if name not in ML_MODELS:
        trials = {"tuning_budget_requested": budget, "tuning_trials_executed": 0,
                  "reason": "non-trainable-baseline", "trials": []}
        return HeuristicEstimator(name, dataset.feature_names), {}, 0.0, trials
    if not len(x_validation) or len(set(y_validation)) < 2:
        raise ValueError("Validation supervisionado requer linhas e ambas as classes.")
    base, configurations = _model_and_grid(name, seed)
    rng = random.Random(seed)
    configurations = list(configurations)
    rng.shuffle(configurations)
    chosen, best_key, best_params, total_time, trials = None, None, None, 0.0, []
    if len(configurations) < budget:
        raise RuntimeError("Espaço de hiperparâmetros menor que o orçamento comum.")
    metric_order = [selection_metric] + [item for item in (
        "validation_mrr", "validation_top_1", "validation_macro_f1")
        if item != selection_metric]
    for index, params in enumerate(configurations[:budget]):
        candidate, _ = _model_and_grid(name, seed)
        candidate.set_params(**params)
        started = time.perf_counter()
        trial = {"index": index, "parameters": params, "seed": seed}
        try:
            candidate.fit(x_train, y_train)
            duration = time.perf_counter() - started
            total_time += duration
            _validate_fitted_pipeline(candidate, len(dataset.feature_names), x_validation)
            metrics = _ranking_metrics(positive_scores(candidate, x_validation),
                                       dataset.select("validation")[2])
            trial.update({"validation_metrics": metrics, "duration_seconds": duration,
                          "status": "ok", "failure_reason": None})
            key = tuple(metrics[item] for item in metric_order)
            canonical = canonical_json(params)
            if best_key is None or key > best_key or (key == best_key and canonical < canonical_json(best_params)):
                chosen, best_key, best_params = candidate, key, params
        except Exception as exc:
            duration = time.perf_counter() - started
            total_time += duration
            trial.update({"validation_metrics": None, "duration_seconds": duration,
                          "status": "failed", "failure_reason": f"{type(exc).__name__}: {exc}"})
        trials.append(trial)
    if chosen is None:
        raise RuntimeError("Todas as trials de tuning falharam.")
    return chosen, best_params or {}, total_time, {
        "tuning_budget_requested": budget, "tuning_trials_executed": len(trials),
        "selection_metric": selection_metric, "trials": trials}


def _percentile(values, q):
    return float(np.percentile(values, q)) if len(values) else 0.0


def evaluate_scores(scores: np.ndarray, rows: tuple[BenchmarkRow, ...], *,
                    inference_seconds: float, memory_peak_bytes: int,
                    model_size_bytes: int, training_seconds: float,
                    inference_latencies_ms: Iterable[float] = ()) -> dict[str, Any]:
    if not rows:
        raise ValueError("Avaliação requer ao menos uma linha.")
    truth = np.asarray([row.target for row in rows], dtype=int)
    predicted = scores >= .5
    tn, fp, fn, tp = confusion_matrix(truth, predicted, labels=[0, 1]).ravel()
    by_incident: dict[str, list[tuple[float, BenchmarkRow]]] = {}
    for score, row in zip(scores, rows):
        by_incident.setdefault(row.incident_id, []).append((float(score), row))
    ranks, top1, top3, incident_predictions = [], 0, 0, []
    for incident, candidates in sorted(by_incident.items()):
        ranked = sorted(candidates, key=lambda item: (-item[0], item[1].service_name))
        positives = {row.service_name for _, row in ranked if row.target}
        found = [index for index, (_, row) in enumerate(ranked, 1) if row.service_name in positives]
        rank = min(found) if found else math.inf
        ranks.append(rank)
        top1 += rank <= 1
        top3 += rank <= 3
        incident_predictions.append({
            "incident_id": incident, "actual_services": sorted(positives),
            "ranking": [{"service_name": row.service_name, "score": score,
                         "is_root_cause": row.target} for score, row in ranked],
            "reciprocal_rank": 0.0 if math.isinf(rank) else 1 / rank,
        })
    latencies = tuple(inference_latencies_ms) or (
        inference_seconds * 1000 / max(1, len(rows)),
    )
    per_service_metrics = {}
    for service_name in sorted({row.service_name for row in rows}):
        indexes = [index for index, row in enumerate(rows)
                   if row.service_name == service_name]
        service_truth = truth[indexes]
        service_predicted = predicted[indexes]
        service_tn, service_fp, _, _ = confusion_matrix(
            service_truth, service_predicted, labels=[0, 1]).ravel()
        per_service_metrics[service_name] = {
            "support": len(indexes),
            "precision": precision_score(service_truth, service_predicted,
                                         zero_division=0),
            "recall": recall_score(service_truth, service_predicted,
                                    zero_division=0),
            "macro_f1": f1_score(service_truth, service_predicted,
                                 average="macro", zero_division=0),
            "fpr": service_fp / (service_fp + service_tn)
            if service_fp + service_tn else 0.0,
        }
    metrics = {
        "precision": precision_score(truth, predicted, zero_division=0),
        "recall": recall_score(truth, predicted, zero_division=0),
        "macro_f1": f1_score(truth, predicted, average="macro", zero_division=0),
        "top_1": top1 / len(by_incident), "top_3": top3 / len(by_incident),
        "mrr": statistics.fmean(0 if math.isinf(rank) else 1 / rank for rank in ranks),
        "fpr": fp / (fp + tn) if fp + tn else 0.0,
        "model_size_bytes": model_size_bytes,
        "training_seconds": training_seconds,
    }
    return {"metrics": metrics, "confusion": {"tn": int(tn), "fp": int(fp),
                                                "fn": int(fn), "tp": int(tp)},
            "per_service_metrics": per_service_metrics,
            "incident_predictions": incident_predictions}


def bootstrap_intervals(predictions: list[dict[str, Any]], *, seed: int,
                        iterations: int = 1000) -> dict[str, dict[str, float]]:
    if iterations < 1:
        raise ValueError("bootstrap iterations deve ser positivo.")
    rng = random.Random(seed)
    incidents = predictions
    samples = {name: [] for name in (
        "precision", "recall", "macro_f1", "fpr", "top_1", "top_3", "mrr")}
    for _ in range(iterations):
        drawn = [rng.choice(incidents) for _ in incidents]
        reciprocal = [item["reciprocal_rank"] for item in drawn]
        truth = np.asarray([int(candidate["is_root_cause"])
                            for item in drawn for candidate in item["ranking"]])
        predicted = np.asarray([candidate["score"] >= .5
                                for item in drawn for candidate in item["ranking"]])
        tn, fp, fn, tp = confusion_matrix(truth, predicted, labels=[0, 1]).ravel()
        samples["precision"].append(precision_score(truth, predicted, zero_division=0))
        samples["recall"].append(recall_score(truth, predicted, zero_division=0))
        samples["macro_f1"].append(f1_score(
            truth, predicted, average="macro", zero_division=0))
        samples["fpr"].append(fp / (fp + tn) if fp + tn else 0.0)
        samples["top_1"].append(statistics.fmean(value == 1 for value in reciprocal))
        samples["top_3"].append(statistics.fmean(value >= 1 / 3 for value in reciprocal))
        samples["mrr"].append(statistics.fmean(reciprocal))
    return {name: {"lower": _percentile(values, 2.5), "upper": _percentile(values, 97.5)}
            for name, values in samples.items()}


def environment_manifest() -> dict[str, Any]:
    memory = None
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                memory = int(line.split()[1]) * 1024
                break
    except OSError:
        pass
    packages = {}
    for name in ("numpy", "scikit-learn", "xgboost", "joblib"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {"platform": platform.platform(), "python": platform.python_version(),
            "processor": platform.processor(), "cpu_count": os.cpu_count(),
            "memory_total_bytes": memory, "packages": packages}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_basename(value: Any, field: str) -> str:
    if (not isinstance(value, str) or not value or Path(value).name != value
            or ".." in value or "/" in value or "\\" in value):
        raise ValueError(f"{field} deve ser basename seguro.")
    return value


def _inside(artifact: Path, filename: str) -> Path:
    root = artifact.resolve()
    target = (artifact / filename).resolve()
    if target.parent != root:
        raise ValueError("Arquivo resolvido escapa do diretório do artefato.")
    return target


@dataclass(frozen=True)
class VerifiedArtifactV3:
    path: Path
    manifest: Mapping[str, Any]
    metrics: Mapping[str, Any]
    predictions: tuple[Mapping[str, Any], ...]
    incident_predictions: tuple[NormalizedIncidentPrediction, ...]
    integrity: Mapping[str, Any]


def load_verified_artifact(path: str | Path) -> VerifiedArtifactV3:
    artifact = Path(path)
    if not artifact.is_dir():
        raise ValueError("Artefato verificado deve ser um diretório.")
    integrity_path = _inside(artifact, "run-integrity.json")
    integrity = json.loads(integrity_path.read_text(encoding="utf-8"))
    manifest_name = _safe_basename(integrity.get("manifest_file"), "manifest_file")
    hashes_name = _safe_basename(integrity.get("artifact_hashes_file"),
                                 "artifact_hashes_file")
    manifest_path = _inside(artifact, manifest_name)
    hashes_path = _inside(artifact, hashes_name)
    if _file_sha256(manifest_path) != integrity.get("manifest_payload_sha256"):
        raise ValueError("Hash de manifest-core.json diverge de run-integrity.")
    if _file_sha256(hashes_path) != integrity.get("artifact_hashes_sha256"):
        raise ValueError("Hash de artifact-hashes.json diverge de run-integrity.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    hashes = json.loads(hashes_path.read_text(encoding="utf-8"))
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError("artifact-hashes.json inválido.")
    for filename, expected_hash in hashes.items():
        safe_name = _safe_basename(filename, "artifact filename")
        target = _inside(artifact, safe_name)
        if not target.is_file() or _file_sha256(target) != expected_hash:
            raise ValueError(f"Integridade do artefato falhou para {filename}.")
    if integrity.get("artifact_hashes") != hashes:
        raise ValueError("Mapa de hashes diverge de run-integrity.")
    contract = {
        "benchmark_version": manifest.get("benchmark_version"),
        "run_id": manifest.get("run_id"), "model_name": manifest.get("model_name"),
        "dataset_sha256": manifest.get("dataset_sha256"),
        "feature_view_sha256": manifest.get("feature_view_sha256"),
        "seed": manifest.get("seed"),
        "manifest_payload_sha256": integrity.get("manifest_payload_sha256"),
        "artifact_hashes": hashes,
        "artifact_hashes_sha256": integrity.get("artifact_hashes_sha256"),
    }
    if integrity.get("run_fingerprint") != sha256(contract):
        raise ValueError("run_fingerprint diverge da raiz canônica da execução.")
    for field in ("benchmark_version", "run_id", "model_name", "dataset_sha256",
                  "feature_view_sha256", "seed"):
        if integrity.get(field) != contract[field]:
            raise ValueError(f"run-integrity diverge do manifesto em {field}.")
    if _safe_basename(manifest.get("manifest_file"), "manifest_file") != manifest_name:
        raise ValueError("manifest_file diverge entre manifesto e integridade.")
    if (_safe_basename(manifest.get("artifact_hashes_file"), "artifact_hashes_file")
            != hashes_name):
        raise ValueError("artifact_hashes_file diverge entre manifesto e integridade.")
    model_file = _safe_basename(manifest.get("model_file"), "model_file")
    if model_file not in hashes or manifest.get("model_sha256") != hashes[model_file]:
        raise ValueError("Modelo e mapa de hashes são inconsistentes.")
    feature_schema = json.loads(_inside(
        artifact, "feature-schema.json").read_text(encoding="utf-8"))
    if feature_schema != manifest.get("feature_schema"):
        raise ValueError("feature-schema.json diverge do manifesto.")
    metrics = json.loads(_inside(artifact, "metrics.json").read_text(encoding="utf-8"))
    predictions = json.loads(_inside(artifact, "predictions.json").read_text(encoding="utf-8"))
    environment = json.loads(_inside(artifact, "environment.json").read_text(encoding="utf-8"))
    hyperparameters = json.loads(_inside(artifact, "hyperparameters.json").read_text(encoding="utf-8"))
    trials = json.loads(_inside(artifact, "tuning-trials.json").read_text(encoding="utf-8"))
    confusion = json.loads(_inside(artifact, "confusion.json").read_text(encoding="utf-8"))
    if environment != manifest.get("environment") or hyperparameters != manifest.get("hyperparameters"):
        raise ValueError("Ambiente/hiperparâmetros divergem do manifesto.")
    if (trials.get("tuning_trials_executed") != manifest.get("tuning_trials_executed")
            or metrics.get("confusion") != confusion):
        raise ValueError("Trials/confusion divergem do manifesto de execução.")
    runtime_metrics = metrics.get("metrics", {})
    if (runtime_metrics.get("tuning_seconds") != manifest.get("tuning_seconds")
            or runtime_metrics.get("final_fit_seconds") != manifest.get("final_fit_seconds")
            or runtime_metrics.get("model_size_bytes") != _inside(artifact, model_file).stat().st_size):
        raise ValueError("Métricas de runtime divergem do manifesto/modelo.")
    normalized = tuple(validate_incident_prediction(item)
                       for item in metrics.get("incident_predictions", ()))
    flattened = {(item.incident_id, candidate["service_name"]):
                 (candidate["is_root_cause"], candidate["score"])
                 for item in normalized for candidate in item.ranking}
    serialized_rows = {(item.get("incident_id"), item.get("service_name")):
                       (item.get("is_root_cause"), item.get("score"))
                       for item in predictions}
    if flattened != serialized_rows or len(serialized_rows) != len(predictions):
        raise ValueError("predictions.json diverge das predições por incidente.")
    return VerifiedArtifactV3(
        artifact, _deep_freeze(manifest), _deep_freeze(metrics),
        tuple(_deep_freeze(item) for item in predictions), normalized,
        _deep_freeze(integrity))


def _measure_runtime(model, matrix: np.ndarray,
                     policy: BenchmarkRuntimePolicy) -> tuple[np.ndarray, dict[str, Any]]:
    for _ in range(policy.warmup_iterations):
        positive_scores(model, matrix)
        positive_scores(model, matrix[:1])
    if policy.collect_python_memory:
        tracemalloc.start()
    batch_samples = []
    scores = None
    for _ in range(policy.batch_repetitions):
        started = time.perf_counter_ns()
        scores = positive_scores(model, matrix)
        batch_samples.append((time.perf_counter_ns() - started) / 1_000_000_000)
    peak = 0
    if policy.collect_python_memory:
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    single_samples = []
    for _ in range(policy.single_row_repetitions):
        for row in matrix:
            started = time.perf_counter_ns()
            positive_scores(model, row.reshape(1, -1))
            single_samples.append((time.perf_counter_ns() - started) / 1_000_000)
    throughput = statistics.fmean(
        len(matrix) / max(duration, 1e-12) for duration in batch_samples)
    return scores, {
        "runtime_policy": policy.to_dict(),
        "batch_duration_samples_seconds": batch_samples,
        "single_row_latency_samples_ms": single_samples,
        "batch_throughput_rows_s": throughput,
        "single_row_p50_ms": _percentile(single_samples, 50),
        "single_row_p95_ms": _percentile(single_samples, 95),
        "single_row_p99_ms": _percentile(single_samples, 99),
        "python_tracemalloc_peak_bytes": peak,
    }


def _preprocessing_manifest(model, dataset: BenchmarkDataset,
                            x_train: np.ndarray) -> dict[str, Any]:
    if not isinstance(model, Pipeline):
        return {"policy": "not-applicable", "scaling": False,
                "feature_names": list(dataset.feature_names),
                "all_missing_train_columns": []}
    imputer = model.named_steps["imputer"]
    return {
        "policy": "median-keep-empty-features-v1",
        "strategy": imputer.strategy,
        "keep_empty_features": imputer.keep_empty_features,
        "imputation_statistics": [None if not math.isfinite(float(value)) else float(value)
                                  for value in imputer.statistics_],
        "all_missing_train_columns": [dataset.feature_names[index]
                                      for index in range(x_train.shape[1])
                                      if np.all(np.isnan(x_train[:, index]))],
        "scaling": "scale" in model.named_steps,
        "feature_names": list(dataset.feature_names),
    }


def train_and_evaluate(name: str, dataset: BenchmarkDataset, output_dir: str | Path, *,
                       seed: int = 42, tuning_budget: int = 3,
                       bootstrap_iterations: int = 1000,
                       selection_metric: str = DEFAULT_SELECTION_METRIC,
                       runtime_policy: BenchmarkRuntimePolicy | None = None,
                       overwrite: bool = False) -> dict[str, Any]:
    if name not in MODEL_NAMES:
        raise ValueError(f"Modelo inválido: {name}.")
    dataset.validate()
    for split in ("train", "validation", "test"):
        if not dataset.select(split)[2]:
            raise ValueError("Benchmark científico requer train, validation e test não vazios.")
    output = Path(output_dir) / name / dataset.dataset_hash[:12] / f"seed-{seed}"
    if output.exists():
        if not overwrite:
            raise FileExistsError(f"Diretório de execução já existe: {output}.")
        shutil.rmtree(output)
    output.mkdir(parents=True)
    model, hyperparameters, tuning_seconds, tuning = tune_model(
        name, dataset, seed=seed, budget=tuning_budget,
        selection_metric=selection_metric)
    final_fit_seconds = 0.0
    model_path = output / ("xgboost-rca-v3.joblib" if name == "xgboost-rca-v3" else f"{name}.joblib")
    joblib.dump(model, model_path)
    x_test, _, rows = dataset.select("test")
    policy = runtime_policy or BenchmarkRuntimePolicy()
    scores, performance = _measure_runtime(model, x_test, policy)
    evaluated = evaluate_scores(
        scores, rows, inference_seconds=sum(performance["batch_duration_samples_seconds"]),
        memory_peak_bytes=performance["python_tracemalloc_peak_bytes"],
        model_size_bytes=model_path.stat().st_size,
        training_seconds=tuning_seconds + final_fit_seconds,
        inference_latencies_ms=performance["single_row_latency_samples_ms"])
    evaluated["metrics"].update({
        "batch_throughput_rows_s": performance["batch_throughput_rows_s"],
        "single_row_p50_ms": performance["single_row_p50_ms"],
        "single_row_p95_ms": performance["single_row_p95_ms"],
        "single_row_p99_ms": performance["single_row_p99_ms"],
        "python_tracemalloc_peak_bytes": performance["python_tracemalloc_peak_bytes"],
        "tuning_seconds": tuning_seconds, "final_fit_seconds": final_fit_seconds})
    evaluated["runtime_measurements"] = performance
    predictions = [
        {"incident_id": row.incident_id, "service_name": row.service_name,
         "is_root_cause": row.target, "score": float(score)}
        for score, row in zip(scores, rows)
    ]
    environment = environment_manifest()
    x_train, _, _ = dataset.select("train")
    preprocessing = _preprocessing_manifest(model, dataset, x_train)
    manifest = {
        "benchmark_version": BENCHMARK_VERSION, "model_name": name,
        "run_id": f"{name}-{dataset.dataset_hash[:12]}-seed-{seed}",
        "model_version": "xgboost-rca-v3" if name == "xgboost-rca-v3" else f"{name}-v3",
        "seed": seed, "tuning_budget_requested": tuning_budget,
        "tuning_trials_executed": tuning["tuning_trials_executed"],
        "tuning_protocol": "equal-budget-validation-ranking-v1",
        "selection_metric": selection_metric, "final_fit_policy": FINAL_FIT_POLICY,
        "threshold": 0.5,
        "dataset_sha256": dataset.dataset_hash,
        "rows_sha256": dataset.feature_schema["rows_sha256"],
        "feature_view_sha256": dataset.feature_schema["feature_view_sha256"],
        "feature_schema": _thaw(dataset.feature_schema),
        "feature_names": list(dataset.feature_names), "hyperparameters": hyperparameters,
        "environment": environment, "model_file": model_path.name,
        "model_sha256": _file_sha256(model_path),
        "runtime_policy": policy.to_dict(), "preprocessing": preprocessing,
        "tuning_seconds": tuning_seconds, "final_fit_seconds": final_fit_seconds,
        "splits": {split: sorted({row.incident_id for row in dataset.rows if row.split == split})
                   for split in ("train", "validation", "test")},
    }
    evaluated["bootstrap_intervals"] = bootstrap_intervals(
        evaluated["incident_predictions"], seed=seed, iterations=bootstrap_iterations)
    for filename, payload in (
        ("feature-schema.json", _thaw(dataset.feature_schema)),
        ("metrics.json", evaluated), ("confusion.json", evaluated["confusion"]),
        ("predictions.json", predictions), ("environment.json", environment),
        ("hyperparameters.json", hyperparameters), ("tuning-trials.json", tuning),
    ):
        (output / filename).write_text(canonical_json(payload) + "\n", encoding="utf-8")
    hashed_files = [model_path.name, "feature-schema.json", "metrics.json", "confusion.json",
                    "predictions.json", "environment.json", "hyperparameters.json",
                    "tuning-trials.json"]
    artifact_hashes = {filename: _file_sha256(output / filename) for filename in hashed_files}
    manifest["artifact_hashes_file"] = "artifact-hashes.json"
    manifest["manifest_file"] = "manifest-core.json"
    manifest_path = output / "manifest-core.json"
    hashes_path = output / "artifact-hashes.json"
    manifest_path.write_text(canonical_json(manifest) + "\n", encoding="utf-8")
    hashes_path.write_text(
        canonical_json(artifact_hashes) + "\n", encoding="utf-8")
    integrity_contract = {
        "benchmark_version": BENCHMARK_VERSION, "run_id": manifest["run_id"],
        "model_name": name, "dataset_sha256": dataset.dataset_hash,
        "feature_view_sha256": dataset.feature_schema["feature_view_sha256"],
        "seed": seed, "manifest_payload_sha256": _file_sha256(manifest_path),
        "artifact_hashes": artifact_hashes,
        "artifact_hashes_sha256": _file_sha256(hashes_path),
    }
    run_integrity = dict(integrity_contract)
    run_integrity.update({"manifest_file": manifest_path.name,
                          "artifact_hashes_file": hashes_path.name,
                          "run_fingerprint": sha256(integrity_contract)})
    (output / "run-integrity.json").write_text(
        canonical_json(run_integrity) + "\n", encoding="utf-8")
    return {"artifact_dir": str(output), "manifest": manifest, **evaluated}


def evaluate_artifact(artifact_dir: str | Path, dataset: BenchmarkDataset, *,
                      bootstrap_iterations: int = 1000,
                      expected_model: str | None = None,
                      runtime_policy: BenchmarkRuntimePolicy | None = None) -> dict[str, Any]:
    verified = load_verified_artifact(artifact_dir)
    artifact = verified.path
    manifest = _thaw(verified.manifest)
    if manifest.get("benchmark_version") != BENCHMARK_VERSION:
        raise ValueError("benchmark_version incompatível.")
    if expected_model is not None and manifest.get("model_name") != expected_model:
        raise ValueError("Modelo do artefato diverge do modelo esperado.")
    model_path = _inside(artifact, _safe_basename(manifest["model_file"], "model_file"))
    if manifest.get("model_sha256") != _file_sha256(model_path):
        raise ValueError("model_sha256 diverge do modelo.")
    if manifest.get("dataset_sha256") != dataset.dataset_hash:
        raise ValueError("Dataset hash diverge do artefato treinado.")
    if manifest.get("feature_view_sha256") != dataset.feature_schema["feature_view_sha256"]:
        raise ValueError("Feature view hash diverge do artefato treinado.")
    if tuple(manifest["feature_names"]) != dataset.feature_names:
        raise ValueError("Feature schema diverge do artefato treinado.")
    if manifest.get("feature_schema") != _thaw(dataset.feature_schema):
        raise ValueError("feature-schema.json diverge integralmente do dataset atual.")
    # joblib/pickle permite execução de código: carregue somente após validar um artefato confiável.
    model = joblib.load(model_path)
    x_test, _, rows = dataset.select("test")
    policy = runtime_policy or BenchmarkRuntimePolicy()
    scores, performance = _measure_runtime(model, x_test, policy)
    report = evaluate_scores(
        scores, rows, inference_seconds=sum(performance["batch_duration_samples_seconds"]),
        memory_peak_bytes=performance["python_tracemalloc_peak_bytes"],
        model_size_bytes=model_path.stat().st_size,
        training_seconds=manifest["tuning_seconds"] + manifest["final_fit_seconds"],
        inference_latencies_ms=performance["single_row_latency_samples_ms"],
    )
    report["metrics"].update({
        "batch_throughput_rows_s": performance["batch_throughput_rows_s"],
        "single_row_p50_ms": performance["single_row_p50_ms"],
        "single_row_p95_ms": performance["single_row_p95_ms"],
        "single_row_p99_ms": performance["single_row_p99_ms"],
        "python_tracemalloc_peak_bytes": performance["python_tracemalloc_peak_bytes"],
        "tuning_seconds": manifest["tuning_seconds"],
        "final_fit_seconds": manifest["final_fit_seconds"]})
    report["runtime_measurements"] = performance
    report["manifest"] = manifest
    report["bootstrap_intervals"] = bootstrap_intervals(
        report["incident_predictions"], seed=manifest["seed"],
        iterations=bootstrap_iterations,
    )
    return report


def compare_predictions(left: Mapping[str, Any], right: Mapping[str, Any], *,
                        seed: int = 42, iterations: int = 10000) -> dict[str, Any]:
    if isinstance(iterations, bool) or not isinstance(iterations, int) or iterations < 1:
        raise ValueError("iterations deve ser inteiro positivo não booleano.")
    def indexed(report):
        items = report.get("incident_predictions", ())
        ids = [item.get("incident_id") for item in items]
        if len(ids) != len(set(ids)):
            raise ValueError("incident_id duplicado na comparação.")
        normalized = [validate_incident_prediction(item) for item in items]
        return {item.incident_id: item for item in normalized}
    left_manifest, right_manifest = left.get("manifest", {}), right.get("manifest", {})
    left_hash = left_manifest.get("dataset_sha256")
    right_hash = right_manifest.get("dataset_sha256")
    if not left_hash or left_hash != right_hash:
        raise ValueError("Comparação pareada requer o mesmo dataset hash.")
    if left_manifest.get("splits", {}).get("test") != right_manifest.get("splits", {}).get("test"):
        raise ValueError("Comparação pareada requer o mesmo split de teste.")
    left_items, right_items = indexed(left), indexed(right)
    a = {key: item.top1_correct for key, item in left_items.items()}
    b = {key: item.top1_correct for key, item in right_items.items()}
    if set(a) != set(b):
        raise ValueError("Comparação pareada requer os mesmos incidentes.")
    for key in a:
        if left_items[key].actual_services != right_items[key].actual_services:
            raise ValueError("Ground truth diverge entre resultados pareados.")
        left_candidates = {item["service_name"] for item in left_items[key].ranking}
        right_candidates = {item["service_name"] for item in right_items[key].ranking}
        if left_candidates != right_candidates:
            raise ValueError("Serviços candidatos divergem entre resultados pareados.")
    discordant_a = sum(a[key] and not b[key] for key in a)
    discordant_b = sum(b[key] and not a[key] for key in a)
    n = discordant_a + discordant_b
    p_value = 1.0 if n == 0 else min(1.0, 2 * sum(
        math.comb(n, k) * .5 ** n for k in range(0, min(discordant_a, discordant_b) + 1)))
    rng = random.Random(seed)
    differences = []
    keys = tuple(sorted(a))
    for _ in range(iterations):
        sample = [rng.choice(keys) for _ in keys]
        differences.append(statistics.fmean(float(b[key]) - float(a[key]) for key in sample))
    difference = statistics.fmean(float(b[key]) - float(a[key]) for key in keys)
    return {"left_model": left_manifest.get("model_name"),
            "right_model": right_manifest.get("model_name"),
            "dataset_sha256": left_hash, "paired_incidents": len(keys),
            "seed": seed, "bootstrap_iterations": iterations,
            "difference_direction": "right-left",
            "mcnemar_exact_p_value": p_value,
            "discordant_left_only": discordant_a, "discordant_right_only": discordant_b,
            "top1_difference": difference,
            "bootstrap_95": {"lower": _percentile(differences, 2.5),
                             "upper": _percentile(differences, 97.5)},
            "interpretation": "Diferença descritiva pareada; o p-value não implica significância automática."}


def stability_summary(results: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for result in results:
        grouped.setdefault(result["manifest"]["model_name"], []).append(result["metrics"])
    return {model: {metric: {"mean": statistics.fmean(values),
                             "stddev": statistics.pstdev(values)}
                    for metric in ("macro_f1", "top_1", "top_3", "mrr", "fpr")
                    if (values := [item[metric] for item in metrics])}
            for model, metrics in sorted(grouped.items())}
