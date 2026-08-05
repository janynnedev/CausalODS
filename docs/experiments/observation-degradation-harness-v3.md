# Observation Degradation Harness v3

`observation-degradation-harness-v3` avalia robustez do RCA sob deterioração
controlada de trace, log, metric, business event e topologia. O identificador
`observation-dataset-v1` é a versão independente do envelope de entrada; não é a
versão do projeto nem do harness.

## Severidade e accounting

As severidades permitidas são 0%, 10%, 30% e 50%. Para severidade positiva e
conjunto elegível não vazio, pelo menos uma unidade é afetada, usando
`max(1, floor(N * severity + 0.5))`. Não há bankers rounding.

Cada política declara versão, modalidades, unidade, arredondamento, configuração
canônica e fingerprint. `DegradationAccountingV3` separa unidades principais de
eventos secundariamente afetados e expõe:

- perdas, adições e modificações de eventos;
- perdas e adições de arestas;
- razões específicas por evento e topologia;
- razão de unidades afetadas e severidade efetivamente realizada.

Assim, delay, duplicate, reorder e edge-add têm perda de dados igual a zero, mas
severidade realizada não nula.

Razões seguem o contrato `0/0 = 0.0`; numerador positivo com denominador zero é
indefinido (`null`). Para `topology_edge_add`, a razão usa as arestas ausentes
elegíveis como denominador, inclusive quando a topologia inicial tem zero arestas.
O accounting valida limites de elegibilidade, relações solicitado/afetado,
equações de eventos e arestas, unidades conhecidas e modificações possíveis.
Reorder registra solicitação original e efetiva quando uma posição é expandida.

## Semântica dos operadores

- `random_span_drop`: sorteio determinístico de spans individuais.
- `critical_path_span_drop`: somente `attributes.critical_path=true`, política
  `critical-path-annotated-v1`; nunca completa a quantidade com outro span.
- `full_trace_sampling`: seleciona identidades de trace e remove todos seus spans.
- `random_log_drop`: sorteio determinístico entre logs.
- `error_log_drop`: política `error-log-policy-v1`, cobrindo ERROR, CRITICAL,
  FATAL, status ERROR, `error_type`, `exception.type` e `error.type`.
- `metric_delay`: altera somente o metadado sintético `late_arrival_ms`; não agenda
  espera real no replay e preserva ausência anterior versus zero no manifesto.
- `metric_downsampling`: downsampling temporal determinístico dentro de cada série,
  preservando primeiro e último ponto. A severidade é calculada uma vez sobre a
  capacidade interior global. `proportional-largest-remainder-v1` distribui a
  solicitação pela capacidade, usa maior resto e desempata pela identidade canônica.
  Quota, capacidade, alocação e pontos removidos são registrados. Não representa
  perda métrica aleatória.
- `metric_series_drop`: remove séries completas usando serviço, instância, nome,
  unidade, agregação e dimensões versionadas como identidade.
- `business_event_drop`: sorteio determinístico de eventos de negócio.
- `kafka_message_duplicate`: adiciona cópias e registra EventReferences e contagem.
- `kafka_message_reorder`: rotação determinística das posições selecionadas;
  seleção unitária é expandida para duas mensagens quando possível.
- `uninstrumented_service`: seleciona serviços e remove sua telemetria, registrando
  serviços e eventos secundariamente removidos.
- `topology_edge_drop` e `topology_edge_add`: usam aresta como unidade e denominador.

O dataset base rejeita EventReference duplicada. Apenas a saída declarada de
`kafka_message_duplicate` habilita duplicatas. Topologia e ground truth são
profundamente imutáveis, e self-loops são rejeitados.

O envelope expõe três identidades distintas:

- `observation_fingerprint`: schema, modo de duplicatas, eventos e topologia;
- `ground_truth_fingerprint`: somente labels canônicos;
- `dataset_fingerprint` (e o alias compatível `fingerprint`): composição das duas.

A seed de todos os operadores deriva exclusivamente da identidade observacional,
operador/versão/fingerprint, severidade, seed informada e hash dos parâmetros.
Ground truth não participa direta ou indiretamente da seleção. Trocar somente os
labels preserva referências afetadas, observação degradada e selection fingerprint.

## Manifesto e replay

O manifesto canônico possui `to_dict()`, `to_json()` e `manifest_fingerprint`.
O `run_id` deriva de hash da entrada, operador/versão, severidade, seed e hash dos
parâmetros, incluindo o fingerprint da política. Uma função única constrói essa
identidade na execução e na validação. Accounting, parâmetros, referências e
metadados são deep-frozen. O manifesto revalida versão do harness, operador,
severidade, seed, hashes, política, accounting, modalidades e run ID reconstruído.
Ground truth participa apenas do fingerprint e da avaliação posterior, nunca da
seleção do operador.

O manifesto registra separadamente hashes de dataset e observação na entrada e
saída, hash do ground truth, `selection_fingerprint` e identidade completa do run.
Ele recalcula `requested_units` a partir de elegibilidade e severidade. Saturação é
obrigatória quando o realizado é menor que o solicitado; a expansão 1→2 do reorder
é a única expansão de solicitação admitida. `removed_by_modality` contém exatamente
as cinco modalidades e é reconciliado com eventos e arestas removidos.

`DegradationRun` vincula o manifesto ao dataset exato, aos três fingerprints e ao
modo de duplicatas. Misturar dataset e manifesto de execuções diferentes falha.

`replay_offline()` devolve eventos canônicos. `replay_kafka()` não importa Kafka:
usa publisher injetável, publica sincronicamente, aguarda `get()` quando fornecido
pelo retorno do publisher, continua após falha parcial e retorna quantidades
tentada, confirmada e falhada, além dos erros sanitizados por tipo.

## Métricas

`degradation_metrics()` valida probabilidades, confidence, outcomes, abstention e
tempos. Exporta Top-1, Top-3, MRR, FPR, confidence, calibração, abstention, tempos,
delta absoluto, variação percentual assinada e degradação orientada pela métrica.
Top-1, Top-3 e MRR são `higher_is_better`; FPR é `lower_is_better`. Baseline zero
produz `null` com razão `baseline_zero`. `outcomes` contém somente decisões
respondidas, portanto abstention é `abstentions / (len(outcomes) + abstentions)`.
O número de abstentions pode ser qualquer inteiro não negativo. Sem respostas,
confidence e calibration são `null`; dez abstentions resultam em taxa 1.0 e o caso
completamente vazio resulta em 0.0.

O harness permanece experimental e não importa nem altera consumers, pipelines,
modelos ou artefatos v1/v2. Nenhuma GNN faz parte deste gate.
