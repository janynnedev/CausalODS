# Checklist de release da plataforma v3

- [x] Auditoria pré-integração registrada.
- [x] Freeze v2 12/12 preservado.
- [x] Auditoria v2 -> `multimodal_v1` sem imports.
- [x] Cinco índices/templates v3 versionados e mapping limitado.
- [x] Stress lógico com 2.000 atributos sem field explosion.
- [x] Oito dashboards e datasources provisionados.
- [x] Tempo e Grafana isolados por overlay.
- [x] Alerta básico sem notificação externa.
- [x] Importador Prompt 11 com schema, checksum e IDs.
- [x] Fixtures healthy/failure/latency/missing signal.
- [x] Smoke contratual multimodal, grafo, features, RCA e explicação.
- [x] Release manifest e relatório reproduzíveis.
- [ ] Artefato operacional `xgboost-rca-v3` publicado e verificado.
- [ ] Runtime multimodal v3 conectado ponta a ponta.
- [ ] Fault E2E Docker completo.
- [x] OpenSearch indexing/query live validado nos cinco índices.
- [x] Grafana API/provisioning/query live validado.
- [x] Tempo: ingestão, busca por TraceId e proxy Grafana validados.
- [ ] Decisão `READY_FOR_FINAL_EXPERIMENTS`.

Decisão atual: `NOT_READY_FOR_FINAL_EXPERIMENTS`.
