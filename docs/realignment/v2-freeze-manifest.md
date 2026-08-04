# Manifesto de congelamento da implementação trace-only v2

Data: 2026-08-03  
Commit: `fa7835f25bdeda403aedc55019181c8317cf4048`  
Branch observada: `fix/codex`  
Commit subject: `feat: construído o relatório integrado de evidências experimentais`

## Regra de congelamento

Os arquivos, contratos, artefatos e resultados abaixo constituem a linha de base v2. A v3 não pode sobrescrever, renomear, remover ou reutilizar seus nomes de versão, modelos, datasets, tópicos de produção ou resultados. Mudanças corretivas futuras na v2 exigem novo commit de freeze e hashes atualizados.

## Versões e pontos de entrada

- Pipeline: `causalods-rca-v2`, selecionado por `RCA_MODEL_VERSION=v2` no `docker-compose.yml`.
- Feature schema: `rca-features-v2` em `correlation-engine/models/rca_feature_schema_v2.py`.
- Dataset: `rca-dataset-v2`, 10.000 linhas/5.000 samples, seed 42, conforme manifesto do modelo.
- Modelo: `xgboost-rca-v2` em `correlation-engine/training/models/xgboost_rca_v2.pkl`.
- Ingestão congelada: tópico `traces`, OTLP `otlp_proto`, `TraceConsumer`.
- Resultado RCA: `RCAResult.schema_version=1.0`; persistência em `causalods-rca-results`.
- Explicação offline associada: `causalods-explanation-v1.1` (determinística, não LLM).

## Hashes SHA-256

| Artefato | SHA-256 |
|---|---|
| `models/rca_feature_schema.py` | `57c9bfa7074fb13f3dc03e6ec4e71f513b3d27ba7f602a01c70993f5a978cfc3` |
| `models/rca_feature_schema_v2.py` | `1d48fe99ca89d6f46d8fe552a9c2a4df7dc6b219ac59f4f437015ca93ed876a0` |
| `pipeline/feature_pipeline.py` | `d1cef03efd348153cd32d3c0d6f103446e336bfb116ce3bed834f1dcabbf3390` |
| `pipeline/prediction_pipeline.py` | `f371ddde888509832b3be764651b2f6e30175f1a0f8eb80622ff4b88c8cc2d6d` |
| `pipeline/prediction_pipeline_v2.py` | `fa89c6634c2a6b15699d6e2c44e17a9098f47dce8c078aae9825c13da14ea723` |
| `pipeline/rca_pipeline.py` | `654b10c14dd947b8599b8722f8f30192d87901656650fee2534ab42ee956006e` |
| `models/rca_context.py` | `dc39524c8778cf5ba118e7a9974c83ad31bcf7f4218e2dd8c53b646db042a303` |
| `models/rca_result.py` | `742cb0b9519eb71b4c50c3d3f17aa69fd582a4ac330a062357ab613065fe05f5` |
| `training/models/xgboost_rca.pkl` | `f0160972e4f977fd6cea2a9bf4819b75f2cc7265b8a75d6fe9ce7ed8b355c4c6` |
| `training/models/xgboost_rca_v2.pkl` | `f418d45f92d91f338092768c1ee9e2dc257047b422d98a24b0be65cae01379d8` |
| `training/models/xgboost_rca_v2.manifest.json` | `b9fff2f06aa162acd00228860d5e9910ef303d585dc80bc0e80dc968e5221523` |
| `training/data/rca_dataset_v2.csv` | `cb30ebea60d818ac59c506493900392b89978232cff4c12336a5e3a10dd71d2a` |

Todos os caminhos da tabela são relativos a `correlation-engine/`.

## Contrato de features v2

Ordem imutável das 16 colunas de modelo:

```text
request_count
error_count
error_rate
avg_latency_ms
max_latency_ms
incoming_dependencies
outgoing_dependencies
dependency_count
is_first_failure
failure_order
failure_lead_time_ms
latency_zscore
anomaly_score
is_anomalous
exclusive_latency_ms
exclusive_latency_ratio
```

