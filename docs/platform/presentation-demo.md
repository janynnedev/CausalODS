# Campanha demonstrativa CausalODS

Implementação para apresentação: `scientific_result=false`. Não executa campanhas H1–H4, não treina modelos, não altera resultados congelados e não limpa índices ou volumes.

Branch: `fix/codex`.

BASE_COMMIT registrado nesta tarefa: `a1d026e2a5ec94194e99ae58937666bc19307caf`.

## Estado da entrega

Implementado, aguardando execução no Dev Container. Nenhum Python, pytest, Docker, serviço ou chamada OpenAI foi executado durante a edição no Windows. PASS/FAIL de testes, freeze H3 e run real: **NOT_RUN**. `READY_FOR_10_RUN_PRESENTATION_DEMO=NO` até a revisão do run único.

O checkout já continha 1.872 exclusões locais quando a tarefa começou, inclusive em diretórios científicos. A lista exata foi preservada em `.git/presentation-demo-initial-status.txt`. Esta tarefa não restaurou nem modificou essas exclusões. Portanto, não se afirma que o checkout científico esteja íntegro; afirma-se apenas que a tarefa não acrescentou alterações nesses caminhos. Isso pode afetar testes existentes e o snapshot completo do `e2e-live` antigo. Não regenere artefatos ou hashes para contornar essas ausências.

`docs/CODEX_CONTEXT.md`, citado no AGENTS, não existe com esse nome. O contexto está em `docs/codex-context.md`; ele contém descrições históricas, enquanto o runtime atual está em `platform_v3`.

## Comportamento implementado

- O comando público `e2e-live` mantém os argumentos, os 19 estágios e o ciclo próprio de configuração/restauração. A implementação compartilhada `execute_live_incident` permite à campanha controlar a falha global; seus relatórios deixam a restauração pendente no manifest da campanha.
- `presentation-demo` configura/verifica o runtime, configura a falha uma vez, executa os incidentes, exporta documentos operacionais pelos IDs reais, seleciona o primeiro sucesso confiável disponível e exporta sete PNGs. IDs repetidos entre runs são rejeitados.
- `finally` restaura/verifica `CAUSALODS_FAULT_MODE=none` e devolve o runtime ao provider determinístico. Falha de restauração torna a campanha FAILED.
- O provider padrão permanece determinístico. O overlay `docker-compose.presentation-demo.yml` é usado apenas com `--live-llm`; disponibiliza `experiments/` em `/app/experiments:ro`, compatível com o `WORKDIR /app` e o build context `./correlation-engine`. Os imports transitivos do cliente H3 também ficam disponíveis nesse volume.
- O cliente H3 é importado diretamente, sem cópia do contrato. `load_manifest()` valida os hashes originais. A dependência adicional `tiktoken==0.14.0` corresponde ao freeze. Modelo, temperatura, reasoning effort, budget e limite de saída vêm do contrato existente; nenhum arquivo H3 foi editado.
- Mesmo quando o provider OpenAI está habilitado, somente bundles com correlation IDs demonstrativos podem usá-lo. Os demais usam `ExplanationService()` sem cliente.
- O resultado RCA é persistido antes da explicação. Uma falha da LLM preserva a RCA, mas não produz um sucesso LLM. Modelo divergente, timeout, schema inválido, fallback ou falta de chave causam FAILED.
- A chave não é argumento de CLI nem campo de artefato. O Compose recebe a variável somente no overlay explícito. Não execute `docker compose config` com esse overlay e a chave no ambiente, pois o próprio Compose pode imprimir variáveis interpoladas. A campanha captura diagnósticos do Compose sem serializá-los e não registra textos arbitrários de exceções.
- O export rejeita nomes sensíveis, credenciais Bearer e o valor da chave ativa, inclusive dentro de JSON serializado. A mesma verificação protege documentos demonstrativos antes da persistência operacional.
- A projeção preserva `propagation_path_json`, `recommendations_json` e o `PruningResult.to_dict()` real, com contagens e candidatos. Os mappings são aditivos; o provisionamento existente aplica `put_mapping` aos índices já criados.
- Explanation Audit consulta citações no datasource de explicações. Root Cause Detail separa ranking, SHAP e evidências. Event Evidence Graph mantém a auditoria tabular, agora com pruning. Transformações restringem as colunas apresentadas.
- O exportador usa NetworkX/Matplotlib já presentes, backend Agg, PNG 2400×1350 e um único layout com seed 42. Referências compostas por event ID e source reference preservam a identidade dos nós. Cada estágio adiciona somente classificações existentes. Associação temporal/estatística não é tratada como causalidade; TOPOLOGY_DEPENDENCY é causal_candidate; SHAP é contribuição do modelo.

