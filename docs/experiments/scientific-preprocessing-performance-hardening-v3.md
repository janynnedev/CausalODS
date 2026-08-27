# SCIENTIFIC_PREPROCESSING_PERFORMANCE_HARDENING

## 1. Root causes encontrados

- O `IncidentEvidenceAssembler` continuava pontuando eventos contra um estado já
  limitado a 10.000 itens. Para incidentes maiores que esse limite, ele nunca
  poderia devolver um único bundle completo e terminava no boundary congelado.
- Regras de aresta reconstruíam índices de spans, identidade e serviço. A regra
  de topologia fazia busca cartesiana source × target dentro da janela.
- `GraphFeatureExtractor` repetia BFS e varreduras de arestas por serviço.
- `RCAFeatureExtractorV3` reconstruía mapas e validava referências por serviço.
- A validação de `EventEvidenceGraph` fazia lookup linear de serviço por
  referência; a correção `service_by_reference` foi preservada.
- O fingerprint materializava estruturas JSON gigantes e recalculava hashes de
  eventos que já haviam sido verificados ao carregar o cache.
- Feature views semanticamente equivalentes refaziam a geração FULL.
- O mount rclone/FUSE adicionava latência relevante e variável à leitura.

## 2. Antes/depois por hotspot

| Hotspot | Antes | Depois |
|---|---:|---:|
| `re1ob_adservice_cpu_1` / `feature_input` | timeout > 180 s | 25,79 s |
| Mesmo caso / load local | 48,70 s | 49,81 s |
| Mesmo caso / total integral | não concluía no gate de 180 s | 117,52 s |
| Caso pequeno / bundle | 55,81 s observado sob carga | 0,14 s |
| Cache montado / load obrigatório | 81,49 s | — |
| Mesmo arquivo em staging local | — | 48,70 s |

O fingerprint do caso obrigatório permaneceu
`b68c7893f9c147db765ce5a716ae407a49afc268f8dff6c35c3f7c8af2de1524`.

## 3. Complexidade antes/depois

- Boundary RCAEval grande: de uma tentativa efetiva
  `O(N × min(N, 10.000))` para a aplicação direta, provadamente equivalente,
  do boundary congelado `O(N)`.
- Topologia: de `O(source × target)` para busca em timestamps ordenados com
  `bisect`, preservando o primeiro candidato elegível e todo o payload da aresta.
- Features do grafo: de BFS por destino/serviço e scans repetidos para passes
  multi-source e uma única agregação, `O(V + E)` por classe de distância.
- Validação de serviços: de lookup linear por referência para índice `O(V)`.
- Fingerprint: de materialização integral do JSON para hash canônico streaming,
  com cache do resultado e hashes de eventos já verificados.
- Views: uma geração FULL por split científico único; ablações usam
  `RCAFeatureDatasetV3.ablate` e não repetem grafo/extractor para cada view.

O teste sintético instrumentado exige probes indexados de topologia e zero
comparações cartesianas source × target.

## 4. Arquivos modificados

- `correlation-engine/multimodal_v1/contracts.py`
- `correlation-engine/multimodal_v1/event_graph/builder.py`
- `correlation-engine/multimodal_v1/event_graph/contracts.py`
- `correlation-engine/multimodal_v1/event_graph/features.py`
- `correlation-engine/multimodal_v1/event_graph/rules.py`
- `correlation-engine/multimodal_v1/features_v3/extractor.py`
- `experiments/final_campaigns_v3/preprocessing.py`
- `experiments/final_campaigns_v3/cli.py`
- `experiments/final_campaigns_v3/profiling.py`
- `experiments/tests/test_scientific_preprocessing_performance_v3.py`

Nenhum dataset congelado, label, boundary, feature definition/registry, split,
seed, método, model specification, contrato público ou configuração H1–H4 foi
alterado.

## 5. Provas de equivalência

- Regra de topologia legada exaustiva versus indexada: nós, arestas, ordem,
  referências de evidência, confiança, versões, service nodes, JSON e fingerprint
  exatamente iguais nas fixtures de fronteira.
- Loader Parquet otimizado versus `CompressedNormalizedEventCache.load`:
  mesmos `UnifiedEvent` e mesmos fingerprints contratuais.
