# Camada de explicação auditável do CausalODS — v1.1

## Objetivo

Transformar um `RCAResult` em uma explicação textual estruturada, determinística
e rastreável, sem introduzir inferências que não estejam presentes nas
evidências persistidas.

## Melhorias da v1.1

- preservação explícita de evidências temporal e SHAP;
- seleção diversificada por fonte;
- score breakdown como complemento, não substituto;
- amostragem estratificada no relatório;
- métricas de cobertura por tipo de evidência;
- avaliação automática de fidelidade ao RCAResult;
- comparação opcional byte a byte entre duas gerações.

## Regra de seleção

A ordem de prioridade é:

1. temporal;
2. SHAP;
3. modelo;
4. anomalia;
5. grafo;
6. confiabilidade;
7. outras evidências;
8. componentes quantitativos do score.

O gerador mantém até oito evidências de domínio e cinco componentes do score.

## Critérios de validação

- cobertura 100%;
- zero falhas de adaptação;
- causa, confiança e confiabilidade iguais ao RCAResult;
- proveniência completa;
- IDs determinísticos;
- consistência entre confiabilidade e linguagem operacional;
- igualdade byte a byte entre duas execuções independentes.

## Limite metodológico

A versão determinística mede rastreabilidade e fidelidade. Clareza percebida,
utilidade operacional e qualidade narrativa exigem avaliação humana ou
comparação futura com uma variante baseada em LLM.
