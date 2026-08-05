from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from multimodal_v1.evidence.contracts import EventReference
from multimodal_v1.event_graph.contracts import (
    EdgeClassification, EventEvidenceGraph, PruningExclusion, PruningResult,
)
from multimodal_v1.event_graph.paths import GraphPathExtractor


@dataclass(frozen=True)
class PruningPolicy:
    version: str = "candidate-pruning-v1"
    path_classifications: tuple[EdgeClassification, ...] = (
        EdgeClassification.STRUCTURAL,
        EdgeClassification.SEMANTIC,
        EdgeClassification.CAUSAL_CANDIDATE,
    )


class CandidatePruner:
    def __init__(self, *, enabled: bool = True, policy: PruningPolicy | None = None):
        self.enabled = enabled
        self.policy = policy or PruningPolicy()

    def prune(self, graph: EventEvidenceGraph, candidate_services: Iterable[str],
              symptom_references: Iterable[EventReference]) -> PruningResult:
        if isinstance(candidate_services, (str, bytes)):
            raise TypeError("candidate_services deve ser coleção de strings.")
        raw_candidates = tuple(candidate_services)
        if any(not isinstance(service, str) or not service.strip() for service in raw_candidates):
            raise ValueError("Cada candidato deve ser string não vazia.")
        candidates = tuple(sorted(set(raw_candidates)))
        symptoms = set(symptom_references)
        if not symptoms:
            raise ValueError("symptom_references não pode ser vazio.")
        if any(not isinstance(reference, EventReference) for reference in symptoms):
            raise TypeError("symptom_references deve conter EventReference.")
        provenance = (
            graph.context.incident_id, graph.context.workflow_key,
            graph.graph_fingerprint, self.policy.version,
            tuple(sorted(symptoms, key=lambda item: (item.event_id, item.source_reference or ""))),
        )
        known = {node.reference for node in graph.nodes}
        if not symptoms <= known:
            raise KeyError("Sintoma não pertence ao grafo.")
        if not self.enabled:
            return PruningResult(
                candidates, len(candidates), len(candidates), (), False,
                *provenance,
                mode="disabled_ablation",
            )
        by_service = {}
        for node in graph.nodes:
            if node.event.service_name:
                by_service.setdefault(node.event.service_name, []).append(node.reference)
        paths = GraphPathExtractor(graph)
        kept, exclusions = [], []
        for service in candidates:
            references = by_service.get(service, ())
            plausible = any(
                source == symptom or paths.shortest_path(
                    source, symptom, classifications=self.policy.path_classifications
                )
                for source in references for symptom in symptoms
            )
            if plausible:
                kept.append(service)
            else:
                rule = "no_event_node" if not references else "no_plausible_non_temporal_path_to_symptom"
                exclusions.append(PruningExclusion(service, f"{self.policy.version}:{rule}"))
        return PruningResult(
            tuple(kept), len(candidates), len(kept), tuple(exclusions), True,
            *provenance,
        )

    def audit_pruning_with_ground_truth(
        self, graph: EventEvidenceGraph, candidate_services: Iterable[str],
        symptom_references: Iterable[EventReference], *, ground_truth_service: str,
        evaluation_mode: bool = False,
    ) -> PruningResult:
        if not evaluation_mode:
            raise ValueError("ground truth exige evaluation_mode=True.")
        production = self.prune(graph, candidate_services, symptom_references)
        if ground_truth_service in production.candidates:
            return PruningResult(
                production.candidates, production.before_count, production.after_count,
                production.exclusions, production.enabled, production.incident_id,
                production.workflow_key, production.graph_fingerprint,
                production.policy_version, production.symptom_references, (),
                "evaluation_audit",
            )
        candidates = tuple(sorted((*production.candidates, ground_truth_service)))
        exclusion = PruningExclusion(
            ground_truth_service, f"{self.policy.version}:ground_truth_protected_evaluation_only"
        )
        audit_exclusions = tuple(
            item for item in production.exclusions
            if item.service_name != ground_truth_service
        ) + (exclusion,)
        return PruningResult(
            candidates, production.before_count, len(candidates),
            audit_exclusions, production.enabled,
            production.incident_id, production.workflow_key,
            production.graph_fingerprint, production.policy_version,
            production.symptom_references, (ground_truth_service,),
            "evaluation_audit",
        )
