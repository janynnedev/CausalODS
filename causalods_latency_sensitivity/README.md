# Próxima etapa do CausalODS: Sensibilidade à Latência

Este pacote implementa a campanha complementar de sensibilidade à intensidade
da latência.

## Pré-requisitos

O repositório precisa conter:

- `experiments/e2e_runner.py`;
- `experiments/e2e_campaign.py`;
- Docker Compose funcional;
- modelos v1 e v2;
- `.venv` com as dependências do projeto.

## Instalação

Na raiz do pacote:

```bash
./install.sh /workspaces/CausalODS
```

Depois:

```bash
cd /workspaces/CausalODS

python -m py_compile \
  experiments/latency_sensitivity_campaign.py \
  experiments/analyze_latency_sensitivity.py

python -m pytest \
  experiments/tests/test_latency_sensitivity_analysis.py \
  -q
```

## Smoke test

Use poucas condições:

```bash
python \
  experiments/latency_sensitivity_campaign.py \
  --campaign-id latency-sensitivity-smoke \
  --versions v1 v2 \
  --services payment-service order-service \
  --delays 500 1500 \
  --repeat 1 \
  --final-runtime v2
```

Total esperado:

- 5 execuções por versão;
- 10 execuções no total.

Analise:

```bash
python \
  experiments/analyze_latency_sensitivity.py \
  --campaign-dir \
  results/e2e/sensitivity/latency-sensitivity-smoke
```

## Campanha final

```bash
./experiments/run_latency_sensitivity.sh start \
  --campaign-id latency-sensitivity-v1-v2 \
  --versions v1 v2 \
  --services payment-service order-service \
  --delays 250 500 750 1000 1500 2000 2500 \
  --repeat 10 \
  --final-runtime v2
```

Monitoramento:

```bash
./experiments/run_latency_sensitivity.sh status
```

```bash
./experiments/run_latency_sensitivity.sh logs
```

A saída final conterá:

```text
results/e2e/sensitivity/latency-sensitivity-v1-v2/
├── status.json
├── SHA256SUMS
├── metadata/
├── v1/
│   ├── results.json
│   └── results.csv
└── v2/
    ├── results.json
    └── results.csv
```

## Análise

```bash
python \
  experiments/analyze_latency_sensitivity.py \
  --campaign-dir \
  results/e2e/sensitivity/latency-sensitivity-v1-v2
```

Saídas:

```text
analysis/
├── analysis.json
├── condition_metrics.csv
├── empirical_thresholds.csv
├── healthy_controls.csv
├── version_comparisons.csv
├── run_level_results.csv
├── report.md
└── SHA256SUMS
```

## Interpretação

Não altere o gate antes da campanha.

Delays pequenos podem resultar em `is_reliable=false`. Isso é um resultado
válido e permite estimar a curva de sensibilidade. Um resultado só deve ser
descartado por falha técnica, não por ausência de detecção.

Leia também:

```text
docs/latency-sensitivity-methodology.md
```
