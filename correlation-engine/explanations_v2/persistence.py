from __future__ import annotations

from dataclasses import dataclass, field

from explanations_v2.contracts import ExplanationProvenanceRecordV1


class ExplanationProvenanceRepository:
    def save(self, record: ExplanationProvenanceRecordV1) -> ExplanationProvenanceRecordV1:
        raise NotImplementedError


@dataclass
class InMemoryExplanationProvenanceRepository(ExplanationProvenanceRepository):
    records: tuple[ExplanationProvenanceRecordV1, ...] = field(default_factory=tuple)

    def save(self, record: ExplanationProvenanceRecordV1) -> ExplanationProvenanceRecordV1:
        self.records = self.records + (record,)
        return record
