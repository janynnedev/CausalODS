# CausalODS — Explicações determinísticas v1.1

Esta revisão preserva temporal/SHAP, produz relatório estratificado e inclui
avaliação automática de cobertura, fidelidade e determinismo.

## Instalação

```bash
./install.sh /caminho/para/CausalODS-explanations
```

## Testes

```bash
PYTHONPATH=correlation-engine \
python -m pytest \
  correlation-engine/tests/test_explanations.py \
  -q
```

## Gerar duas execuções

```bash
python experiments/generate_rca_explanations.py \
  --input /caminho/rca-documents.json \
  --output-dir results/offline-explanations/v1.1-run-a
```

```bash
python experiments/generate_rca_explanations.py \
  --input /caminho/rca-documents.json \
  --output-dir results/offline-explanations/v1.1-run-b
```

## Avaliar

```bash
python experiments/evaluate_rca_explanations.py \
  --source /caminho/rca-documents.json \
  --explanations \
    results/offline-explanations/v1.1-run-a/explanations.json \
  --second-run \
    results/offline-explanations/v1.1-run-b/explanations.json \
  --output-dir \
    results/offline-explanations/v1.1-evaluation
```
