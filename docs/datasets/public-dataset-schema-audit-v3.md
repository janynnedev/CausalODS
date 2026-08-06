# Auditoria de schema dos datasets públicos — adaptadores v3

Esta auditoria foi produzida em 5 de agosto de 2026 antes da implementação. Os
datasets completos não são versionados no CausalODS. Os adaptadores inspecionam
os cabeçalhos e geram `SchemaReport`, incluindo SHA-256, contagem de linhas,
campos detectados e campos não mapeados de cada arquivo local.

## RCAEval

Fonte oficial: `phamquiluan/RCAEval`, branch `main`, commit Git
`4695aa69f4f1f57b9094ca04ff235908b73a8e24`, acessado em 5 de agosto de
2026. Dataset sob MIT;
citação recomendada: Pham et al., *RCAEval: A Benchmark for Root Cause Analysis
of Microservice Systems with Telemetry Data*, WWW Companion 2025. DOI do
dataset: `10.5281/zenodo.14590730`.

Layouts detectados/documentados:

- caso: `{benchmark}_{service}_{fault}_{instance}` ou hierarquia equivalente;
- `inject_time.txt`: Unix timestamp;
- `metrics.json`: objeto `metric_name -> [[timestamp, value], ...]`;
- `metrics.parquet` e `metrics.csv`: formato wide, coluna `time` e uma coluna por métrica;
- `logs.csv` observado na fixture oficial: `time`, `timestamp`,
  `container_name`, `message`, `level`, `req_path`, `error`, `cluster_id`,
  `log_template`;
- `traces.csv` observado na fixture oficial: `time`, `traceID`, `spanID`,
  `serviceName`, `methodName`, `operationName`, `startTimeMillis`, `startTime`,
  `duration`, `statusCode`, `parentSpanID`.

Modalidades por suíte, conforme documentação oficial:

| Suíte | Métricas | Logs | Traces |
|---|---:|---:|---:|
| RE1-OB/SS/TT | sim | não | não |
| RE2-OB/TT | sim | sim | sim |
| RE2-SS | sim | sim | não |
| RE3-OB/TT | sim | sim | sim |
| RE3-SS | sim | sim | não |

A matriz é aplicada pelo contrato versionado
`rcaeval-suite-modality-policy-v1`. Em strict mode, qualquer modalidade esperada
ausente ou presente sem eventos invalida o incidente. Modalidades extras são
preservadas e registradas como inesperadas, sem serem descartadas. Sistemas não
listados não assumem silenciosamente a matriz de outra suíte: strict exige uma
política oficial/conhecida; permissive usa política não oficial, registra o fato
e marca o incidente como não utilizável para benchmark.

O `IncidentManifest` registra `modality_policy_version`, modalidades esperadas e
observadas, ausências e extras.

RCAEval não fornece business events. Métricas wide são expandidas para um
`UnifiedEvent` por ponto. Campos de logs/traces fora do conjunto detectado não
são descartados: permanecem em `attributes` e aparecem em `unmapped_fields`.

Arquivos métricos são obrigatórios. Ausência, JSON vazio/sem ponto válido,
CSV apenas com cabeçalho, Parquet sem linhas e tabela wide sem célula métrica
válida tornam o incidente inválido em `strict=True`. Para logs e traces, uma
modalidade exigida pela matriz e ausente é erro strict; modalidade não exigida e
ausente não é erro; modalidade extra presente é preservada e reportada. Se um
arquivo existente não produz evento válido, strict também falha. Essa decisão
usa `event_count`, nunca a presença de linhas como substituto de eventos.

Métricas wide adotam `partial-wide-row-v1`: células válidas da linha são
preservadas e células inválidas são rejeitadas individualmente. O relatório
separa linhas totalmente aceitas, parcialmente aceitas e totalmente rejeitadas,
além das células métricas aceitas/rejeitadas e do número real de eventos. Em
strict mode qualquer rejeição impede o incidente; em permissive mode os pontos
válidos sobrevivem com accounting explícito.

