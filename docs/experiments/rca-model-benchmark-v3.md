# Benchmark de modelos RCA v3

O benchmark compara sete métodos sobre o mesmo `RCAFeatureDatasetV3`:

- `temporal-baseline`;
- `max-zscore`;
- `first-anomalous`;
- `logistic-regression`;
- `random-forest`;
- `xgboost-rca-v3`;
- `mlp-light-v3`.

GNN está explicitamente fora deste gate. Nenhum arquivo ou modelo v2 é utilizado
como destino de escrita.

## Protocolo

O dataset de entrada é exclusivamente o JSON determinístico produzido por
`RCAFeatureDatasetV3.to_json()`, em modo supervisionado. Todas as linhas precisam
ter `RCAServiceTargetV3` com `label_status=labeled`. A validação impede que um
`incident_id` atravesse train, validation e test.

O loader científico não possui fallback: `dataset_sha256` é obrigatório e é
recalculado após remover o próprio campo. Também são verificados
`rows_sha256`, `feature_view_sha256`, `registry_sha256`, hashes e IDs por split,
fingerprint, contagens e schema base. A ordem física das propriedades JSON é
ignorada; valores são materializados na ordem de `manifest.feature_names`.

LR, Random Forest, XGBoost e MLP recebem exatamente `feature_names` na mesma
ordem. Imputação e scaling pertencem ao `sklearn.Pipeline` e são ajustados somente
no train. O validation escolhe hiperparâmetros; test é usado apenas na avaliação.
`--tuning-budget` limita igualmente a quantidade de configurações testadas por
modelo supervisionado. Seeds controlam amostragem da grade e os estimadores.
Cada trial registra parâmetros, seed, duração, status, falha e métricas de
validation. A seleção padrão é `validation_mrr`, com desempate por Top-1,
macro-F1 e JSON canônico dos hiperparâmetros. Ela pode ser alterada por
`--selection-metric`.

A política final é `final-fit-train-only-v1`: o pipeline vencedor, já ajustado
somente no train, é aplicado ao test sem refit. A imputação usa
`SimpleImputer(strategy="median", keep_empty_features=True)`, preservando colunas
totalmente ausentes no train. O manifesto registra estatísticas de imputação,
colunas ausentes, scaling e ordem das features. Baselines registram zero trials
e o motivo `non-trainable-baseline`.

Os baselines não são treinados:

- temporal: menor `distance_to_first_failure`;
- max-zscore: maior magnitude entre latency/CPU/memory z-score;
- first-anomalous: primeira distância com evidência anômala.

## Métricas e estatística

Por serviço são calculados precision, recall, macro-F1 e FPR. Por incidente, os
scores são ordenados com desempate por `service_name`, produzindo Top-1, Top-3 e
MRR. `BenchmarkRuntimePolicy` define warm-up, repetições batch, repetições
single-row, coleta de memória e versão. O runner exporta throughput batch e
p50/p95/p99 single-row separadamente. `python_tracemalloc_peak_bytes` mede apenas
alocações Python, não a memória RSS total. Tuning e fit final são separados.

Intervalos de 95% usam bootstrap por incidente. A comparação pareada usa o mesmo
conjunto de incidentes, teste exato de McNemar para Top-1 e bootstrap da diferença.
A estabilidade agrega média e desvio padrão entre seeds.

Antes de comparação, cada previsão de incidente é reconstruída por uma função
única. Ela valida identidade, ground truth, candidatos, scores e ordenação; deriva
novamente `first_root_rank`, reciprocal rank, Top-1 e Top-3. Um reciprocal rank
serializado é somente uma redundância auditável e deve coincidir com o valor
recalculado com tolerância absoluta `1e-12`. Ele nunca é a fonte da comparação.

O diretório é `model/dataset-prefix/seed-N` e falha se já existir;
`--overwrite` remove somente esse diretório antes de recriá-lo. Cada execução
exporta modelo, manifesto, schema, ambiente, métricas, confusion, predições,
hiperparâmetros e trials.

O contrato de integridade evita autorreferência circular:

- `manifest-core.json` contém o contrato da execução;
- `artifact-hashes.json` cobre modelo, schema, métricas, confusion, predições,
  ambiente, hiperparâmetros e trials;
- `run-integrity.json` contém os hashes dos dois arquivos e o `run_fingerprint`.

O fingerprint cobre deterministicamente manifesto, mapa de hashes, dataset,
schema, modelo, preprocessing, trials, runtime policy, métricas e predições.
`load_verified_artifact()` é a única entrada segura compartilhada por evaluate,
compare e report; para compare/report ela não carrega Joblib. Nomes de arquivos
precisam ser basenames e permanecer dentro do diretório do artefato.

Como joblib/pickle pode executar código, somente artefatos de origem confiável
devem ser usados. SHA-256 detecta divergência em relação ao fingerprint registrado,
mas não comprova autoria nem substitui assinatura/autenticidade. JSON avulso em
compare/report é recusado por padrão e requer `--unsafe-unverified-input`.

## CLI

```bash
cd correlation-engine

../.venv-host/bin/python -m multimodal_v1.benchmark_v3.cli train \
  --dataset ../artifacts/rca-dataset-v3.json \
  --model xgboost-rca-v3 --output ../results/benchmark-v3 --seed 42

../.venv-host/bin/python -m multimodal_v1.benchmark_v3.cli evaluate \
  --dataset ../artifacts/rca-dataset-v3.json \
  --artifact ../results/benchmark-v3/xgboost-rca-v3/DATASET_PREFIX/seed-42 \
  --output ../results/benchmark-v3/xgboost-evaluation.json

../.venv-host/bin/python -m multimodal_v1.benchmark_v3.cli benchmark-all \
  --dataset ../artifacts/rca-dataset-v3.json \
  --output ../results/benchmark-v3 --seeds 17,42,73 --tuning-budget 3

../.venv-host/bin/python -m multimodal_v1.benchmark_v3.cli compare \
  --left model-a-metrics.json --right model-b-metrics.json \
  --output comparison.json

../.venv-host/bin/python -m multimodal_v1.benchmark_v3.cli export-report \
  --results model-a-metrics.json model-b-metrics.json --output report.md
```
