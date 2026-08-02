#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_INDEX = "causalods-rca-results"
DEFAULT_VERSIONS = ("v1", "v2")


class ExportError(RuntimeError):
    """Erro de validação ou comunicação durante a exportação."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Exporta do OpenSearch os RCAResults completos referenciados "
            "pelos JSONs de uma campanha E2E."
        )
    )
    parser.add_argument(
        "--campaign-dir",
        type=Path,
        required=True,
        help="Diretório da campanha, contendo v1/ e v2/.",
    )
    parser.add_argument(
        "--opensearch-url",
        default="http://localhost:9200",
        help="URL base do OpenSearch.",
    )
    parser.add_argument(
        "--index",
        default=DEFAULT_INDEX,
        help=f"Índice dos RCAResults. Padrão: {DEFAULT_INDEX}",
    )
    parser.add_argument(
        "--versions",
        nargs="+",
        choices=DEFAULT_VERSIONS,
        default=list(DEFAULT_VERSIONS),
        help="Versões que serão exportadas.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Arquivo JSON de saída. Padrão: "
            "<campaign-dir>/baseline-input/rca-documents.json"
        ),
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Quantidade de IDs por chamada _mget.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Timeout HTTP em segundos.",
    )
    parser.add_argument(
        "--username",
        default=os.getenv("OPENSEARCH_USERNAME"),
        help="Usuário do OpenSearch, quando houver autenticação.",
    )
    parser.add_argument(
        "--password",
        default=os.getenv("OPENSEARCH_PASSWORD"),
        help="Senha do OpenSearch, quando houver autenticação.",
    )
    return parser.parse_args()


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except json.JSONDecodeError as exc:
        raise ExportError(f"JSON inválido em {path}: {exc}") from exc


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=False,
    ) + "\n"

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())

    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index:index + size]


def result_file_score(path: Path) -> tuple[int, float, str]:
    name = path.name.lower()
    score = 0

    if "all-repeat50" in name:
        score += 100
    elif "all-repeat" in name:
        score += 80

    if name.endswith(".json"):
        score += 10

    return score, path.stat().st_mtime, path.name


def find_result_file(version_dir: Path) -> Path:
    candidates = [
        path
        for path in version_dir.glob("*.json")
        if path.is_file()
        and path.name not in {"summary.json", "status.json"}
    ]

    if not candidates:
        raise FileNotFoundError(
            f"Nenhum JSON de resultados encontrado em {version_dir}"
        )

    candidates.sort(key=result_file_score, reverse=True)
    selected = candidates[0]

    payload = read_json(selected)
    if not isinstance(payload, list):
        raise ExportError(
            f"O arquivo selecionado não contém uma lista: {selected}"
        )

    return selected


def load_campaign_rows(
    campaign_dir: Path,
    versions: list[str],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    rows: list[dict[str, Any]] = []
    sources: dict[str, str] = {}
    seen_result_ids: set[str] = set()

    for version in versions:
        version_dir = campaign_dir / version
        if not version_dir.is_dir():
            raise FileNotFoundError(
                f"Diretório da versão não encontrado: {version_dir}"
            )

        source_file = find_result_file(version_dir)
        sources[version] = str(source_file)

        payload = read_json(source_file)
        if not isinstance(payload, list):
            raise ExportError(f"{source_file} não contém uma lista")

        for position, row in enumerate(payload, start=1):
            if not isinstance(row, dict):
                raise ExportError(
                    f"Linha {position} inválida em {source_file}"
                )

            result_id = row.get("rca_result_id")
            if not result_id:
                raise ExportError(
                    "RCAResult ausente: "
                    f"version={version}; "
                    f"scenario={row.get('scenario')}; "
                    f"iteration={row.get('iteration')}"
                )

            result_id = str(result_id)
            if result_id in seen_result_ids:
                raise ExportError(
                    f"rca_result_id duplicado na campanha: {result_id}"
                )
            seen_result_ids.add(result_id)

            runtime = row.get("runtime_model_version")
            if runtime and runtime != version:
                raise ExportError(
                    f"Runtime divergente em {result_id}: "
                    f"esperado={version}; encontrado={runtime}"
                )

            rows.append(
                {
                    "source_version": version,
                    "campaign_row": row,
                }
            )

    if not rows:
        raise ExportError("A campanha não contém linhas para exportar")

    return rows, sources


def authorization_header(
    username: str | None,
    password: str | None,
) -> dict[str, str]:
    if not username and not password:
        return {}

    if not username or password is None:
        raise ExportError(
            "Informe username e password juntos para autenticação."
        )

    token = base64.b64encode(
        f"{username}:{password}".encode("utf-8")
    ).decode("ascii")

    return {"Authorization": f"Basic {token}"}


def post_json(
    url: str,
    payload: dict[str, Any],
    *,
    timeout: float,
    username: str | None,
    password: str | None,
) -> dict[str, Any]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        **authorization_header(username, password),
    }

    request = Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise ExportError(
            f"OpenSearch retornou HTTP {exc.code} em {url}: {body}"
        ) from exc
    except URLError as exc:
        raise ExportError(
            f"Falha ao acessar OpenSearch em {url}: {exc}"
        ) from exc

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ExportError(
            f"Resposta não JSON recebida de {url}: {body[:500]}"
        ) from exc

    if not isinstance(parsed, dict):
        raise ExportError(f"Resposta inesperada de {url}")

    return parsed


def export_documents(
    campaign_rows: list[dict[str, Any]],
    *,
    opensearch_url: str,
    index: str,
    batch_size: int,
    timeout: float,
    username: str | None,
    password: str | None,
) -> list[dict[str, Any]]:
    by_id = {
        str(item["campaign_row"]["rca_result_id"]): item
        for item in campaign_rows
    }

    endpoint = (
        f"{opensearch_url.rstrip('/')}/{index}/_mget"
    )
    fetched: dict[str, dict[str, Any]] = {}

    ids = list(by_id)
    total_batches = (len(ids) + batch_size - 1) // batch_size

    for batch_number, batch in enumerate(
        chunks(ids, batch_size),
        start=1,
    ):
        print(
            f"Buscando lote {batch_number}/{total_batches} "
            f"({len(batch)} documentos)..."
        )

        response = post_json(
            endpoint,
            {"ids": batch},
            timeout=timeout,
            username=username,
            password=password,
        )

        documents = response.get("docs")
        if not isinstance(documents, list):
            raise ExportError(
                "Resposta _mget sem o campo 'docs'."
            )

        for document in documents:
            if not isinstance(document, dict):
                raise ExportError(
                    "Documento inválido na resposta _mget."
                )

            document_id = str(document.get("_id"))
            if not document.get("found"):
                raise ExportError(
                    "RCAResult não encontrado no OpenSearch: "
                    f"{document_id}"
                )

            source = document.get("_source")
            if not isinstance(source, dict):
                raise ExportError(
                    f"_source inválido para {document_id}"
                )

            fetched[document_id] = source

    missing = sorted(set(ids) - set(fetched))
    if missing:
        raise ExportError(
            f"{len(missing)} documentos não foram retornados: "
            + ", ".join(missing[:10])
        )

    output: list[dict[str, Any]] = []

    for item in campaign_rows:
        version = item["source_version"]
        row = item["campaign_row"]
        result_id = str(row["rca_result_id"])
        source = fetched[result_id]

        payload = source.get("payload")
        if not isinstance(payload, dict):
            raise ExportError(
                f"payload ausente ou inválido no RCAResult {result_id}"
            )

        expected_trace = row.get("trace_id")
        document_trace = source.get("trace_id")
        if document_trace != expected_trace:
            raise ExportError(
                f"trace_id divergente no RCAResult {result_id}: "
                f"campanha={expected_trace}; "
                f"documento={document_trace}"
            )

        expected_pipeline = f"causalods-rca-{version}"
        document_pipeline = source.get("pipeline_version")
        if (
            document_pipeline
            and document_pipeline != expected_pipeline
        ):
            raise ExportError(
                f"pipeline_version divergente em {result_id}: "
                f"esperado={expected_pipeline}; "
                f"encontrado={document_pipeline}"
            )

        output.append(
            {
                "source_version": version,
                "scenario": row.get("scenario"),
                "iteration": row.get("iteration"),
                "run_id": row.get("run_id"),
                "trace_id": expected_trace,
                "rca_result_id": result_id,
                "ground_truth": row.get("fault_target"),
                "campaign_row": row,
                "rca_document": source,
            }
        )

    output.sort(
        key=lambda item: (
            item["source_version"],
            str(item["scenario"]),
            int(item["iteration"] or 0),
        )
    )
    return output


def main() -> int:
    args = parse_args()

    campaign_dir = args.campaign_dir.expanduser().resolve()
    if not campaign_dir.is_dir():
        raise FileNotFoundError(
            f"Campanha não encontrada: {campaign_dir}"
        )

    if args.batch_size < 1:
        raise ValueError("--batch-size deve ser maior que zero")

    if args.timeout <= 0:
        raise ValueError("--timeout deve ser maior que zero")

    output_path = (
        args.output.expanduser().resolve()
        if args.output
        else campaign_dir
        / "baseline-input"
        / "rca-documents.json"
    )

    versions = list(args.versions)
    campaign_rows, source_files = load_campaign_rows(
        campaign_dir,
        versions,
    )

    print(f"Campanha: {campaign_dir}")
    print(f"Versões: {', '.join(versions)}")
    print(f"Linhas encontradas: {len(campaign_rows)}")
    for version, source in source_files.items():
        print(f"  {version}: {source}")

    exported = export_documents(
        campaign_rows,
        opensearch_url=args.opensearch_url,
        index=args.index,
        batch_size=args.batch_size,
        timeout=args.timeout,
        username=args.username,
        password=args.password,
    )

    payload = {
        "schema_version": "1.0",
        "campaign_directory": str(campaign_dir),
        "opensearch_index": args.index,
        "versions": versions,
        "source_files": source_files,
        "campaign_row_count": len(campaign_rows),
        "document_count": len(exported),
        "documents": exported,
    }

    atomic_write_json(output_path, payload)

    checksum_path = output_path.with_suffix(
        output_path.suffix + ".sha256"
    )
    checksum = sha256_file(output_path)
    checksum_path.write_text(
        f"{checksum}  {output_path.name}\n",
        encoding="utf-8",
    )

    print()
    print(f"Exportação concluída: {output_path}")
    print(f"Documentos exportados: {len(exported)}")
    print(f"SHA-256: {checksum_path}")

    if len(exported) != len(campaign_rows):
        raise ExportError(
            "Quantidade exportada difere da quantidade da campanha."
        )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        ExportError,
        FileNotFoundError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        raise SystemExit(2)
