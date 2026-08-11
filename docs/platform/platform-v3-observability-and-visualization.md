# Observabilidade e visualização da plataforma v3

## Estratégia OpenSearch

Os templates estão em `infrastructure/platform-v3/opensearch/templates`.
Todos usam `dynamic: false`, limite explícito de 300 campos e nomes versionados.
Campos canônicos continuam indexados. `attributes`, resource attributes,
proveniência, payloads de grafo, SHAP e métricas experimentais são serializados
como JSON canônico em campos `index: false`. Assim 2.000 chaves arbitrárias
continuam sendo um único campo do mapping.

Não foi usado `flat_object`: embora OpenSearch 2.19 suporte esse tipo em
contextos específicos, a projeção não precisa pesquisar chaves arbitrárias e a
representação JSON não indexada reduz a superfície de mapping e privacidade.

Ground truth é removido recursivamente de eventos, incidentes, RCA e
explicações. Ele só é preservado no índice experimental com `evaluation: true`.
Secrets por nomes canônicos também são removidos na fronteira de projeção.

## Grafana

Datasources provisionados:

- cinco datasources OpenSearch, um por família de índice;
- Prometheus;
- Tempo.

Dashboards versionados:

1. Incident Overview;
2. Root Cause Detail;
3. Multimodal Evidence;
4. Event Evidence Graph;
5. Observability Quality & Robustness;
6. Experimental Results H1-H4;
7. Explanation Audit;
8. Platform Health.

O painel de grafo usa uma path/table view auditável, sem plugin adicional. As
relações temporais e coanomalias são explicitamente descritas como associações,
não como causalidade comprovada. O dashboard experimental exibe valores do
Prompt 11 e não recalcula métricas.

O importador verificou os checksums da campanha reduzida, indexou 26 runs por
`run_id` e validou 23 CSVs, 3 JSONs agregados e 2 resumos Markdown. Os valores
canônicos não são recalculados. Duas execuções consecutivas mantiveram as
contagens, comprovando idempotência das chaves finais.

## Tempo e alertas

Tempo 2.7.2 é adicionado apenas no overlay. O Collector v3 envia traces ao
Tempo e preserva Jaeger. O teste live gerou um `POST /orders` distribuído entre
order/payment, preservou `business.correlation.id`, encontrou o TraceId pela
TraceQL e consultou o trace diretamente e pelo proxy do datasource Grafana.

A regra versionada `causalods-v3-service-down` avalia a métrica Prometheus
`up` com limiar 1 e duração de 2 minutos. Não há contact point externo. Os
demais alertas candidatos aguardam métricas reais correspondentes; não foram
fabricados contadores. A API de regras confirmou provisioning, `health=ok`,
estado `Normal` e avaliação periódica da regra.

A avaliação foi simulada sem notificação externa: ao parar somente o
payment-service, a regra transitou `Normal -> Pending -> Firing` após o `for:
2m`; após reiniciar o serviço em `fault_mode=none`, retornou a `Normal`, sempre
com `health=ok`.

O plugin OpenSearch 2.25.0 é instalado com `GF_PLUGINS_PREINSTALL`. Os cinco
datasources OpenSearch passaram no health-check de índice/campo temporal, e uma
consulta métrica completa foi executada pela API backend do Grafana. Os oito
dashboards foram enumerados pela API e o Incident Overview foi renderizado em
navegador headless sem erro de query.

Filtros de IDs/campanha usam variáveis `textbox` com wildcard inicial. A
variável dinâmica `find: terms` foi removida porque o caminho `_msearch` do
plugin 2.25.0 expirou em teste real; Root Cause Detail e Experimental Results
foram renderizados novamente sem erro após a correção.
