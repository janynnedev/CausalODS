# CausalODS — Análise pós-campanha v2

Este pacote implementa a fase pós-campanha do projeto:

1. validação e comparação automática de `v1 × v2`;
2. estatística pareada por cenário e iteração;
3. exportação dos RCAResults completos do OpenSearch;
4. execução de três baselines auditáveis;
5. comparação pareada baseline × CausalODS;
6. geração de relatórios e tabelas reprodutíveis.

Todos os scripts usam somente a biblioteca padrão do Python.

## Estrutura a copiar para o repositório

```text
experiments/
├── analysis_utils.py
├── compare_e2e_results.py
├── export_rca_evidence.py
├── run_baselines.py
├── post_campaign_analysis.py
├── build_experimental_report.py
├── baselines/
│   ├── __init__.py
│   ├── contracts.py
│   └── selectors.py
└── tests/
    ├── test_compare_e2e_results.py
    └── test_baselines.py

docs/
├── post-campaign-analysis.md
├── baseline-methodology.md
└── experimental-results-template.md
```

## Instalação

Na raiz do CausalODS, copie os diretórios `experiments/` e `docs/` preservando a estrutura.

Valide a sintaxe:

```bash
python3 -m py_compile \
  experiments/analysis_utils.py \
  experiments/compare_e2e_results.py \
  experiments/export_rca_evidence.py \
  experiments/run_baselines.py \
  experiments/post_campaign_analysis.py \
  experiments/build_experimental_report.py \
  experiments/baselines/*.py
```

Execute os testes:

```bash
python3 -m unittest discover \
  -s experiments/tests \
  -v
```

Resultado esperado:

```text
Ran 7 tests
OK
```

## Desenvolvimento usando o smoke test

O comparador pode ser desenvolvido e executado sem Docker ou OpenSearch. Basta copiar uma campanha contendo:

```text
orchestration-smoke/
├── status.json
├── v1/all-repeat1.json
└── v2/all-repeat1.json
```

Execute:

```bash
python3 experiments/compare_e2e_results.py \
  --campaign-dir results/e2e/final/orchestration-smoke \
  --bootstrap-samples 1000
```

Saídas:

```text
comparison/
├── run_level_metrics.csv
├── scenario_metrics.csv
├── overall_metrics.csv
├── confidence_summary.csv
├── reliability_summary.csv
├── incident_summary.csv
├── duration_summary.csv
├── provenance_validation.json
├── statistical_tests.json
├── comparison.json
├── report.md
└── SHA256SUMS
```

## Fluxo após a campanha final

### 1. Comparar v1 e v2

```bash
python3 experiments/compare_e2e_results.py \
  --campaign-dir results/e2e/final/final-v1-v2-50
```

### 2. Exportar evidências completas

Este comando deve ser executado em uma máquina que tenha acesso ao OpenSearch que armazenou a campanha:

```bash
python3 experiments/export_rca_evidence.py \
  --campaign-dir results/e2e/final/final-v1-v2-50 \
  --opensearch-url http://localhost:9200
```

Saída:

```text
results/e2e/final/final-v1-v2-50/
└── baseline-input/
    └── rca-documents.json
```

O exportador usa `_mget` por `rca_result_id`, evitando selecionar o documento errado por horário ou por `trace_id`.

### 3. Transferir para o computador de análise

Copie pelo menos:

```text
final-v1-v2-50/
├── status.json
├── v1/all-repeat50.json
├── v2/all-repeat50.json
└── baseline-input/rca-documents.json
```

Os CSVs, logs, metadados e `SHA256SUMS` também devem ser preservados no pacote científico final.

### 4. Executar os baselines

```bash
python3 experiments/run_baselines.py \
  --input \
  results/e2e/final/final-v1-v2-50/baseline-input/rca-documents.json \
  --output-dir \
  results/e2e/final/final-v1-v2-50/baselines
```

Saídas:

```text
baselines/
├── baseline_results.json
├── baseline_results.csv
├── baseline_metrics.json
├── baseline_overall_metrics.csv
├── baseline_scenario_metrics.csv
├── statistical_tests.json
├── report.md
└── SHA256SUMS
```

### 5. Gerar o relatório consolidado

```bash
python3 experiments/build_experimental_report.py \
  --campaign-dir results/e2e/final/final-v1-v2-50
```

Saídas:

```text
final-analysis/
├── report.md
├── report-data.json
└── SHA256SUMS
```

### 6. Executar tudo em sequência

Na máquina que possui acesso ao OpenSearch:

```bash
python3 experiments/post_campaign_analysis.py \
  --campaign-dir results/e2e/final/final-v1-v2-50 \
  --opensearch-url http://localhost:9200
```

Na máquina de análise, usando um arquivo já exportado:

```bash
python3 experiments/post_campaign_analysis.py \
  --campaign-dir results/e2e/final/final-v1-v2-50 \
  --skip-export \
  --baseline-input \
  results/e2e/final/final-v1-v2-50/baseline-input/rca-documents.json
```

Depois execute o gerador consolidado:

```bash
python3 experiments/build_experimental_report.py \
  --campaign-dir results/e2e/final/final-v1-v2-50
```

## Métricas implementadas

### Por execução

- Top-1 correto;
- decisão de confiabilidade correta;
- decisão de criar incidente correta;
- causa raiz do incidente correta;
- família HTTP correta;
- duração mínima de latência correta;
- criação de exatamente um RCAResult;
- delta de incidentes correto;
- ausência de reinicialização;
- proveniência de runtime/modelo/pipeline.

### Por cenário e versão

- total de execuções;
- pass rate;
- Top-1 accuracy fault-only;
- reliability accuracy;
- incident decision accuracy;
- incident root-cause accuracy;
- falsos positivos em `healthy`;
- falsos negativos nas falhas;
- confiança média, mediana, desvio padrão e percentis;
- duração da requisição e do cenário;
- IC95% de proporções pelo intervalo de Wilson.

### Estatística pareada

O pareamento usa:

```text
(scenario, iteration)
```

- McNemar exato bicaudal para métricas booleanas;
- bootstrap pareado da diferença média `v2 − v1` para confiança e durações;
- McNemar baseline × CausalODS sobre os mesmos traces.

## Regras metodológicas incorporadas

- `healthy` não participa da Top-1 accuracy, pois não possui causa raiz real;
- no `healthy`, são medidos falso positivo e ausência de incidente;
- abstenção de baseline em cenário de falha conta como erro;
- abstenção no saudável conta como comportamento correto;
- baselines usam evidências pré-modelo, não o ranking final;
- a comparação pareada usa o mesmo cenário e iteração;
- proveniência incompatível causa falha de validação por padrão.

## Próximas fases após este pacote

Depois de obter os resultados finais:

1. revisar outliers e execuções reprovadas;
2. verificar se há diferença entre falhas de erro e de latência;
3. selecionar tabelas e figuras para o artigo;
4. adicionar um dataset ou aplicação pública para validade externa;
5. executar testes de robustez isolados da campanha principal;
6. implementar a camada de explicação textual sem permitir que o LLM altere o `RCAResult`.


## Compatibilidade desta revisão

Esta revisão inclui o exportador autônomo validado para o formato real da campanha final. O instalador preserva backups dos arquivos existentes em `.causalods-backup/<timestamp>/` e usa o Python ativo no ambiente virtual quando disponível.

O comparador foi validado com os arquivos reais de 250 execuções da v1 e 250 da v2, reproduzindo `150/250` para v1 e `250/250` para v2.
