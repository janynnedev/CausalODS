# Protocolo experimental multimodal v3

## H1–H4 e unidade experimental

A unidade persistida é uma configuração por `incident_id`; treino e avaliação
agregada são delegados ao benchmark v3. H1 compara baselines, ablações estruturais
e modalidades. H2 usa sete métodos na mesma full FeatureView. H3 explica o mesmo
RCAResult nos três modos. H4 aplica o harness ao mesmo ObservationDataset em 0%,
10%, 30% e 50%, sem retreino.

## Datasets, capabilities e protocolos

Capabilities vêm de `IncidentManifest.modalities`. Local pode declarar trace,
log, metric, business_event e topology. RCAEval segue a tabela oficial: RE1 é
metric-only; RE2/RE3 variam e Sock Shop não recebe traces artificiais. AIOps2020
mapeia business/platform data para metric e call-chain para trace. Ausência natural
gera `SKIPPED_MODALITY_UNAVAILABLE`, nunca degradação artificial.

Within-dataset, cross-system, cross-source e leave-one-system-out reutilizam o
`ProtocolPlanner`. Quando só há train/test, `derive_validation_split` deriva
validation exclusivamente do train, agrupado por incident_id e seed. Interseções
train/test ou validation/test invalidam a campanha. Test não participa de tuning.
O manifesto de protocolo, folds, holdout, train, validation e test é persistido.
Um grupo insuficiente é um skip de protocolo, não uma divisão por linha.

## Feature views, modelos e seeds

H1-B mantém `xgboost-rca-v3`: NO_GRAPH exclui graph e desabilita pruning; GRAPH
ativa grafo/pruning e exclui business; GRAPH_BUSINESS ativa ambos e requer a
modalidade. H1-C usa METRIC_ONLY, TRACE_ONLY, METRIC_TRACE, METRIC_TRACE_LOG e
METRIC_TRACE_LOG_BUSINESS. Cada view guarda ID e hash. H2 delega os três baselines,
LR, RF, XGBoost e MLP ao benchmark com mesmos splits, view, seed e tuning budget.

## H3

A amostra é determinística por dataset, sistema, tipo de falha e incidente,
aceita estratos opcionais de correção RCA, reliability e cenário, e respeita
`sample_size`. Generic recebe contexto mínimo; raw recebe até 50 itens
ordenados, sanitizados e versionados; grounded recebe o EvidenceObject. Ground
truth e campos de label são removidos. Root, confidence e reliability vêm do
RCAResult. Fallback é registrado separadamente. Métricas de citação são `null`
quando inaplicáveis. O export humano usa códigos cegos e mapa separado; julgamento
humano e concordância só existem após ratings suficientes. Ratings 1–5 podem ser
importados com `--human-ratings`; concordância percentual exata só é calculada
quando dois revisores avaliam ao menos o mesmo item.

## H4 e robustez

Operadores e nomes vêm do harness. Cada incident/operator/seed parte de um único
dataset base nas quatro severidades; 0% é o no-op contratual. São persistidos perda
real/esperada, modalidades ausentes, hash do manifesto e delta contra 0%.
Robustez de infraestrutura é uma campanha suplementar separada.
`offline` é o modo científico primário. `kafka-replay` reutiliza
`ObservationDegradationHarness.replay_kafka`, exige publisher injetado e registra
falhas como `FAILED_REPLAY`, separadas de degradação observacional.

## Métricas e estatística

Precision, recall, macro-F1, FPR, Top-1/3, MRR, latências, throughput, memória,
tamanho e treino são definições do benchmark. H1 acrescenta métricas de pruning;
H3 mede consistência, grounding, unsupported claims, uncertainty, schema, fallback,
tokens, latência e custo; H4 reutiliza `degradation_metrics`. Comparações exigem
alinhamento: McNemar para Top-1, bootstrap pareado IC95% para contínuas e Holm por
família. Estabilidade entre seeds é separada.

