from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from typing import Iterable

from multimodal_v1.features_v3.contracts import (
    SCHEMA_VERSION, FeatureDefinition, FeatureGroup, FeatureType,
)


F, I, B = FeatureType.FLOAT, FeatureType.INTEGER, FeatureType.BOOLEAN


FEATURES = (
    FeatureDefinition("latency_mean", FeatureGroup.TRACE, F, "Média das latências observadas nos spans do serviço.", "ms"),
    FeatureDefinition("latency_p95", FeatureGroup.TRACE, F, "Percentil 95 das latências observadas nos spans do serviço.", "ms"),
    FeatureDefinition("latency_zscore", FeatureGroup.TRACE, F, "Z-score da latência média contra baseline normal de treino."),
    FeatureDefinition("error_span_ratio", FeatureGroup.TRACE, F, "Proporção de spans classificados como erro."),
    FeatureDefinition("distance_to_first_failure", FeatureGroup.TRACE, I, "Menor distância plausível no grafo até a primeira falha.", nullable=False),
    FeatureDefinition("failed_descendant_count", FeatureGroup.TRACE, I, "Quantidade de spans descendentes classificados como falha.", nullable=False),
    FeatureDefinition("log_error_count", FeatureGroup.LOG, I, "Logs classificados como anômalos pela política multimodal unificada.", nullable=False),
    FeatureDefinition("log_warn_count", FeatureGroup.LOG, I, "Logs com severidade WARN/WARNING.", nullable=False),
    FeatureDefinition("exception_present", FeatureGroup.LOG, B, "Indica presença de exceção nos logs.", nullable=False),
    FeatureDefinition("distinct_error_type_count", FeatureGroup.LOG, I, "Quantidade de tipos de erro distintos.", nullable=False),
    FeatureDefinition("novel_log_template_count", FeatureGroup.LOG, I, "Templates de log ausentes do baseline normal de treino."),
    FeatureDefinition("cpu_zscore", FeatureGroup.METRIC, F, "Z-score do último valor de CPU contra treino."),
    FeatureDefinition("memory_zscore", FeatureGroup.METRIC, F, "Z-score do último valor de memória contra treino."),
    FeatureDefinition("request_rate_delta", FeatureGroup.METRIC, F, "Última taxa de requisições menos média normal de treino."),
    FeatureDefinition("error_rate_delta", FeatureGroup.METRIC, F, "Última taxa de erros menos média normal de treino."),
    FeatureDefinition("saturation_score", FeatureGroup.METRIC, F, "Maior valor de métrica de saturação no incidente."),
    FeatureDefinition("anomalous_metric_count", FeatureGroup.METRIC, I, "Pontos métricos anômalos ou com |z-score| >= 3.", nullable=False),
    FeatureDefinition("business_failure_count", FeatureGroup.BUSINESS, I, "Eventos de negócio com desfecho de falha.", nullable=False),
    FeatureDefinition("rejected_transition_count", FeatureGroup.BUSINESS, I, "Pares consecutivos da mesma entidade cujo evento destino é rejeitado.", nullable=False),
    FeatureDefinition("unexpected_transition_count", FeatureGroup.BUSINESS, I, "Pares consecutivos da mesma entidade sem BUSINESS_TRANSITION.", nullable=False),
    FeatureDefinition("time_since_last_success", FeatureGroup.BUSINESS, F, "Tempo do fim da janela desde o último sucesso de negócio.", "ms"),
    FeatureDefinition("business_outcome_change", FeatureGroup.BUSINESS, B, "Indica mudança entre outcomes consecutivos da mesma entidade.", nullable=False),
    FeatureDefinition("service_depth", FeatureGroup.GRAPH, I, "Profundidade estrutural mínima do serviço.", nullable=False),
    FeatureDefinition("in_degree", FeatureGroup.GRAPH, I, "Grau dirigido de entrada agregado.", nullable=False),
    FeatureDefinition("out_degree", FeatureGroup.GRAPH, I, "Grau dirigido de saída agregado.", nullable=False),
    FeatureDefinition("centrality", FeatureGroup.GRAPH, F, "Centralidade normalizada no grafo de eventos.", nullable=False),
    FeatureDefinition("affected_descendant_count", FeatureGroup.GRAPH, I, "Serviços descendentes afetados.", nullable=False),
    FeatureDefinition("upstream_anomaly_count", FeatureGroup.GRAPH, I, "Eventos anômalos alcançáveis a montante.", nullable=False),
    FeatureDefinition("downstream_anomaly_count", FeatureGroup.GRAPH, I, "Eventos anômalos de qualquer modalidade alcançáveis a jusante.", nullable=False),
    FeatureDefinition("event_chain_count", FeatureGroup.GRAPH, I, "Eventos alcançáveis por caminhos plausíveis.", nullable=False),
    FeatureDefinition("pruning_survival", FeatureGroup.GRAPH, B, "Indica sobrevivência no CandidatePruner; null quando não executado."),
    FeatureDefinition("missing_trace", FeatureGroup.QUALITY, B, "Serviço sem eventos trace.", nullable=False),
    FeatureDefinition("missing_logs", FeatureGroup.QUALITY, B, "Serviço sem eventos log.", nullable=False),
    FeatureDefinition("missing_metrics", FeatureGroup.QUALITY, B, "Serviço sem eventos metric.", nullable=False),
    FeatureDefinition("missing_business_events", FeatureGroup.QUALITY, B, "Serviço sem business_event.", nullable=False),
    FeatureDefinition("span_loss_ratio", FeatureGroup.QUALITY, F, "Perda estimada por expected_span_count; null sem expectativa."),
    FeatureDefinition("log_loss_ratio", FeatureGroup.QUALITY, F, "Perda estimada por expected_log_count; null sem expectativa."),
    FeatureDefinition("metric_delay_ms", FeatureGroup.QUALITY, F, "Maior late_arrival_ms entre métricas; null sem métricas."),
    FeatureDefinition("uninstrumented_neighbor_count", FeatureGroup.QUALITY, I, "Vizinhos conhecidos sem eventos no bundle; null sem topologia."),
    FeatureDefinition("topology_staleness_score", FeatureGroup.QUALITY, F, "Fração de vizinhos topológicos sem instrumentação; null sem topologia."),
)