A mesma política também é usada pelo AIOps 2020. Apenas colunas métricas
documentadas ou configuradas são interpretadas; uma célula permitida inválida
jamais é omitida silenciosamente. A normalização AIOps produz um único
`AIOpsFileNormalizationResult` por arquivo, reutilizado por validação,
normalização, cache e manifesto. Portanto `validate()`, `normalize_events()` e
`load_incident()` não podem divergir semanticamente.

## AIOps Challenge 2020

Fonte oficial: `NetManAIOps/AIOps-Challenge-2020-Data`, branch `main`, commit
`5dcea9688794f3787a9b7e4a8931c6e70fa0ec78`, acessado em 5 de agosto de
2026. O README publica MD5 do download
Stage One `fac7fe1b4e048c81ef88874334b73534` e restringe o uso a pesquisa/ensino
não comercial salvo acordo com os organizadores. Citação recomendada: Li et
al., *Constructing Large-Scale Real-World Benchmark Datasets for AIOps*, 2022.

Estrutura confirmada pelo README oficial:

- `故障整理（预赛）.csv`: tempo, tipo e localização da falha;
- `业务指标`: business metrics;
- `平台指标`: métricas de infraestrutura/plataforma;
- `调用链指标`: call-chain; `id` é span ID e `pid` é parent span ID.

Campos explicitamente documentados e reconhecidos: `timestamp`, `itemid`,
`name`, `cmdb_id`/`omdb_id`, `id`, `pid` e `traceid`. Como o arquivo grande não
é incluído no repositório oficial Git, nomes adicionais não são presumidos. O
construtor aceita aliases configuráveis; `validate()` rejeita um fault CSV sem
mapeamento explícito de tempo/local/tipo e o relatório lista todo campo não
mapeado.

Modalidades: métricas de negócio, métricas de plataforma e traces/call-chain.
O download público não declara logs normalizados nem business events discretos;
portanto business metrics são `signal_type=metric`, não `business_event`.

## Cache, integridade e protocolos

- Raízes e fault CSV são configuráveis.
- Cache opcional é externo e chaveado pelos SHA-256 dos arquivos-fonte.
- `datasets/` e `.cache/causalods-datasets/` são ignorados pelo Git.
- `IncidentManifest` preserva sistema, caso, falha, serviço/localização, janela,
  modalidades, checksums e campos não mapeados.
- Protocolos: `within-dataset`, `cross-system`, `cross-source` e
  `leave-one-system-out`, sempre agrupados por `incident_id`.
- Replay Kafka é opcional via protocolo `KafkaReplayPublisher`; o adaptador não
  importa cliente Kafka nem conecta a pipeline ativa.

## Gate semântico v2 dos adaptadores

Versões contratuais:

- `RCAEVAL_ADAPTER_VERSION=rcaeval-adapter-v3`;
- `AIOPS2020_ADAPTER_VERSION=aiops2020-adapter-v2`;
- `NORMALIZED_CACHE_VERSION=normalized-public-dataset-cache-v2`;
- `PUBLIC_DATASET_PROTOCOL_VERSION=public-dataset-protocol-v2`.

### Duração de traces

| Fonte | Coluna | Unidade confirmada | Conversão para ms | Política |
|---|---|---|---:|---|
| RCAEval | `duration` | microssegundos | `raw × 0.001` | sempre normalizada quando finita e não negativa |
| AIOps 2020 | `elapsedTime`/alias configurado | não declarada no download público | configurável (`ns`, `us`, `ms`, `s`) | sem configuração, não cria `duration_ms` |

A proveniência exata da unidade RCAEval é:

- repositório/branch/commit: `phamquiluan/RCAEval`, `main`,
  `4695aa69f4f1f57b9094ca04ff235908b73a8e24`;
- arquivo: `RCAEval/e2e/tracerca.py`;
- linhas 51–54: `inject_time` é descrito no domínio de microssegundos e comparado
  diretamente com `startTime + duration`;