## Comandos no Dev Container

Execute da raiz do repositório, com o venv do projeto ativado. Não execute os blocos seguintes se o anterior falhar.

### 1. Conferência e testes offline (sem chave)

```bash
git branch --show-current
git rev-parse HEAD
git status --short
unset OPENAI_API_KEY
export PYTHONPATH="$PWD:$PWD/correlation-engine"
python -m pip install -r correlation-engine/requirements-multimodal-v3.txt
python -m pytest -q \
  correlation-engine/tests/test_presentation_demo.py \
  correlation-engine/tests/test_platform_v3.py \
  correlation-engine/tests/test_event_evidence_graph_v3.py \
  correlation-engine/tests/test_incident_evidence_assembler_v3.py \
  correlation-engine/tests/test_rca_features_v3.py \
  correlation-engine/tests/test_explanations_v2.py \
  correlation-engine/tests/test_rca_contracts.py \
  experiments/tests/test_h3_openai_freeze_v3.py \
  experiments/tests/test_h3_scientific_materializer_v3.py
python -c 'from experiments.final_campaigns_v3.h3_openai_freeze import load_manifest; load_manifest(); print("H3_FREEZE=PASS")'
```

Qualquer drift H3 é uma regressão: pare, sem atualizar hashes. Arquivos científicos ausentes precisam ser investigados como condição preexistente do checkout.

### 2. Infraestrutura e conferência operacional

```bash
docker compose -f docker-compose.yml -f docker-compose.platform-v3.yml build platform-v3-runtime
docker compose -f docker-compose.yml -f docker-compose.platform-v3.yml up -d
python -m platform_v3 health
```

O modelo operacional existente deve estar disponível no volume `artifacts/platform-v3/models`. A demo valida esse modelo; não o publica nem o regenera. Aguarde o health do runtime indicar `ready=true` antes de prosseguir.

### 3. Preflight H3 explícito (três chamadas pagas, fora da ciência)

Somente depois dos testes offline e health aprovados:

```bash
read -rsp 'OPENAI_API_KEY: ' OPENAI_API_KEY
printf '\n'
export OPENAI_API_KEY
python -m experiments.final_campaigns_v3.h3_live_preflight
```

O preflight existente não grava resultados científicos. Não use `set -x` neste terminal.

### 4. Um único run real

```bash
python -m platform_v3 presentation-demo \
  --runs 1 \
  --scenario payment_failure \
  --live-llm \
  --export-graphs \
  --output artifacts/presentation-demo
unset OPENAI_API_KEY
```

**Pare aqui e revise o resultado antes de solicitar dez runs.**

O comando imprime o manifest com `campaign_id`. Os arquivos ficam em `artifacts/presentation-demo/<campaign-id>/`. Verifique no manifest: `campaign_status=SUCCESS`, `runs_successful=1`, `fault_restored=true`, `runtime_provider_restored=true`, `scientific_result=false` e sete caminhos em `graph_images`. No run: os cinco IDs preenchidos, RCA `payment-service`, provider `llm`, modelo `gpt-5.4-mini-2026-03-17`, `fallback=false`, `valid=true`, nós/arestas maiores que zero. O campo `reliable` deve ser relatado como observado; a seleção prefere um run confiável quando disponível.

Abra `runs/run-01/{report,incident,rca,explanation}.json`, `featured/identifiers.json`, os sete PNGs e `README.md`. O relatório de cada run delega a restauração ao manifest global.

URLs: Grafana http://localhost:3000, Prometheus http://localhost:9090, Tempo http://localhost:3200, runtime http://localhost:8090/health. Use os IDs do featured run nos filtros dos dashboards e no Explore/Tempo.

### 5. Dez runs — somente depois de nova autorização

