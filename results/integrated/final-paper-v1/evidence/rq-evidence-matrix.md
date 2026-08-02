# Matriz de perguntas de pesquisa, hipóteses e evidências

## RQ1

**Em que medida a integração de OpenTelemetry, Kafka e o pipeline
CausalODS fornece uma base unificada, rastreável, confiável e robusta
para análise automatizada de causa raiz?**

### H1

A versão v2 do pipeline aumenta significativamente a proporção de
execuções E2E aprovadas e de decisões confiáveis em relação à versão v1.

### H2

O pipeline v2 preserva processamento, rastreabilidade e identificação
da causa raiz diante de diferentes intensidades de falha e de
interrupções de componentes da infraestrutura.

### Evidências

| Bloco | Métricas |
|---|---|
| E2E v1 × v2 | taxa de aprovação, Top-1, confiabilidade, incidentes e McNemar |
| Sensibilidade | Top-1 e confiabilidade por serviço e atraso |
| Robustez | validade técnica, perda, duplicação, lag, recuperação e restauração |
| Proveniência | versão do modelo, pipeline, trace, RCAResult e incidentes |

## RQ2

**Em que medida as evidências produzidas pelo CausalODS podem ser
convertidas em explicações auditáveis, consistentes e reproduzíveis
sobre a causa raiz identificada?**

### H3

Os RCAResults podem ser transformados em explicações estruturadas com
cobertura integral das evidências disponíveis e comportamento
determinístico no ambiente avaliado.

### Evidências

| Bloco | Métricas |
|---|---|
| Explicações v1.1 | cobertura, erros, campos ausentes e consistência |
| Runs A e B | igualdade das serializações e determinismo observado |
| RCAResult | scores, ranking, confiabilidade e proveniência |
| Robustez | preservação da explicação após recuperação do pipeline |

## Critério de interpretação

As hipóteses serão consideradas apoiadas quando as evidências
experimentais mostrarem resultados consistentes nos cenários avaliados.

O estudo não deve afirmar generalização para qualquer sistema
distribuído, carga de produção ou arquitetura fora do ambiente
experimental observado.
