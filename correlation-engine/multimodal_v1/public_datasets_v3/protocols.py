from __future__ import annotations

import math
import random
from typing import Iterable

from .contracts import (EvaluationProtocol, IncidentDescriptor, ProtocolPlanManifest,
                        ProtocolSplit)

PUBLIC_DATASET_PROTOCOL_VERSION = "public-dataset-protocol-v2"


class ProtocolPlanner:
    @staticmethod
    def _values(incidents: Iterable[IncidentDescriptor]) -> tuple[IncidentDescriptor, ...]:
        values = tuple(sorted(incidents, key=lambda item: item.incident_id))
        if not values: raise ValueError("Protocolo requer incidentes.")
        ids = [item.incident_id for item in values]
        if len(ids) != len(set(ids)): raise ValueError("incident_id duplicado no protocolo.")
        return values

    @classmethod
    def plan(cls, incidents: Iterable[IncidentDescriptor], protocol: EvaluationProtocol | str,
             *, seed: int = 42, test_fraction: float = .2,
             held_out_system: str | None = None,
             held_out_source: str | None = None) -> ProtocolSplit:
        values, mode = cls._values(incidents), EvaluationProtocol(protocol)
        if mode is EvaluationProtocol.WITHIN_DATASET:
            if isinstance(seed, bool) or not isinstance(seed, int):
                raise ValueError("seed deve ser inteira não booleana.")
            if isinstance(test_fraction, bool) or not isinstance(test_fraction, (int, float)) or not 0 < test_fraction < 1:
                raise ValueError("test_fraction deve estar entre 0 e 1.")
            if len(values) < 2: raise ValueError("within-dataset requer ao menos dois incidentes.")
            shuffled = list(values); random.Random(seed).shuffle(shuffled)
            size = min(len(values) - 1, max(1, math.floor(len(values) * test_fraction + .5)))
            test = {item.incident_id for item in shuffled[:size]}
            held_out = None
        elif mode is EvaluationProtocol.CROSS_SYSTEM:
            if not held_out_system: raise ValueError("cross-system requer held_out_system explícito.")
            systems = {item.system for item in values}
            if held_out_system not in systems: raise ValueError("Sistema holdout ausente.")
            held_out = held_out_system
            test = {item.incident_id for item in values if item.system == held_out}
        elif mode is EvaluationProtocol.LEAVE_ONE_SYSTEM_OUT:
            raise ValueError("Use plan_all_folds para leave-one-system-out.")
        else:
            if not held_out_source: raise ValueError("cross-source requer held_out_source explícito.")
            sources = {item.source_dataset for item in values}
            if held_out_source not in sources: raise ValueError("Fonte holdout ausente.")
            held_out = held_out_source
            test = {item.incident_id for item in values if item.source_dataset == held_out}
        train = tuple(item.incident_id for item in values if item.incident_id not in test)
        return ProtocolSplit(mode, train, tuple(sorted(test)), held_out,
                             seed if mode is EvaluationProtocol.WITHIN_DATASET else None,
                             test_fraction if mode is EvaluationProtocol.WITHIN_DATASET else None)

    @classmethod
    def plan_all_folds(cls, incidents: Iterable[IncidentDescriptor], *,
                       seed: int = 42, test_fraction: float = .2) -> ProtocolPlanManifest:
        values = cls._values(incidents)
        systems = tuple(sorted({item.system for item in values}))
        if len(systems) < 2: raise ValueError("LOSO requer ao menos dois sistemas.")
        folds = tuple(ProtocolSplit(
            EvaluationProtocol.LEAVE_ONE_SYSTEM_OUT,
            tuple(item.incident_id for item in values if item.system != system),
            tuple(item.incident_id for item in values if item.system == system), system)
            for system in systems)
        index = {item.incident_id: {"system": item.system, "source": item.source_dataset}
                 for item in values}
        return ProtocolPlanManifest(
            EvaluationProtocol.LEAVE_ONE_SYSTEM_OUT,
            tuple(item.incident_id for item in values), systems,
            tuple(sorted({item.source_dataset for item in values})), folds, index,
            None, None, PUBLIC_DATASET_PROTOCOL_VERSION)

    @classmethod
    def manifest(cls, incidents: Iterable[IncidentDescriptor],
                 protocol: EvaluationProtocol | str, **kwargs) -> ProtocolPlanManifest:
        values = cls._values(incidents)
        mode = EvaluationProtocol(protocol)
        if mode is EvaluationProtocol.LEAVE_ONE_SYSTEM_OUT:
            return cls.plan_all_folds(values, seed=kwargs.get("seed", 42),
                                      test_fraction=kwargs.get("test_fraction", .2))
        split = cls.plan(values, mode, **kwargs)
        index = {item.incident_id: {"system": item.system, "source": item.source_dataset}
                 for item in values}
        return ProtocolPlanManifest(
            mode, tuple(item.incident_id for item in values),
            tuple(sorted({item.system for item in values})),
            tuple(sorted({item.source_dataset for item in values})), (split,), index,
            split.seed, split.test_fraction, PUBLIC_DATASET_PROTOCOL_VERSION)
