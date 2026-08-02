# CausalODS — Explicações determinísticas v1

Implementação offline e auditável da camada de explicação.

## Instalação

```bash
./install.sh /caminho/para/CausalODS
```

## Testes

```bash
cd /caminho/para/CausalODS

PYTHONPATH=correlation-engine \
python -m pytest \
  correlation-engine/tests/test_explanations.py \
  -q
```

## Execução em lote

```bash
python \
  experiments/generate_rca_explanations.py \
  --input \
  results/e2e/final/final-v1-v2-50/baseline-input/rca-documents.json \
  --output-dir \
  results/e2e/final/final-v1-v2-50/explanations
```

O comando não acessa Docker, Kafka nem OpenSearch.
