# Limitações conhecidas da plataforma v3

## Bloqueantes

Não há bloqueante aberto no Gate 1: a validação final registrou 13 PASS e 0 FAIL.

## Não bloqueantes no desenho

- Live LLM é opcional; Stub/deterministic fallback valida o contrato.
- Tempo fica isolado no overlay. Saúde, ingestão, busca por TraceId e consulta
  pelo proxy Grafana fazem parte do fault E2E obrigatório.
- A visualização do grafo é tabular/path view para evitar plugin adicional.
- Retenção dos índices é uma política externa ainda não definida; os templates
  registram essa decisão em `_meta`.
- Datasets públicos completos não são incluídos no Git.
- Logs OTLP dependem de instrumentação/exportação efetiva da aplicação Java;
  habilitar a variável de ambiente não garante por si só que todo log seja
  emitido. No smoke live foram observados offsets em `traces-v3`, `logs-v1` e
  `metrics-v1`; não existe produtor live de `business-events-v1`. O runtime não
  fabrica essa modalidade e registra sua ausência como qualidade não bloqueante.
- O modelo publicado tem finalidade exclusiva `runtime_e2e_validation`; campanhas
  H1/H2 devem treinar seus próprios artefatos pelos folds do Prompt 11.