Definições H3: `root_cause_consistency` compara a causa textual à causa congelada;
`valid_evidence_reference_rate` é a fração de IDs citados existentes no catálogo;
`evidence_citation_precision` é a mesma precisão apenas quando citações estruturadas
são aplicáveis; `evidence_coverage` é a fração do catálogo permitido citada;
`unsupported_claim_rate` é a fração de statements sem evidência;
`uncertainty_compliance` valida o vocabulário de incerteza;
`schema_validity_rate` registra validação do schema; `fallback_rate` distingue
fallback de resposta LLM; latência, tokens e custo vêm da provenance do cliente.
Citation precision/coverage são `null`, nunca 1, quando semanticamente inaplicáveis.

## Identidade, resume, status e proveniência

`run_id` é SHA-256 de JSON canônico cobrindo campanha, hipótese/família, fonte,
sistema, incidente, protocolo/holdout, view/grafo/pruning, método/versão/hash, seed,
degradação, explanation mode, repetição, execution mode e configuration hash.
Manifesto e plano antecedem runs. Resume recusa hash divergente, valida checksums e
executa somente pendentes. SUCCESS, skips e falhas por estágio são distintos.
Ground truth aparece somente em evaluation. `SHA256SUMS` cobre a campanha.
Status possíveis: `SUCCESS`, skips de modalidade/protocolo e falhas distintas de
input, feature, modelo, degradação, explicação, replay, pré-condição estatística ou
fronteira interna. Skip nunca é contado como erro nem como execução bem-sucedida.

## Comandos reproduzíveis

```bash
PYTHONPATH=.:correlation-engine .venv-host/bin/python -m experiments.multimodal_v3.cli plan --config campaign.json --output results/experiments-v3
PYTHONPATH=.:correlation-engine .venv-host/bin/python -m experiments.multimodal_v3.cli smoke --campaign-id smoke-v3 --output /tmp/causalods-v3
PYTHONPATH=.:correlation-engine .venv-host/bin/python -m experiments.multimodal_v3.cli run --config campaign.json --campaign-id h1-v3 --hypotheses H1 --executor-factory causalods_campaign_bindings:build_executors --output results/experiments-v3
PYTHONPATH=.:correlation-engine .venv-host/bin/python -m experiments.multimodal_v3.cli run --config campaign.json --campaign-id h2-v3 --hypotheses H2 --executor-factory causalods_campaign_bindings:build_executors --output results/experiments-v3
PYTHONPATH=.:correlation-engine .venv-host/bin/python -m experiments.multimodal_v3.cli run --config campaign.json --campaign-id h3-v3 --hypotheses H3 --sample-size 40 --explanation-modes generic,raw_telemetry,evidence_grounded --executor-factory causalods_campaign_bindings:build_executors --output results/experiments-v3
PYTHONPATH=.:correlation-engine .venv-host/bin/python -m experiments.multimodal_v3.cli run --config campaign.json --campaign-id h4-v3 --hypotheses H4 --severities 0,0.1,0.3,0.5 --executor-factory causalods_campaign_bindings:build_executors --output results/experiments-v3
PYTHONPATH=.:correlation-engine .venv-host/bin/python -m experiments.multimodal_v3.cli resume --config campaign.json --campaign-id h1-v3 --hypotheses H1 --executor-factory causalods_campaign_bindings:build_executors --output results/experiments-v3
PYTHONPATH=.:correlation-engine .venv-host/bin/python -m experiments.multimodal_v3.cli validate --config campaign.json --campaign-id h1-v3 --hypotheses H1 --output results/experiments-v3
PYTHONPATH=.:correlation-engine .venv-host/bin/python -m experiments.multimodal_v3.cli analyze --config campaign.json --campaign-id h1-v3 --hypotheses H1 --output results/experiments-v3
PYTHONPATH=.:correlation-engine .venv-host/bin/python -m experiments.multimodal_v3.cli export-paper --config campaign.json --campaign-id h1-v3 --hypotheses H1 --output results/experiments-v3
```

`causalods_campaign_bindings:build_executors` representa o binding local que
carrega os artefatos v3 e adapters aprovados. A CLI exige essa injeção explícita:
ela não descobre silenciosamente datasets, modelos, brokers ou credenciais. Para
um dry-run, use `plan` ou acrescente `--dry-run` a `run`.
