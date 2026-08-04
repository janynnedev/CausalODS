# EventEvidenceGraph v3

O `EventEvidenceGraph` deriva exclusivamente de um `IncidentEvidenceBundle` e
não substitui nem importa o `ServiceGraph` trace-only v2. O contexto preserva
incident/workflow, geração do ciclo, scores e razões de associação, identidades
observadas e externas, proveniência e eventos atrasados.

## Identidade composta

Nós, endpoints e evidências usam sempre `EventReference(event_id,
source_reference)`. IDs iguais de fontes diferentes coexistem. Lookup por
referência é inequívoco; lookup apenas por `event_id` é rejeitado quando houver
mais de uma fonte. Toda aresta é validada contra os nós existentes e a
serialização inclui a fonte em endpoints e evidências.

## Evidência observada, derivada e candidata

| Relação | Classificação | Direção |
|---|---|---|
| `SPAN_PARENT` | structural | directed |
| `SPAN_LINK` | structural | directed |
| `SERVICE_CALL` | structural | directed |
| `TEMPORAL_PRECEDENCE` | temporal | directed |
| `SAME_CORRELATION` | semantic | directed |
| `SAME_BUSINESS_ENTITY` | semantic | directed |
| `BUSINESS_TRANSITION` | semantic | directed |
| `CO_ANOMALY` | statistical | undirected |
| `TOPOLOGY_DEPENDENCY` | causal_candidate | directed |

O contrato rejeita combinações fora dessa matriz. Precedência e coanomalia não
são causais. `SAME_CORRELATION` usa `identity_evidence` do bundle e calcula
`min(confiança_source, confiança_target)`, preservando origem e fonte no texto
da evidência. Claims externos nunca recebem confiança 1,0 automaticamente.

`BusinessTransitionPolicy` versionada só aceita transições declaradas. Eventos
consecutivos arbitrários da mesma entidade não viram `BUSINESS_TRANSITION`.

`TOPOLOGY_DEPENDENCY` registra versão, direção, eventos e suporte. A política
padrão exige destino anômalo, identidade semântica comum ou relação estrutural.
Eventos saudáveis meramente próximos não geram candidato causal. Um prior para
esse caso só pode ser habilitado explicitamente em `TopologyDependencyPolicy`.

A seleção topológica calcula os suportes de **todos** os destinos na janela,
remove os não sustentados e só então escolhe o primeiro suportado por timestamp
e `EventReference`. Um destino saudável anterior não oculta uma anomalia
posterior. Cada suporte registra tipo, confiança, referências e explicação. Para
correlation, a confiança é `min(source, target)` com origem observed/external.
A combinação final é:

```text
topology_confidence = min(configured_topology_weight,
                          strongest_support_confidence)
```

`target_anomalous` e `structural_relation` valem 1,00; identidade semântica
preserva sua confiança; entidade tipada vale 0,90; prior saudável usa a
confiança explicitamente configurada. Evidência fraca nunca é elevada apenas
pela topologia.

## Política de anomalia

`multimodal-anomaly-v1`, em módulo neutro, é compartilhada pelo agregador do
assembler, pelas regras do grafo e pelo extractor. Reconhece `error_type`,
ERROR/FATAL/CRITICAL, status numérico >= 400, ERROR/FAILED/FAILURE textual,
outcomes failed/failure/rejected/error, `attributes.is_anomalous` e a flag
`anomalous`. Assim coanomalia, primeira falha e taxas de erro usam a mesma
classificação, sem imports circulares.

## Observações estruturais repetidas

Índices de span mapeiam `(trace_id, span_id)` para uma coleção ordenada. Parent,
Span Link e Service Call preferem a mesma `source_reference`, depois o mesmo
`source_dataset`; sem correspondência, preservam todas as relações válidas. A
explicação registra política e multiplicidade. Nenhuma observação é sobrescrita.

Arestas com a mesma chave e conteúdo são deduplicadas, unindo referências de
evidência. Conteúdo conflitante para a mesma chave gera erro.

## Caminhos e pruning

`GraphPathExtractor` opera com `EventReference`, valida endpoints e profundidade
e diferencia `classifications=None` (todas) de `classifications=()` (nenhuma).
Vizinhos são ordenados e ciclos não repetem nós no mesmo caminho.

`PruningPolicy` padrão permite structural, semantic e causal_candidate.
Temporal nunca sustenta pruning. Statistical é apenas suporte e não mantém um
candidato isoladamente. O resultado registra contagens e regra de exclusão.

Ground truth não existe na API de produção `prune()`. A proteção de rótulo está
restrita a `audit_pruning_with_ground_truth(..., evaluation_mode=True)` e não
altera features nem resultados posteriores de produção.

## Política de features

`GraphFeaturePolicy` define três visões:

- `structural_adjacency`: somente structural; usada para `depth`;
- `plausible_causal_adjacency`: structural, semantic e causal_candidate; usada
  em descendants, upstream/downstream, primeira falha e cadeias;
- `all_evidence_adjacency`: todas; usada apenas onde a natureza da evidência é
  explicitamente contabilizada, como graus.

Graus são separados em `directed_in_degree`, `directed_out_degree` e
`undirected_degree`. Arestas undirected contribuem simetricamente e não criam
upstream/downstream. `causal_candidate_edge_weight` usa somente candidatos;
`business_transition_distance` usa evidência semantic. Arestas temporal ou
coanomalia isoladas não alteram alcance destinado a RCA.

Centralidade não muda silenciosamente de significado: `centrality` continua a
visão de todas as evidências e é igual a `all_evidence_centrality`. As visões
`structural_centrality` e `plausible_causal_centrality` são expostas à parte.

## Limitações e ablações

O grafo contém evidência e hipóteses, não causalidade comprovada. Qualidade de
topologia, instrumentação e identidade afeta as relações. Pruning pode ser
desabilitado para ablação; políticas de topologia, transição, pruning e features
são versionadas e substituíveis sem tocar no runtime v2.
