# Limitações conhecidas da plataforma v3

## Bloqueantes

- Não existe artefato operacional publicado `xgboost-rca-v3`; existe o
  benchmark científico que produz artefatos por execução.
- O `MultimodalKafkaConsumer` termina em um sink injetado, mas o Compose não
  possui ainda um runtime v3 que encadeie assembler, grafo, modelo, explicação
  e projeções OpenSearch.
- O fault E2E completo de microsserviços até Grafana não está automatizado.

## Não bloqueantes no desenho

- Live LLM é opcional; Stub/deterministic fallback valida o contrato.
- Tempo fica isolado no overlay. Saúde, ingestão, busca por TraceId e consulta
  pelo proxy Grafana foram validadas; isso não equivale ao fault E2E multimodal.
- A visualização do grafo é tabular/path view para evitar plugin adicional.
- Retenção dos índices é uma política externa ainda não definida; os templates
  registram essa decisão em `_meta`.
- Datasets públicos completos não são incluídos no Git.
- Logs OTLP dependem de instrumentação/exportação efetiva da aplicação Java;
  habilitar a variável de ambiente não garante por si só que todo log seja
  emitido. No smoke live foram observados offsets em `traces-v3`, `logs-v1` e
  `metrics-v1`; não existe produtor live de `business-events-v1` conectado ao
  runtime v3.