Metadados do dataset v2: `sample_id`, `scenario`, `service_name`, `root_cause_service`, `fault_delay_ms`, `generation_seed`. Target: `root_cause`.

## Campanhas e evidências congeladas

- `results/e2e/final/final-v1-v2-50/`: campanha completada, 50 repetições × 5 cenários por versão; v2 obteve 250/250 casos aprovados. A v1 teve 150/250, preservada como comparação.
- `results/e2e/sensitivity/latency-sensitivity-v1-v2-20260729T191418Z/`: 7 delays, 2 serviços, controle saudável, 10 repetições; 150 execuções por versão.
- `results/offline-explanations/final-v1-v2-50/` e execuções v1.1: explicações/evaluations determinísticas offline.
- `results/robustness/consolidated/final-robustness-v2/`: 30/30 execuções técnica e validamente concluídas, 10 por cenário Kafka backlog/OpenSearch indisponível/restart.
- `results/robustness/live/robustness-live-restart-v2-repeat10-ubuntu-warm-20260802T024249Z/`: 10/10 reinícios concluídos.
- `results/integrated/final-paper-v1/`: pacote integrado de evidências, tabelas, figuras, proveniência e relatório.

As próprias campanhas contêm arquivos `SHA256SUMS`/`FINAL_SHA256SUMS` quando produzidos pelo runner; eles são a autoridade para integridade interna dos respectivos diretórios.

## Comandos de verificação

```bash
git rev-parse HEAD

cd correlation-engine
.venv/bin/python -m pytest tests -q

cd ..
correlation-engine/.venv/bin/python -m pytest experiments/tests -q

sha256sum \
  correlation-engine/models/rca_feature_schema_v2.py \
  correlation-engine/pipeline/prediction_pipeline_v2.py \
  correlation-engine/pipeline/rca_pipeline.py \
  correlation-engine/training/models/xgboost_rca_v2.pkl \
  correlation-engine/training/models/xgboost_rca_v2.manifest.json \
  correlation-engine/training/data/rca_dataset_v2.csv

cd services/order-service && mvn test
cd ../payment-service && mvn test
cd .. && ./gradlew test
```

Importante: os testes v2 que leem modelo/dataset usam caminhos relativos; a suíte do engine deve ser iniciada dentro de `correlation-engine/` até que uma correção versionada seja aprovada. Os comandos Java podem exigir cache gravável e permissão de attach do agente Mockito.

## Baseline de testes em 2026-08-03

| Suíte | Resultado observado |
|---|---|
| Engine Python, CWD `correlation-engine/` | `115 passed, 1 skipped, 3 warnings` |
| Experimentos Python, raiz | `22 passed` |
| Agregado Python iniciado na raiz | `131 passed, 1 skipped, 6 failed` por caminhos relativos; os mesmos 6 passaram no CWD correto |
| Order Service Maven | 1 teste, erro ambiental no auto-attach Mockito/Byte Buddy; nenhuma asserção falhou |
| Payment Service Maven | 1 teste, mesmo erro ambiental; nenhuma asserção falhou |
| Projeto Gradle legado | bloqueado: Gradle ausente e wrapper sem cache gravável em `$HOME` |

Warnings Python: três `PendingDeprecationWarning` do mapa de cores SHAP/Matplotlib. O teste ignorado é marcado como integração e requer infraestrutura externa/Docker.

## Invariantes para a v3

1. Usar `multimodal-schema-v1`, `causalods-rca-v3` e `xgboost-rca-v3`; não alterar `rca-features-v2` nem seus 16 campos.
2. Criar novo modelo, manifesto, dataset e hashes; nunca regravar `xgboost_rca_v2.pkl`.
3. Manter uma rota/configuração explícita para executar v2 sem importar componentes v3.
4. Criar tópicos e índices v3 ou aliases novos; não migrar mappings v2 in-place.
5. Gravar resultados experimentais v3 em novos diretórios, preservando todas as campanhas listadas.
6. Incluir regressão v2 no gate de v3 e comparar shadow outputs antes de qualquer cutover.
