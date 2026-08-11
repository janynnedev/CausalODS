from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from platform_v3.constants import INDEX_NAMES, TEMPLATE_ROOT


class TemplateCatalog:
    def __init__(self, root: Path = TEMPLATE_ROOT):
        self.root = Path(root)

    def load(self) -> dict[str, dict[str, Any]]:
        templates = {}
        for path in sorted(self.root.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or "index_patterns" not in payload or "template" not in payload:
                raise ValueError(f"Template OpenSearch inválido: {path}.")
            templates[path.stem] = payload
        if set(templates) != set(INDEX_NAMES):
            raise ValueError(
                f"Templates incompletos: esperado {sorted(INDEX_NAMES)}, recebido {sorted(templates)}."
            )
        return templates

    @staticmethod
    def mapping_field_count(template: Mapping[str, Any]) -> int:
        properties = template.get("template", {}).get("mappings", {}).get("properties", {})

        def count(items: Mapping[str, Any]) -> int:
            total = 0
            for value in items.values():
                total += 1
                nested = value.get("properties", {}) if isinstance(value, Mapping) else {}
                total += count(nested)
            return total

        return count(properties)

    def validate(self) -> dict[str, Any]:
        templates = self.load()
        counts = {name: self.mapping_field_count(value) for name, value in templates.items()}
        errors = []
        for name, payload in templates.items():
            mappings = payload["template"].get("mappings", {})
            if mappings.get("dynamic") not in (False, "strict"):
                errors.append(f"{name}: dynamic mapping não controlado")
            if counts[name] >= 1000:
                errors.append(f"{name}: mapping com {counts[name]} campos")
        return {"valid": not errors, "field_counts": counts, "errors": errors}


class OpenSearchProjectionRepository:
    def __init__(self, client: Any, catalog: TemplateCatalog | None = None):
        self.client = client
        self.catalog = catalog or TemplateCatalog()

    def provision(self) -> tuple[str, ...]:
        names = []
        for name, payload in self.catalog.load().items():
            self.client.indices.put_index_template(name=f"causalods-v3-{name}", body=payload)
            exists = getattr(self.client.indices, "exists", None)
            put_mapping = getattr(self.client.indices, "put_mapping", None)
            if callable(exists) and callable(put_mapping) and exists(index=INDEX_NAMES[name]):
                put_mapping(
                    index=INDEX_NAMES[name],
                    body={"properties": payload["template"]["mappings"]["properties"]},
                )
            names.append(name)
        return tuple(names)

    def index(self, kind: str, document_id: str, document: Mapping[str, Any]) -> Mapping[str, Any]:
        if kind not in INDEX_NAMES:
            raise KeyError(f"Tipo de projeção desconhecido: {kind}.")
        if not document_id:
            raise ValueError("document_id não pode ser vazio.")
        return self.client.index(
            index=INDEX_NAMES[kind], id=document_id, body=dict(document), refresh="wait_for",
        )

    def query(self, kind: str, field: str, value: str, *, size: int = 100) -> list[dict[str, Any]]:
        if kind not in INDEX_NAMES:
            raise KeyError(kind)
        response = self.client.search(
            index=INDEX_NAMES[kind],
            body={"size": size, "query": {"term": {field: value}}},
        )
        return [dict(item["_source"]) for item in response.get("hits", {}).get("hits", [])]

    def document_count(self, kind: str) -> int:
        exists = getattr(self.client.indices, "exists", None)
        if callable(exists) and not exists(index=INDEX_NAMES[kind]):
            return 0
        return int(self.client.count(index=INDEX_NAMES[kind]).get("count", 0))


def verify_sha256sums(root: Path) -> dict[str, Any]:
    root = Path(root).resolve()
    checksum_file = root / "SHA256SUMS"
    if not checksum_file.is_file():
        raise ValueError("Campanha sem SHA256SUMS.")
    verified = []
    for number, raw in enumerate(checksum_file.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            expected, relative = raw.split(maxsplit=1)
        except ValueError as exception:
            raise ValueError(f"Linha SHA256SUMS inválida: {number}.") from exception
        relative = relative.lstrip("*")
        path = (root / relative).resolve()
        if root not in path.parents:
            raise ValueError("SHA256SUMS contém caminho fora da campanha.")
        if not path.is_file():
            raise ValueError(f"Artefato ausente: {relative}.")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"Checksum divergente: {relative}.")
        verified.append(relative)
    if not verified:
        raise ValueError("SHA256SUMS vazio.")
    return {"valid": True, "verified_files": tuple(verified)}
