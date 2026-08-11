# Protocolo de validação da plataforma v3

## Gates

Cada gate retorna `PASS`, `FAIL` ou `SKIP_WITH_REASON`. A decisão só pode ser
`READY_FOR_FINAL_EXPERIMENTS` quando todos os gates obrigatórios forem `PASS`.
Tempo pode ser skip justificado; modelo operacional v3, fault E2E real,
OpenSearch e Grafana live não podem.

O gate live também exige cinco índices consultáveis, oito dashboards, health
dos datasources, uma query OpenSearch via backend Grafana, estado das regras e,
quando houver trace, consulta Tempo direta e pelo proxy Grafana.

O smoke de contratos cobre healthy, payment failure, payment latency e missing
signal. Ele usa eventos controlados somente nos limites declarados
`telemetry_fixture` e `model_output_contract`; assembler, grafo, feature
extractor, RCAResult, explicação e adaptadores são reais. Por isso ele não é
rotulado como E2E Docker.

Como validação live parcial, o injetor real produziu: healthy HTTP 200; payment
failure HTTP 500 com 3 spans de erro; e payment latency HTTP 200 em 0,848 s,
com trace de 846 ms. Os traces preservaram o correlation ID e envolveram
order/payment. Missing signal permaneceu no harness/fixture. Esses testes param
em Collector/Tempo e não satisfazem o fault E2E completo até RCA/Grafana.

## Comandos

```bash
PYTHONPATH=correlation-engine .venv-host/bin/python -m platform_v3 smoke
PYTHONPATH=correlation-engine .venv-host/bin/python -m platform_v3 validate --output /tmp/causalods-platform-v3
PYTHONPATH=correlation-engine .venv-host/bin/python -m platform_v3 validate --live --output /tmp/causalods-platform-v3-live
```

O relatório inclui ambiente, contratos, templates, dashboards, smoke,
isolamento v2, segurança, limitações e decisão. O release manifest possui
SHA-256 separado.

## Regressão

Executar sequencialmente. A suíte do engine regrava um modelo durante testes;
o arquivo deve ser restaurado e os 12 hashes confirmados antes de experimentos.

```bash
cd correlation-engine
PYTHONPATH=. ../.venv-host/bin/python -m pytest -q tests
cd ..
PYTHONPATH=correlation-engine .venv-host/bin/python -m pytest -q experiments/tests
PYTHONPATH=correlation-engine .venv-host/bin/python -m compileall -q correlation-engine experiments
.venv-host/bin/python -m pip check
git diff --check
```
