# IncidentEvidenceAssembler v3

O `IncidentEvidenceAssembler` reduz eventos `UnifiedEvent v1` a evidências por
serviço sem alterar os eventos observados e sem integrar o namespace multimodal
ao runtime trace-only v2.

## Associação

A pontuação segue esta precedência: `correlation_id` (1,00), `trace_id` (0,92),
parent span ou span link (0,85), entidade de negócio (0,75), topologia com
proximidade temporal (0,45–0,65) e somente proximidade temporal (0,10–0,35).
Um `correlation_id` explícito conflitante bloqueia associações mais fracas. Um
empate dentro de `ambiguity_margin` é ambíguo, inclusive quando forte; não há
desempate por ordem lexical. Um empate forte só poderá ser resolvido futuramente
por um discriminador auditável adicional. A topologia é somente uma entrada de
associação e não é declarada como prova causal.

Parent/span e span links usam o par `(trace_id, span_id)`. Sem `trace_id`, essa
relação não é usada, pois `span_id` pode se repetir entre traces. Entidades usam
o par `(business_entity_type, business_entity_id)`; sem tipo, a identidade de
entidade não participa da associação.

Os métodos públicos não aceitam overrides livres. Identidade externa requer
`TrustedAssociationContext`, com claims independentes para correlation e trace.
Cada `TrustedIdentityClaim` possui valor, fonte, confiança e motivo próprios. A
confiança limita apenas a identidade externa correspondente: correlation
observado mantém 1,00 com trace externo de baixa confiança, e trace observado
mantém 0,92 com correlation externo de baixa confiança. Divergência com uma
identidade observada é rejeitada e o `UnifiedEvent` nunca é reescrito.

O estado não guarda IDs como strings soltas. Cada ocorrência produz um
`IncidentIdentityEvidence` imutável com tipo, valor, origem (`observed` ou
`external`), confiança, `EventReference` e, quando externa, fonte e motivo. Os
limites são 1,00 para correlation observado, 0,92 para trace observado, até
1,00 para correlation externo e até 0,92 para trace externo.

Para cada valor coincidente, `match_score = min(confiança_atual,
confiança_candidata)`. Havendo correlation e trace coincidentes, vale o maior
dos scores de identidade; topologia ou proximidade não elevam um match de
identidade de baixa confiança. A fórmula é simétrica quanto à ordem. Uma
identidade observada posteriormente é anexada como nova evidência e pode elevar
matches futuros, mas não altera scores já gravados em `association_scores`.

Correlation IDs externos têm no máximo 128 caracteres e não podem ser vazios,
conter whitespace ou caracteres de controle. Trace IDs externos, declarados
como identidade técnica, exigem o formato OTLP hexadecimal de 32 caracteres. A
tolerância a IDs legados permanece no contrato de entrada `UnifiedEvent`, mas
não se aplica a claims técnicos externos.

Eventos com o mesmo `event_id` e fontes diferentes são preservados. Somente o
par `(event_id, source_reference)` é tratado como duplicado exato.

`workflow_key` é estável e deriva, em ordem, de correlation ID, trace ID ou da
referência do evento seed. Uma identidade externa pode formar essa chave, mas
sua origem permanece auditável em `external_identity_evidence`.

`incident_id` é determinístico para o mesmo replay ordenado e considera
workflow, ID, timestamp e fonte do seed, além de `lifecycle_generation`. A
geração é um campo separado no bundle, cresce por workflow e começa em zero. O
ID não depende de contador global entre workflows. Replays com a mesma ordem
produzem os mesmos IDs; particionamento ou ordem diferentes podem mudar a
geração e devem ter proveniência operacional própria. Ciclos distintos não
compartilham ID nem sobrescrevem bundles fechados auditáveis.

## Ciclo de vida e limites

- `advance_watermark()` fecha bundles cujo último evento ultrapassou a
  tolerância de atraso.
- Um bundle fechado só reabre se o timestamp estiver na janela completa
  `[state.first_at - allowed_lateness, state.closed_at + reopen_window]` e com
  evidência forte (`score >= reopen_threshold`). Proximidade isolada não basta.
- Limites de memória e de incidentes ativos são verificados antes da transição.
  Uma rejeição preserva `closed_at`, `reopened_count`, telemetria e mapas.
- `expire()` remove bundles fechados retidos e incidentes ativos abandonados.
- `max_active_incidents`, `max_events_per_incident` e `max_total_events` limitam
  memória. Eventos recusados pelo limite não alteram o estado.

## Evidências e telemetria

Cada serviço recebe contagens por modalidade, intervalos, taxa de erro, resumo
de métricas, estados de negócio, amostras de logs/exceções e indicadores de
qualidade. O bundle preserva eventos, traces, entidades, modalidades ausentes,
eventos atrasados, razões, scores e proveniência.

`bundle.traces` contém apenas traces observados. Claims externos ficam na
coleção separada `external_identity_evidence`, cujo contrato imutável contém
`EventReference`, tipo, valor, fonte, confiança e motivo. O seed mantém a razão
`incident_seed` e também preserva todas as evidências externas.

`EventReference` não possui ordenação implícita. Ordenações explícitas usam
sempre `(event_id, source_reference or "")`, sem comparar `None` com strings.

`AssemblerTelemetry.snapshot()` expõe entradas, associados, não associados,
duplicados, bundles fechados/reabertos/expirados, rejeições por memória, tamanho
do estado e latência de associação (total, máxima, amostras e média).

## Concorrência

O assembler é deliberadamente **single-threaded**. Seus mapas internos não têm
sincronização e cada instância deve ficar confinada a um loop/worker. Somente o
coletor independente `AssemblerTelemetry` possui lock.
