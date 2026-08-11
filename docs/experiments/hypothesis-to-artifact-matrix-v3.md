# Matriz hipótese → artefato v3

| Hipótese | Experimento/configuração | Métricas | Arquivos | Uso futuro |
|---|---|---|---|---|
| H1 | H1-A: baselines vs full XGBoost | FPR, precision, recall, macro-F1, Top-1/3, MRR, latency | `h1-overall.csv`, `h1-by-dataset.csv`, `h1-by-scenario.csv` | desempenho geral |
| H1 | H1-B: NO_GRAPH, GRAPH, GRAPH_BUSINESS | anteriores + candidate counts, pruning rate, GT pruned | `h1-structural-ablation.csv`, `h1-pruning.csv`, `h1-paired-statistics.csv` | contribuição estrutural |
| H1 | H1-C: cinco modality views | desempenho e capability skips | `h1-modality-ablation.csv` | ablação multimodal |
| H2 | sete métodos, full view/splits idênticos | qualidade, p50/95/99, throughput, memória, tamanho, treino, estabilidade | `h2-model-comparison.csv`, `h2-performance.csv`, `h2-runtime-cost.csv`, `h2-seed-stability.csv`, `h2-paired-statistics.csv` | comparação de modelos |
| H3 | três modos no mesmo RCAResult | grounding, citações, unsupported claims, uncertainty, fallback, tokens/custo | `h3-automatic-evaluation.csv`, `h3-by-mode.csv`, `h3-fallbacks.csv`, `h3-cost-latency.csv` | avaliação automática |
| H3 | avaliação humana cega | utilidade, clareza, confiança, rastreabilidade, incerteza | `h3-human-evaluation-blinded.csv`, `h3-human-condition-map.json` | estudo humano |
| H4 | operadores aplicáveis × 0/10/30/50 | Top-1/3, MRR, FPR, confidence, calibration, abstention, latency, loss, delta | `h4-by-operator.csv`, `h4-by-severity.csv`, `h4-by-modality.csv`, `h4-confidence-abstention.csv`, `h4-degradation-curves.csv` | curvas de robustez |

Artefatos transversais: manifesto, plano JSON/CSV, runs, `SHA256SUMS`,
`summary-metrics.json`, `hypothesis-summary.md` e
`experiment-validity-report.md`. A síntese usa linguagem neutra e nunca declara
automaticamente uma hipótese confirmada.

As comparações H1/H2 também produzem `statistics/paired-comparisons.json` com
valores A/B, delta absoluto/relativo, bootstrap IC95%, McNemar e ajuste Holm. O
mapa humano permanece separado do arquivo entregue aos avaliadores; ratings
posteriores geram `h3-human-agreement.json` apenas com pares suficientes.
