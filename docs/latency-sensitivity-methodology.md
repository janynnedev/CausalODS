# Campanha de Sensibilidade à Intensidade da Latência

## Objetivo

Medir como a capacidade de detecção do CausalODS varia com a magnitude da
latência introduzida, mantendo constantes a arquitetura, o serviço alvo, a
taxa de injeção e a configuração de confiabilidade.

Esta campanha complementa o experimento final de 50 repetições. O experimento
final demonstrou estabilidade em 1.500 ms; a campanha de sensibilidade procura
o menor atraso no qual cada versão passa a produzir uma decisão operacional
confiável.

## Hipóteses

- H1: a taxa de detecção aumenta ou permanece estável conforme o delay cresce;
- H2: a v2 atinge níveis de detecção de 80% e 95% em delays menores que a v1;
- H3: o controle saudável mantém taxa de falso positivo próxima de zero;
- H4: quando há detecção, o serviço Top-1 permanece correto;
- H5: o ganho da v2 não depende exclusivamente do ponto de 1.500 ms.

## Variáveis

Variável independente:

- versão do pipeline: v1 ou v2;
- serviço alvo: payment-service ou order-service;
- delay configurado.

Variáveis dependentes:

- `is_reliable`;
- criação de incidente;
- sucesso end-to-end;
- confiança;
- Top-1;
- duração HTTP.

Controles:

- `fault_rate=1.0`;
- `fault_seed=42`;
- mesmos serviços e infraestrutura;
- runtime validado antes de cada bloco;
- ordem das condições randomizada deterministicamente dentro de cada iteração;
- controle saudável em cada iteração.

## Matriz padrão

- versões: v1 e v2;
- serviços: payment-service e order-service;
- delays: 250, 500, 750, 1.000, 1.500, 2.000 e 2.500 ms;
- 10 repetições por condição;
- 10 controles saudáveis por versão.

Total:

- 14 condições de latência por versão;
- 1 controle saudável por iteração;
- 150 execuções por versão;
- 300 execuções no total.

## Métrica principal

Taxa de detecção:

`detected = is_reliable == true`

A taxa end-to-end é mais estrita e também exige:

- serviço Top-1 correto;
- incidente criado;
- causa do incidente correta;
- ausência de reinício;
- persistência do RCAResult.

## Limiar empírico

O limiar empírico é o menor delay que alcança uma taxa de detecção definida:

- 50%;
- 80%;
- 95%.

Esse valor é descritivo para a matriz testada; não deve ser tratado como um
limiar universal para qualquer aplicação.

## Critérios de qualidade

Investigar e, se necessário, repetir apenas o bloco afetado quando:

- validade técnica ficar abaixo de 98%;
- houver RCAResult ausente;
- ocorrer reinício do correlation-engine;
- houver divergência de proveniência;
- a injeção não produzir duração compatível com o delay.

A detecção baixa em delays pequenos não é falha de infraestrutura. É um
resultado experimental.

## Estatística

Para cada serviço e delay:

- Fisher exato bicaudal para comparar as taxas de detecção de v1 e v2;
- correção de Holm-Bonferroni;
- diferença absoluta de risco;
- odds ratio com correção de Haldane;
- Cliff's delta para a distribuição de confiança.

As execuções de v1 e v2 são independentes, pois correspondem a traces
diferentes, ainda que compartilhem a mesma condição experimental.

## Limitações

A campanha avalia apenas:

- dois serviços;
- injeção determinística (`fault_rate=1.0`);
- uma aplicação sintética;
- uma máquina/ambiente;
- falha de latência isolada.

Ela não substitui a etapa posterior de validade externa.
