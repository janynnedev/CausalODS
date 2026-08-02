# Pacotes importados do CausalODS

Este diretório preserva os pacotes originais recebidos ou gerados
durante o desenvolvimento do CausalODS.

Os arquivos em `extracted/` representam cópias integrais dos diretórios
originais. Os ZIPs e seus checksums originais foram mantidos ao lado da
cópia extraída.

Esses pacotes são históricos e não representam os caminhos canônicos
de execução do projeto.

## Pacotes

| Pacote | Finalidade |
|---|---|
| `causalods_explanations_v1` | Primeira implementação das explicações RCA |
| `causalods_explanations_v1_1` | Revisão e avaliação das explicações determinísticas |
| `causalods_latency_sensitivity` | Campanha de sensibilidade à latência |
| `causalods_post_campaign_v2` | Scripts de baselines e análises pós-campanha |
| `causalods_robustness_live_v1` | Implementação inicial da robustez live |

## Caminhos canônicos

- código experimental: `../../experiments/`;
- motor RCA: `../../correlation-engine/`;
- documentação atual: `../../docs/`;
- resultados experimentais: `../../results/`.

Os pacotes deste diretório não devem ser importados diretamente pelo
runtime e não devem substituir automaticamente os arquivos canônicos.