- SHA-256 do arquivo: `1ef420e4430a7ad0379689c1d18eee9dd7fb0d15ac4371f6ff968900a22b0946`;
- fixture oficial `multi-source-data/traces.csv`: `startTime=1705353846065999`
  e `duration=186`, confirmando o mesmo domínio;
- data de acesso: 5 de agosto de 2026.

A documentação pública do AIOps 2020
define `elapsedTime` como tempo de execução, mas não declara a unidade. Uma
configuração experimental deve registrar explicitamente a unidade verificada no
arquivo adquirido.

Todo valor preserva `duration_raw`, `duration_raw_unit`,
`duration_source_column` e `duration_policy_version`. Valores negativos, NaN ou
infinitos não produzem `duration_ms` e recebem quality flags. Unidade desconhecida
recebe `unknown_trace_duration_unit` e `missing_normalized_duration`.

### Identidade e janela

IDs trace/span inválidos ou legados permanecem `None` no campo canônico, são
preservados como `*.raw_trace_id`, `*.raw_span_id` ou `*.raw_parent_span_id` e
geram flags `legacy_or_invalid_*`. Essa política é uniforme nos dois adapters.

- RCAEval com `inject_time.txt`: `source-prewindowed-v1`; janela solicitada é
  injection ± pre/post, todos os eventos do caso são incluídos e o manifesto
  registra intervalo observado e eventos externos à janela. Sem `inject_time`,
  `raw-observed-interval-v1` deriva início/fim diretamente dos arquivos-fonte,
  sem normalização nem acesso ao cache.
- AIOps 2020: `adapter-window-filter-v1`; somente eventos dentro da janela
  solicitada são incluídos. O manifesto registra intervalo observado, quantidade
  fora da janela e decisão de exclusão.

### Cache

A chave inclui adapter/versão, incidente, hashes das fontes, janela, aliases,
políticas de timestamp e duração, campos métricos wide, metadados preservados,
strict mode e schema do evento. `cache_dir` não participa da identidade lógica.

Envelope persistido:

```json
{
  "cache_version": "normalized-public-dataset-cache-v2",
  "cache_key": "sha256",
  "adapter": "...",
  "adapter_version": "...",
  "configuration_sha256": "sha256",
  "source_files_sha256": {},
  "event_count": 0,
  "events_sha256": "sha256",
  "events": []
}
```

A leitura verifica todos os campos, recalcula o hash e reconstrói cada
`UnifiedEvent`. A escrita usa arquivo temporário no mesmo diretório, `flush`,
`fsync` e `os.replace`.

### Validação e protocolos

`strict=True` é o padrão. Cada arquivo relata linhas aceitas/rejeitadas, eventos,
motivos e classificação entre identidade, métricas interpretadas, metadados,
campos não mapeados e rejeitados. Métricas wide só são interpretadas por uma
allowlist documentada ou configurada; outras colunas numéricas não viram métricas.

O enforcement strict é interno e compartilhado por `validate()`,
`normalize_events()` e `load_incident()`: não é necessário chamar validação
antes da normalização. Ele ocorre antes da leitura/gravação do cache e exige,
por incidente, `total_event_count > 0`, `metric_event_count > 0`, zero unidades
rejeitadas e que todo arquivo existente produza evento. Assim uma falha strict
jamais cria cache.

No AIOps 2020, o enforcement também ocorre antes de qualquer leitura de cache.
Ausência de telemetria, arquivo sem eventos na janela, linha semanticamente
rejeitada ou célula wide inválida bloqueiam integralmente o resultado strict.
Em permissive mode, eventos válidos são preservados, todas as perdas aparecem
nos relatórios e `usable_for_benchmark=false` acompanha um motivo auditável.

`rejection_reasons` contém apenas falhas que impediram aceitar linha, ponto ou
célula. Problemas preservados no evento (duração negativa/inválida, duração
normalizada ausente, IDs legados/inválidos e serviço métrico não derivável)
ficam em `quality_issue_reasons`; eles não incrementam linhas rejeitadas.
Execuções permissivas preservam eventos válidos, emitem warnings e manifestam
as perdas. Um incidente permissivo vazio registra `normalization_mode`,
`empty_incident_reason` e `usable_for_benchmark=false`.

