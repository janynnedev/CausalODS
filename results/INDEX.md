# Índice dos resultados experimentais do CausalODS

## Campanha E2E final

- `e2e/final/final-v1-v2-50/`
  - campanha v1 × v2;
  - 50 repetições por cenário;
  - análises, baselines, comparação e metadados.

- `e2e/final/comparison/`
  - relatório comparativo em Markdown e DOCX.

## Sensibilidade à latência

- `e2e/sensitivity/latency-sensitivity-v1-v2-20260729T191418Z/`
  - campanha final de sensibilidade;
  - resultados v1 e v2;
  - análise corrigida de Top-1;
  - metadados e checksums.

- `e2e/sensitivity-archives/`
  - arquivo compactado e checksum da campanha.

## Explicações RCA

- `offline-explanations/final-v1-v2-50/`
  - explicações geradas para a campanha final.

- `offline-explanations/v1.1-evaluation/`
  - avaliação da cobertura e consistência das explicações.

- `offline-explanations/v1.1-run-a/`
- `offline-explanations/v1.1-run-b/`
  - execuções usadas na análise de determinismo.

## Robustez live

- `robustness/live/robustness-live-v2-repeat10/`
  - campanha original;
  - resultados válidos de Kafka backlog e OpenSearch indisponível;
  - restart antigo preservado como falha técnica do driver.

- `robustness/live/robustness-live-restart-v2-repeat10-ubuntu-warm-20260802T024249Z/`
  - campanha corrigida de restart;
  - dez execuções;
  - controles, warmups, análise, proveniência e checksums.

- `robustness/consolidated/final-robustness-v2/`
  - consolidação dos trinta resultados válidos.

- `robustness/live-archives/`
  - arquivos compactados e checksums das evidências.

## Diagnósticos e execuções intermediárias

Os demais arquivos e diretórios em `e2e/` e `robustness/live/` representam
smokes, diagnósticos, execuções intermediárias e evidências auxiliares.

Eles devem permanecer preservados, mas não entram automaticamente nas
métricas finais do artigo.