- Fingerprint streaming e binding binário interno: igual a
  `sha256(graph.to_json())`.
- Bundle indexado pequeno: igual ao resultado público do assembler após somente
  a troca auditável de `incident_id`.

## 6. Test results

- Regressão relevante combinada: `365 passed in 209.29s`.
- Inclui todos os testes EventGraph, features v3, public adapters, RCAEval,
  orchestrator, causalods-local-v3, final campaign wiring/hardening e os novos
  testes de performance/equivalência.
- `compileall`: PASS.
- `git diff --check`: PASS.

## 7. Benchmark results

| Caso | Eventos | Load s | Bundle s | Grafo s | Extractor s | Baseline s | Generator s | Total s | Nós | Arestas |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| small `re1ob_productcatalogservice_cpu_3` | 3.654 | 0,74 | 0,14 | 0,25 | 0,07 | 0,01 | 0,47 | 1,69 | 3.654 | 3.653 |
| medium `re2ss_carts_mem_2` | 196.907 | 51,40 | 5,17 | 22,09 | 8,91 | 2,46 | 33,68 | 124,15 | 196.907 | 196.906 |
| required `re1ob_adservice_cpu_1` | 205.849 | 49,81 | 4,40 | 21,39 | 9,73 | 1,25 | 30,18 | 117,52 | 205.849 | 205.848 |
| largest `re2tt_ts-route-service_socket_1` | 2.331.390 | 1.078,56 | 58,38 | 808,65 | 204,27 | 17,29 | 604,68 | 2.797,10 | 2.331.390 | 4.013.811 |

O maior caso concluiu sem sampling. Seu grafo contém 1.527.803 `SPAN_PARENT`,
154.619 `SERVICE_CALL` e 2.331.389 `TEMPORAL_PRECEDENCE`.

## 8. Peak RSS

| Caso | Peak RSS |
|---|---:|
| small | 250,37 MiB |
| medium | 997,30 MiB |
| required | 970,50 MiB |
| largest | 9,95 GiB |

Para o maior caso, a stack local foi pausada de forma reversível, sem remover
volumes/imagens/índices, e restaurada após o benchmark. Kafka/OpenSearch voltaram
saudáveis e o Collector voltou a reportar `Everything is ready`.

## 9. Golden artifact comparison

Os sete BenchmarkDatasets foram reprocessados em `/tmp`, sem sobrescrever os
originais. Resultado: 7/7 nomes, JSON, manifests e SHA-256 exatamente iguais.
Todos possuem 800 rows. Exemplos: dataset `8e6d…` =
`08420b2a524bf6f19bcee6c349dc461356e31548aed7776833a82b6a5dbb89e8`;
manifest = `601482c34f4eb7588d11ad3571f8d37bf51dbdec703341f74d85f759fe75bb02`.

## 10. Resume validation

- Auditoria original: `VALID=7`, `INVALID=0`.
- Resume completo controlado: `reused=7`, `new=0`; todos os mtimes preservados.
- Diretório controlado com seis artefatos: `reused=6`, `new=1`, `valid=7`.
- O novo sétimo artefato reproduziu exatamente os hashes dourados.
- Nenhuma requisição, fault injection, modelo ou inferência final foi executado.

## 11. Frozen invariants

- V2 freeze: `12/12`, sem mismatch.
- Imports v2 → multimodal v1/v3 runtime: `0`.
- Dataset registry preflight: PASS.
- Frozen immutability snapshot antes/depois: idêntico,
  `ca4a1c6163fea7d7d2b902b25893f9fd8f091e5d7d11fbfb166910c3369a6cf4`.
- RCAEval `SHA256SUMS`:
  `413debe4cea2beebf024d36fa7163e86657a6e5d076a022605ad35c7a50f1bca`.
- causalods-local-v3 `SHA256SUMS`:
  `81b2791ef7b5dfeef2875f822074a01623d0139ba5d227041c8fb6537deb4f4c`.
- Release snapshot não atualizado; H1/H2/H3/H4, treino e inferência final não
  foram iniciados.

`PROMPT_13C_1_PERFORMANCE = READY_FOR_PREPROCESS_RESUME`
