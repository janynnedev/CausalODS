from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from platform_v3.documents import VisualizationDocumentAdapter
from platform_v3.opensearch import OpenSearchProjectionRepository, verify_sha256sums


class ExperimentArtifactImporter:
    """Importa a projeção consultável; os artefatos originais continuam canônicos."""

    def __init__(
        self,
        repository: OpenSearchProjectionRepository | None = None,
        adapter: VisualizationDocumentAdapter | None = None,
    ):
        self.repository = repository
        self.adapter = adapter or VisualizationDocumentAdapter()

    def load(self, campaign_root: Path) -> tuple[dict[str, Any], ...]:
        root = Path(campaign_root).resolve()
        verify_sha256sums(root)
        manifest_path = root / "campaign-manifest.json"
        if not manifest_path.is_file():
            raise ValueError("campaign-manifest.json ausente.")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        identity = manifest.get("identity", {})
        if identity.get("schema_version") != "campaign-manifest-v3":
            raise ValueError("schema_version da campanha inválida.")
        campaign_id = identity.get("campaign_id")
        if not campaign_id:
            raise ValueError("campaign_id ausente.")
        documents = []
        run_ids = set()
        for path in sorted((root / "runs").glob("*.json")):
            if path.name == "index.json":
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("schema_version") != "experiment-run-v3":
                raise ValueError(f"Run com schema inválido: {path.name}.")
            run_identity = payload.get("identity", {})
            if run_identity.get("campaign_id") != campaign_id:
                raise ValueError(f"campaign_id divergente: {path.name}.")
            run_id = run_identity.get("run_id")
            if run_id in run_ids:
                raise ValueError(f"run_id duplicado: {run_id}.")
            if path.stem != run_id:
                raise ValueError(f"Nome de arquivo não corresponde ao run_id: {path.name}.")
            run_ids.add(run_id)
            flattened = {
                "run_id": run_id,
                "hypothesis": run_identity.get("hypothesis"),
                "experiment_family": run_identity.get("experiment_family"),
                "protocol": run_identity.get("protocol"),
                "dataset": payload.get("input", {}).get("dataset"),
                "system": payload.get("input", {}).get("system"),
                "model": payload.get("configuration", {}).get("method"),
                "feature_view": payload.get("configuration", {}).get("graph_mode"),
                "degradation": {
                    "operator": payload.get("configuration", {}).get("degradation_operator"),
                    "severity": payload.get("configuration", {}).get("degradation_severity"),
                },
                "explanation_mode": payload.get("configuration", {}).get("explanation_mode"),
                "status": payload.get("status", {}).get("state"),
                "started_at": payload.get("status", {}).get("started_at"),
                "completed_at": payload.get("status", {}).get("finished_at"),
                "metrics": payload.get("evaluation", {}),
                "provenance": payload.get("provenance", {}),
            }
            documents.append(self.adapter.experiment(flattened, campaign_id=campaign_id))
        index_path = root / "runs" / "index.json"
        if index_path.is_file():
            index = json.loads(index_path.read_text(encoding="utf-8"))
            indexed_ids = {
                item.get("run_id") for item in index.get("runs", [])
                if isinstance(item, dict)
            }
            if indexed_ids and indexed_ids != run_ids:
                raise ValueError("runs/index.json diverge dos runs importados.")
        return tuple(documents)

    @staticmethod
    def validate_derived_outputs(campaign_root: Path) -> dict[str, Any]:
        """Valida os agregados canônicos sem recalculá-los ou reclassificá-los."""
        root = Path(campaign_root).resolve()
        csv_files = sorted(path for path in root.rglob("*.csv") if path.is_file())
        json_files = sorted(
            path for path in (
                root / "experiment-plan.json",
                root / "paper/summary-metrics.json",
                root / "statistics/paired-comparisons.json",
            )
            if path.is_file()
        )
        markdown_files = sorted(path for path in (root / "paper").glob("*.md") if path.is_file())
        for path in csv_files:
            with path.open(encoding="utf-8", newline="") as stream:
                reader = csv.DictReader(stream)
                if not reader.fieldnames:
                    raise ValueError(f"CSV agregado sem header: {path.relative_to(root)}.")
                tuple(reader)
        for path in json_files:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, (dict, list)):
                raise ValueError(f"JSON agregado inválido: {path.relative_to(root)}.")
        for path in markdown_files:
            if not path.read_text(encoding="utf-8").strip():
                raise ValueError(f"Resumo vazio: {path.relative_to(root)}.")
        return {
            "csv_files_validated": len(csv_files),
            "json_files_validated": len(json_files),
            "summary_files_validated": len(markdown_files),
            "canonical_values_recomputed": False,
        }

    def import_campaign(self, campaign_root: Path) -> dict[str, Any]:
        if self.repository is None:
            raise RuntimeError("Importação persistente requer repository.")
        documents = self.load(campaign_root)
        derived_outputs = self.validate_derived_outputs(campaign_root)
        for document in documents:
            self.repository.index("experiment_runs", document["run_id"], document)
        return {
            "campaign_id": documents[0]["campaign_id"] if documents else None,
            "indexed": len(documents),
            "idempotency_key": "run_id",
            "derived_outputs": derived_outputs,
        }
