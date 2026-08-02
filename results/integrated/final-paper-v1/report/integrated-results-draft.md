# Resultados experimentais integrados — CausalODS

## 1. Visão geral

A avaliação do CausalODS foi organizada em quatro blocos:

1. comparação E2E entre as versões v1 e v2;
2. sensibilidade à intensidade das falhas de latência;
3. robustez diante de falhas de infraestrutura;
4. cobertura e consistência das explicações auditáveis.

Os resultados intermediários, smokes e diagnósticos foram preservados,
mas as métricas finais usam apenas as campanhas explicitamente
identificadas no inventário integrado de evidências.

## 2. Comparação E2E v1 × v2

A campanha final contém 250 execuções para cada versão, distribuídas
entre cinco cenários e cinquenta repetições por cenário.

A versão v1 aprovou 150 das 250 execuções. As cem reprovações
concentraram-se nos cenários de latência, nos quais o serviço Top-1
frequentemente estava correto, mas a confiança não ultrapassava o gate
de confiabilidade.

A versão v2 aprovou as 250 execuções. A diferença entre as versões foi
avaliada por teste pareado de McNemar, conforme os artefatos da análise
final.

### Resultado principal

| Versão | Aprovadas | Total | Taxa |
|---|---:|---:|---:|
| v1 | 150 | 250 | 60,0% |
| v2 | 250 | 250 | 100,0% |

## 3. Sensibilidade à latência

A campanha de sensibilidade avaliou falhas de latência nos serviços
`payment-service` e `order-service`, com atrasos de 250, 500, 750,
1000, 1500, 2000 e 2500 ms.

Foram realizadas dez repetições por combinação de versão, serviço e
intensidade.

### Resultados observados

- v1: 129 de 140 Top-1 corretos, mas nenhuma detecção classificada como
  confiável pelo gate usado na campanha;
- v2: 140 de 140 Top-1 corretos;
- v2: 138 de 140 decisões confiáveis;
- `payment-service` com 250 ms: 8 de 10 decisões confiáveis;
- `payment-service` com 500 ms ou mais: 10 de 10;
- `order-service`: 10 de 10 em todas as intensidades avaliadas.

Esses resultados mostram que a versão v2 preservou a localização Top-1
mesmo nos dois casos em que a confiança ficou abaixo do limite.

## 4. Robustez

O conjunto final de robustez é composto por trinta execuções válidas:

| Cenário | Execuções |
|---|---:|
| Kafka backlog | 10 |
| OpenSearch indisponível | 10 |
| Reinicialização do Correlation Engine | 10 |
| Total | 30 |

As execuções antigas do cenário de reinicialização foram preservadas,
mas excluídas das métricas finais porque o driver experimental dependia
de uma reinicialização automática do container que não ocorreu.

A campanha corrigida iniciou explicitamente o container e confirmou a
reinicialização pela mudança de `StartedAt`.

A validação final deve reportar, por cenário:

- validade técnica;
- perda e duplicação;
- recuperação para lag zero;
- número de RCAResults;
- serviço Top-1;
- decisão de confiabilidade;
- proveniência;
- restauração do ambiente.

Os tempos absolutos entre os cenários executados em ambientes
diferentes não devem ser comparados diretamente.

## 5. Explicações auditáveis

A avaliação v1.1 processou 500 documentos da campanha E2E final.

Resultados principais:

- cobertura das explicações: 500 de 500;
- resultados confiáveis: 300;
- resultados não confiáveis: 200;
- comportamento determinístico observado entre as execuções A e B no
  mesmo ambiente, código, entradas e forma de serialização.

A evidência não sustenta uma afirmação de determinismo universal entre
diferentes versões do Python, sistemas operacionais, caminhos de
arquivos ou ordens arbitrárias de entrada.

## 6. Resposta preliminar à RQ1

Os resultados apoiam a hipótese de que OpenTelemetry e Kafka podem
fornecer uma base rastreável para RCA automatizada no ambiente
avaliado.

A versão v2 obteve maior taxa de aprovação E2E, manteve localização
Top-1 diante das intensidades de latência avaliadas e recuperou o
processamento após os três tipos de perturbação de infraestrutura.

## 7. Resposta preliminar à RQ2

Os resultados apoiam a hipótese de que os RCAResults podem ser
transformados em explicações auditáveis e consistentes com as
evidências do pipeline.

A cobertura observada foi integral no conjunto avaliado, e duas
execuções independentes no mesmo ambiente produziram saídas
determinísticas.

## 8. Limitações que devem acompanhar os resultados

- ambiente experimental controlado;
- arquitetura principal com dois serviços de negócio;
- uso de falhas injetadas;
- pequeno conjunto saudável na campanha de sensibilidade;
- campanhas de robustez executadas em mais de um ambiente;
- warmups excluídos das métricas após recriação dos serviços;
- determinismo das explicações restrito às condições avaliadas;
- ausência de cargas de produção reais;
- impossibilidade de generalizar diretamente os tempos absolutos de
  recuperação.