@dataclass(frozen=True)
class FeatureRegistryV3:
    definitions: tuple[FeatureDefinition, ...] = FEATURES
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("Versão inválida do Feature Registry.")
        if self.definitions != FEATURES:
            raise ValueError("rca-features-v3 aceita somente definições canônicas na ordem canônica.")

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.definitions)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.to_json().encode()).hexdigest()

    def select(self, *, include_groups: Iterable[FeatureGroup | str] | None = None,
               exclude_groups: Iterable[FeatureGroup | str] = ()) -> "FeatureViewV3":
        included = None if include_groups is None else {FeatureGroup(item) for item in include_groups}
        excluded = {FeatureGroup(item) for item in exclude_groups}
        definitions = tuple(
            item for item in self.definitions
            if (included is None or item.group in included) and item.group not in excluded
        )
        return FeatureViewV3(definitions)

    def manifest(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "unit_of_analysis": ["incident_id", "service_name"],
            "missing_value": None,
            "model_missing_value": "NaN",
            "features": [item.to_dict() for item in self.definitions],
        }

    def to_json(self) -> str:
        return json.dumps(self.manifest(), sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class FeatureViewV3:
    definitions: tuple[FeatureDefinition, ...]

    def __post_init__(self) -> None:
        canonical = {item.name: item for item in FEATURES}
        if not self.definitions or any(canonical.get(item.name) != item for item in self.definitions):
            raise ValueError("Feature view deve ser subconjunto canônico não vazio.")
        positions = [tuple(canonical).index(item.name) for item in self.definitions]
        if positions != sorted(positions):
            raise ValueError("Feature view deve preservar a ordem canônica.")

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.definitions)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.to_json().encode()).hexdigest()

    @property
    def ablation_id(self) -> str:
        excluded = [item.name for item in FEATURES if item.name not in self.names]
        return "none" if not excluded else f"ablation-{hashlib.sha256(','.join(excluded).encode()).hexdigest()[:16]}"

    def to_json(self) -> str:
        return json.dumps({"base_schema_version": SCHEMA_VERSION, "ablation_id": self.ablation_id,
                           "features": [item.to_dict() for item in self.definitions]},
                          sort_keys=True, separators=(",", ":"), allow_nan=False)