O manifesto final inclui `normalization_accounting_by_file`, com linhas aceitas,
rejeitadas e parcialmente aceitas, além de células métricas aceitas/rejeitadas.
Os totais são reconciliados com o incidente e com as razões cell-level
centralizadas (`invalid_metric_cell:*` e razões long explícitas). Manifestos
strict não aceitam rejeições, modalidades esperadas ausentes, motivo de vazio
ou estado não utilizável. Manifestos permissivos com qualquer perda exigem
`usable_for_benchmark=false` e um motivo textual não vazio. Coleções de
modalidades duplicadas são rejeitadas antes da ordenação canônica.

### Contrato final de accounting

Cada arquivo processado possui um `NormalizedFileAccounting` imutável com path,
kind, signal, linhas totais/aceitas/rejeitadas/parciais/excluídas, eventos e
células métricas aceitas/rejeitadas. `normalized_source_files` contém exatamente
as chaves desse mapa. No RCAEval ele inclui somente metrics/logs/traces
processados, nunca `inject_time.txt`; no AIOps inclui somente business metrics,
platform metrics e call-chain processados, nunca o fault CSV.

As reconciliações são obrigatórias mesmo quando o mapa recebido está vazio:
linhas, eventos, células e contagens por modalidade devem coincidir com os
totais globais. Accounting vazio só representa execução permissiva realmente
vazia, não utilizável e com motivo. Razões row-level e cell-level são verificadas
separadamente; quality issues não participam dessas unidades.

`modality_policy_status` declara a origem da política: `official` para suíte
RCAEval conhecida, `detected` para AIOps derivado dos arquivos e `nonofficial`
para RCAEval desconhecido permissivo. Strict não aceita `nonofficial`, arquivos
sem eventos, linha parcial, célula/linha rejeitada, modalidade esperada ausente,
accounting incompleto ou motivo de vazio. Permissive preserva eventos, mas toda
perda exige `usable_for_benchmark=false` e motivo textual; sem perda, o motivo
deve ser `None`.

`SchemaFileReport.layout_policy` elimina ambiguidade entre
`non-metric-v1`, `long-metric-row-v1`, `partial-wide-row-v1` e
`metric-json-point-v1`. Os fingerprints incluem integralmente status, arquivos
normalizados e accounting. `IncidentManifest.from_dict()` e
`NormalizedFileAccounting.from_dict()` reexecutam todas as invariantes; o
round-trip JSON preserva `to_dict()` e fingerprint.

No AIOps, as flags de duração configurável, duração ausente/inválida, IDs
legados e campos de modalidade ausentes são agregadas por arquivo tanto em
`SchemaFileReport.quality_issue_reasons` quanto em
`IncidentManifest.quality_issue_reasons_by_file`. Quality issues preservam o
evento e não alteram `rejected_row_count`.

O `dataset_schema_report` audita todos os arquivos disponíveis. Cada
`IncidentManifest` usa também um `incident_normalization_report`, restrito ao
caso/janela corrente, e registra separadamente linhas consideradas, aceitas,
rejeitadas, excluídas pela janela, eventos e motivos por arquivo. Os dois hashes
permanecem distintos e auditáveis.

`cross-system` exige um sistema explícito. `cross-source` exige uma fonte
explícita. `leave-one-system-out` usa `plan_all_folds()` e gera exatamente um
fold por sistema, enquanto `within-dataset` registra seed e fração, com split
determinístico exclusivamente por `incident_id`. `ProtocolPlanManifest` contém
todos os folds e fingerprint canônico.

Coleções de IDs, sistemas, fontes e folds são copiadas defensivamente e
convertidas em tuples canônicas. `within-dataset` tem exatamente um fold, seed
inteira não booleana e fração em `(0,1)`, idênticas no manifesto e no fold.
Cross-system, cross-source e LOSO não aceitam seed/fração; LOSO possui exatamente
um fold por sistema. Parâmetros não usados não participam do fingerprint.
