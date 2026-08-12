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

O fault live obrigatório injeta payment failure, envia uma request real e valida
19 estágios até RCA, explicação, OpenSearch, Grafana e Tempo. Ground truth aparece
somente em `evaluation`; um scan das projeções operacionais comprova ausência de
leakage. O checksum, o hash do modelo e os hashes do registry/view são revalidados
dinamicamente pelo release gate.

As evidências de regressão e fault E2E são criptograficamente vinculadas ao
mesmo snapshot-fonte determinístico. O fingerprint usa caminhos Git versionados
e SHA-256 dos conteúdos, excluindo apenas artefatos gerados autorreferentes,
caches e os modelos já validados pelo gate criptográfico próprio. O relatório de
validação recalcula o snapshot uma vez e o reconcilia com `test-results.json`,
`fault-e2e-live.json` e `release-manifest.json`.

## Comandos

```bash
PYTHONPATH=correlation-engine .venv-host/bin/python -m platform_v3 smoke
PYTHONPATH=correlation-engine .venv-host/bin/python -m platform_v3 e2e-live --scenario payment_failure --output artifacts/platform-v3
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
