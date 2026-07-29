# Resultados experimentais — modelo de redação

## Configuração experimental

Descrever:

- commit e tag;
- hardware e sistema operacional;
- versões Docker, Java e Python;
- imagens e Compose resolvido;
- versões dos modelos;
- número de repetições;
- ordem dos cenários;
- política de restauração entre falhas.

## Cenários e ground truth

| Cenário | Falha | Serviço ground truth | HTTP esperado | Incidente esperado |
|---|---|---|---:|---|
| healthy | nenhuma | — | 2xx | não |
| payment_failure | erro | payment-service | 5xx | sim |
| payment_latency | latência | payment-service | 2xx | sim |
| order_failure | erro | order-service | 5xx | sim |
| order_latency | latência | order-service | 2xx | sim |

## Métricas

Relatar:

- Top-1 accuracy fault-only;
- reliability accuracy;
- incident decision accuracy;
- incident root-cause accuracy;
- falso positivo no saudável;
- falso negativo nas falhas;
- confiança por cenário;
- duração da requisição e do cenário;
- IC95%;
- McNemar pareado;
- bootstrap pareado das diferenças contínuas.

## Comparação v1 × v2

Inserir as tabelas de:

```text
comparison/overall_metrics.csv
comparison/scenario_metrics.csv
comparison/statistical_tests.json
```

Discutir separadamente:

- falhas por erro;
- falhas por latência;
- comportamento saudável;
- efeito do reliability gate.

## Comparação com baselines

Inserir:

```text
baselines/baseline_overall_metrics.csv
baselines/baseline_scenario_metrics.csv
baselines/statistical_tests.json
```

Evitar afirmar superioridade apenas por média; considerar p-valor, tamanho da amostra, cenário e padrão de erros.

## Ameaças à validade

### Validade interna

- isolamento entre cenários;
- associação por trace ID;
- proveniência v1/v2;
- containers reiniciados;
- ordem de execução;
- aquecimento.

### Validade de construção

- qualidade do ground truth;
- limiar de confiabilidade;
- definição de incidente correto;
- representatividade das evidências.

### Validade externa

- apenas dois serviços de aplicação;
- falhas sintéticas;
- intensidade fixa de latência;
- ambiente local;
- necessidade de benchmark público.

### Validade de conclusão

- 50 repetições por cenário;
- análise pareada;
- múltiplas métricas;
- intervalos de confiança;
- ausência ou presença de variabilidade.
