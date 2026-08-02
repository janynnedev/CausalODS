# Cadência de controles da campanha de restart

A campanha executou 10 iterações experimentais do cenário
`correlation_engine_restart`.

Antes de cada iteração experimental, foi realizado um controle saudável
medido. Após o término das dez iterações, foi realizado um controle
saudável adicional.

Cada controle saudável foi precedido por duas execuções de aquecimento,
excluídas das métricas experimentais.

Composição observada:

- execuções experimentais: 10;
- controles saudáveis medidos: 11;
- warmups excluídos das métricas: 22;
- warmups por controle: 2.

Essa cadência foi adotada após a identificação de falsos positivos
transitórios na primeira requisição posterior à recriação dos serviços
no ambiente Ubuntu nativo.

Os controles e warmups não foram incluídos no cálculo das métricas de
robustez. Apenas as dez linhas de `results.json` representam execuções
experimentais.
