# Procedimento pós-campanha

## Critérios de aceitação da campanha

Antes de analisar resultados, confirme:

```text
status.state = completed
status.errors = []
v1.actual_rows = 250
v2.actual_rows = 250
v1 e v2 possuem 50 ocorrências de cada cenário
final_runtime = v2
```

Valide os checksums no diretório da campanha:

```bash
sha256sum -c SHA256SUMS
```

## Validação comparativa

```bash
python3 experiments/compare_e2e_results.py \
  --campaign-dir results/e2e/final/final-v1-v2-50
```

Não use `--allow-incomplete` para resultados do artigo. Essa opção existe somente para depuração de campanhas interrompidas.

## Exportação dos RCAResults

O JSON produzido pelo runner contém a decisão final, mas não contém todas as evidências temporais e de anomalia. Por isso, os baselines exigem exportação dos documentos completos.

```bash
python3 experiments/export_rca_evidence.py \
  --campaign-dir results/e2e/final/final-v1-v2-50 \
  --opensearch-url http://localhost:9200
```

A exportação deve acontecer antes de apagar índices ou volumes do OpenSearch.

## Execução dos baselines

```bash
python3 experiments/run_baselines.py \
  --input results/e2e/final/final-v1-v2-50/baseline-input/rca-documents.json
```

## Relatório final

```bash
python3 experiments/build_experimental_report.py \
  --campaign-dir results/e2e/final/final-v1-v2-50
```

## Arquivamento

Inclua no pacote final:

- resultados v1 e v2;
- documentos completos exportados;
- resultados dos baselines;
- relatório consolidado;
- metadados Docker e Git;
- modelos e manifestos ou seus hashes;
- scripts efetivamente usados;
- `SHA256SUMS`.
