# Camada de explicação auditável do CausalODS

## Objetivo

Transformar um `RCAResult` em uma explicação textual estruturada, determinística
e rastreável, sem introduzir inferências que não estejam presentes nas
evidências persistidas.

## Por que uma versão determinística

A primeira versão não usa LLM. Isso cria um baseline de explicação que:

- é reproduzível;
- mantém vínculo explícito com `rca_result_id` e `trace_id`;
- não inventa causas ou relações;
- permite testes unitários;
- pode ser comparado posteriormente com uma versão baseada em LLM.

## Contrato de saída

Cada explicação contém:

- `explanation_id`;
- `rca_result_id`;
- `trace_id`;
- serviço classificado;
- confiança;
- decisão de confiabilidade;
- resumo;
- evidências;
- alternativas;
- limitações;
- proveniência do modelo, pipeline e gerador.

## Regras

1. O serviço principal vem do primeiro item do ranking.
2. A confiança vem de `scores.final_score`.
3. Evidências do serviço principal têm prioridade.
4. Componentes do score podem ser expostos como evidência quantitativa.
5. Resultado não confiável é descrito como hipótese, não como incidente.
6. Campos ausentes são informados como limitação.
7. O gerador não altera o RCAResult nem cria incidentes.

## Uso científico

Esta versão pode ser avaliada por:

- cobertura de explicações;
- fidelidade ao RCAResult;
- determinismo;
- completude de proveniência;
- proporção de explicações sem evidência textual;
- avaliação humana de clareza.

Uma versão LLM deve ser comparada contra este baseline e receber somente a
estrutura validada, nunca documentos arbitrários do OpenSearch.