```bash
read -rsp 'OPENAI_API_KEY: ' OPENAI_API_KEY
printf '\n'
export OPENAI_API_KEY
python -m platform_v3 presentation-demo \
  --runs 10 \
  --scenario payment_failure \
  --live-llm \
  --export-graphs \
  --output artifacts/presentation-demo
unset OPENAI_API_KEY
```

Sem `--live-llm`, a campanha utiliza o fallback determinístico e não requer chave. `--no-export-graphs` desabilita as imagens. O diretório de saída deve permanecer dentro de `artifacts/presentation-demo`, inclusive quando personalizado, para proteger os namespaces científicos.

## Verificação final desta edição

Branch e commit permaneceram os mesmos. `git diff --check` e leitura sintática de JSON via PowerShell são verificações estáticas; não substituem pytest, validação Grafana ou E2E. As exclusões iniciais foram comparadas ao estado final. Nenhum commit ou push foi feito. IDs de campanha, incidentes, traces, confidence e imagens reais permanecem **NOT_RUN** até a execução pelo usuário.

## Inventário desta tarefa

Arquivos criados:

- `correlation-engine/platform_v3/explanation_provider.py`
- `correlation-engine/platform_v3/presentation_demo.py`
- `correlation-engine/platform_v3/presentation_graph.py`
- `correlation-engine/platform_v3/presentation_safety.py`
- `correlation-engine/tests/test_presentation_demo.py`
- `docker-compose.presentation-demo.yml`
- `docs/platform/presentation-demo.md`

Arquivos alterados:

- `correlation-engine/platform_v3/cli.py`
- `correlation-engine/platform_v3/constants.py`
- `correlation-engine/platform_v3/documents.py`
- `correlation-engine/platform_v3/e2e_live.py`
- `correlation-engine/platform_v3/runtime.py`
- `correlation-engine/requirements.txt`
- `docker-compose.platform-v3.yml`
- `infrastructure/platform-v3/opensearch/templates/explanations.json`
- `infrastructure/platform-v3/opensearch/templates/incidents.json`
- `infrastructure/platform-v3/grafana/dashboards/explanation-audit.json`
- `infrastructure/platform-v3/grafana/dashboards/root-cause-detail.json`
- `infrastructure/platform-v3/grafana/dashboards/event-evidence-graph.json`

```text
BRANCH = fix/codex
BASE_COMMIT = a1d026e2a5ec94194e99ae58937666bc19307caf
CURRENT_COMMIT = a1d026e2a5ec94194e99ae58937666bc19307caf
GIT_STATUS = 12 arquivos rastreados modificados + 7 novos + 1872 exclusões preexistentes preservadas
TESTES_EXECUTADOS = somente verificações estáticas de Git e sintaxe JSON
TESTES_PASS = NOT_RUN (pytest)
TESTES_FAIL = NOT_RUN (pytest)
H3_FREEZE_STATUS = NOT_RUN; fontes e manifest H3 sem alterações desta tarefa
PRESENTATION_DEMO_1_RUN_STATUS = NOT_RUN
CAMPAIGN_ID = NOT_RUN
CORRELATION_ID = NOT_RUN
TRACE_ID = NOT_RUN
INCIDENT_ID = NOT_RUN
RCA_RESULT_ID = NOT_RUN
EXPLANATION_ID = NOT_RUN
ROOT_CAUSE_SERVICE = NOT_RUN
CONFIDENCE = NOT_RUN
RELIABLE = NOT_RUN
LLM_PROVIDER = NOT_RUN
LLM_MODEL = NOT_RUN
LLM_FALLBACK = NOT_RUN
LLM_VALID = NOT_RUN
GRAPH_NODE_COUNT = NOT_RUN
GRAPH_EDGE_COUNT = NOT_RUN
PRUNING_BEFORE = NOT_RUN
PRUNING_AFTER = NOT_RUN
GRAPH_IMAGES_01_TO_07 = NOT_RUN
GRAFANA_URL = http://localhost:3000
PROMETHEUS_URL = http://localhost:9090
TEMPO_URL = http://localhost:3200
FAULT_RESTORED = NOT_RUN (nenhuma falha foi configurada nesta edição)
SCIENTIFIC_ARTIFACTS_CHANGED_BY_THIS_TASK = false
SCIENTIFIC_ARTIFACTS_CHANGED = true (1872 exclusões já presentes antes da tarefa)
READY_FOR_10_RUN_PRESENTATION_DEMO = NO
```
