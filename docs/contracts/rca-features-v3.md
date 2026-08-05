# RCA Features v3

`rca-features-v3` é um contrato multimodal novo e isolado. Ele não substitui
`rca-features-v2`, não é importado pela pipeline trace-only e não altera o modelo
`xgboost-rca-v2`.

## Unidade, ordem e valores ausentes

Cada vetor representa exatamente `(incident_id, service_name)`. A ordem canônica
é a ordem de `FeatureRegistryV3`; mudar nome, tipo, semântica ou posição exige uma
nova versão. `FeatureRegistryV3.manifest()` é o manifesto executável e exportável,
com nome, grupo, tipo, unidade, nulabilidade e semântica das 40 features.

Ausência observacional é `null` no contrato e no JSON determinístico. A conversão
para matriz usa `NaN`, de forma explícita, em `RCAFeatureVectorV3.model_row()`.
As quatro modalidades possuem indicadores próprios de missingness. Valores zero
significam uma observação real de zero; nunca substituem uma observação ausente.

`UnifiedEvent.contract_fingerprint` é o SHA-256 de `UnifiedEvent.to_json()`. Essa
representação canônica inclui todo o envelope validado: referências e IDs,
timestamps UTC, serviço/instância, modalidade, tracing e links, estado/erro,
métrica, negócio, atributos e resource attributes, qualidade, atraso e origem.
Mappings são ordenados pelo JSON canônico, sequências são estáveis, `None` é
`null` e números não finitos já são rejeitados pelo contrato.

## Grupos

- `trace`: latência, erro e distância/falhas descendentes do grafo.
- `log`: severidade, exceções, tipos de erro e novidade de template.
- `metric`: CPU, memória, deltas de taxa, saturação e anomalias.
- `business`: falhas, rejeições, transições e mudança de outcome.
- `graph`: posição estrutural, alcance anômalo, cadeias e pruning.
- `quality`: modalidades ausentes, perdas, atraso e cobertura da topologia.

O registry canônico é fechado: `FeatureRegistryV3` rejeita qualquer alteração de
ordem, tipo, nulabilidade ou definição sob o nome `rca-features-v3`. O manifesto
completo pode ser serializado deterministicamente com `to_json()` e possui
fingerprint SHA-256.

A ablação cria `FeatureViewV3`, com `ablation_id`, `feature_names` e fingerprint
próprios. `RCAFeatureVectorViewV3` contém somente os valores visíveis; uma feature
excluída não permanece acessível na interface destinada ao modelo. A ordem
relativa canônica é preservada. Ablações encadeadas são cumulativas e partem da
view atual; colunas removidas nunca são reintroduzidas.

## Baselines e prevenção de leakage

`TrainingBaselineEstimatorV3.fit()` exige a lista explícita de `incident_id` do
split de treino, ignora heldout e falha se algum incidente declarado como treino
não foi fornecido ou se um bundle estiver duplicado. Somente eventos normais de
treino alimentam média, desvio padrão e templates conhecidos.

Métricas são segregadas por `(service_name, metric_kind, metric_name, metric_unit,
metric_aggregation)`: ratio, percent, bytes, megabytes, count e rate nunca são
combinados implicitamente. Havendo várias séries do mesmo kind, vence a de maior
quantidade de amostras normais de treino; o desempate é lexicográfico pela
identidade completa. O snapshot registra identidade escolhida, unidade,
agregação, contagem e a política `max-normal-training-samples-then-lexicographic-v1`.

Quando `stddev == 0`, igualdade usa `math.isclose(rel_tol=1e-12,
abs_tol=1e-12)`. Valor numericamente equivalente à média produz `0.0`; diferença
material produz `+12.0` ou `-12.0`. A política é
`zscore-v3-zero-variance-isclose-1e-12-saturated-12`.

O dataset é dividido por `incident_id` antes do ajuste do baseline. Labels usam o
contrato `RCAServiceTargetV3`: `labeled`, `unknown` ou `excluded`, origem,
confiança e rank opcional. Em modo supervisionado, toda linha recebe um target
`labeled`; serviços negativos usam `is_root_cause=false`, nunca `None`. Targets
extras, ausentes ou duplicados falham. Vários targets `true` no mesmo incidente
representam causas múltiplas explicitamente. Labels são anexados somente após a
extração; o extrator não possui parâmetro de ground truth.

`root_cause_rank` existe somente para target positivo e `labeled`. Negativos,
`unknown` e `excluded` não possuem rank. Ranks iguais entre múltiplas raízes são
permitidos e representam empate declarado pela fonte do label.

`DatasetManifestV3` registra registry/baseline e seus hashes, fingerprint,
ablação, seed, algoritmo e frações de split, IDs e hash por split, contagens,
distribuição de labels, versões de regras do grafo e hash das linhas. Timestamp
não participa do contrato reproduzível. Dataset, linhas e splits possuem SHA-256
determinístico.

O manifesto inclui `feature_view_json`, seu SHA-256, nomes ordenados, número de
colunas e `base_schema_version`. Em datasets ablados, esses campos descrevem
exatamente a view utilizada, não o registry canônico completo.

A proveniência preserva versões por identidade: relação→versões das regras,
GraphFeaturePolicy, pruning por incidente, anomalia, schema do baseline, seleção
métrica, variância zero, transições de negócio e dependência topológica. Também
registra fingerprints de grafo e topologia por incidente. Alterar uma associação,
política, topologia ou pruning muda o hash mesmo se as features forem iguais.

As versões não são strings informativas fornecidas separadamente. O generator
recebe o `RCAFeatureExtractorV3` efetivamente executado e deriva configuração,
versão e fingerprint de seus objetos `GraphFeaturePolicy` e `AnomalyPolicy`.
O baseline deriva suas versões dos `BaselineStatistic` e `MetricSelectionV3`
reais e rejeita mistura incompatível. Por incidente, o manifesto preserva
`relation_rule_versions_by_incident` e a provenance estrutural do builder:
`BusinessTransitionPolicy`, `TopologyDependencyPolicy`, `AnomalyPolicy` e
`EdgeRuleRegistry`, com configurações e fingerprints.

Rule version e policy version são contratos diferentes. A rule version identifica
a implementação registrada para uma relação; a policy version identifica a
configuração usada por essa regra. Para `TOPOLOGY_DEPENDENCY`, por exemplo, o
manifesto preserva separadamente a versão da regra e `topology_version`, pesos,
healthy prior e fingerprint da `TopologyDependencyPolicy` por incidente. O mesmo
vale para `BusinessTransitionPolicy`.

## Semânticas operacionais

Latências e `time_since_last_success` usam milissegundos. P95 usa interpolação
linear determinística. Z-scores usam média/desvio populacional do treino e a
política tolerante/saturada descrita acima. Perdas exigem
`expected_span_count`/`expected_log_count`; sem expectativa são `null`.
`topology_staleness_score` é a fração de vizinhos conhecidos sem eventos no
bundle. Pruning possui os modos `production`, `disabled_ablation` e
`evaluation_audit`. Somente os dois primeiros entram no extrator;
`evaluation_audit` e qualquer resultado com ground truth protegido são rejeitados.
Em `disabled_ablation`, `pruning_survival=null`, pois não houve decisão de poda.
Cada `PruningResult` carrega `incident_id`, `workflow_key`, fingerprint do grafo,
versão da política e referências dos sintomas. O extrator rejeita pruning de
outro incidente, workflow ou grafo.

`PruningResult.to_json()` cobre candidatos, counts, exclusões/regras, enabled,
incidente, workflow, fingerprint do grafo, política, sintomas, proteção e mode.
`pruning_fingerprint` é o SHA-256 dessa representação e é usado diretamente pelo
dataset. Candidatos e sintomas são únicos/ordenados, counts são coerentes e todo
sintoma deve pertencer ao grafo inclusive em `disabled_ablation`.

Em `production`, `before_count = after_count + número de serviços excluídos
únicos`. Em `disabled_ablation`, os counts são iguais, exclusions é vazio e os
candidatos são exatamente a entrada deduplicada/ordenada. `enabled` é booleano e
ground truth protegido também é único e ordenado.

Cada nó serializado do `EventEvidenceGraph` contém o fingerprint integral do
evento. O fingerprint do grafo cobre contexto, payloads dos eventos, nós,
arestas, classificações/pesos, rule versions e políticas estruturais. Antes da
extração, o mapa completo `EventReference -> event_fingerprint` do bundle deve
ser idêntico ao mapa do grafo; compartilhar somente referências não é suficiente.

Toda configuração aninhada que participa dos hashes usa `deep_freeze_json`:
mappings viram `MappingProxyType` ordenados, sequências e sets viram tuples
determinísticas, datetimes viram UTC e NaN/infinito ou chaves não textuais são
rejeitados. Cópias defensivas impedem que a mutação posterior do dicionário/lista
de origem altere o grafo, dataset ou seus hashes.

`EventGraphBuilder` injeta a `AnomalyPolicy` no `RuleContext`. `co_anomaly_rule` e
os suportes de topologia chamam exatamente
`context.anomaly_policy.is_anomalous(event)`; a mesma configuração, versão e
fingerprint são preservados no grafo.

## Visões das features de grafo

- `distance_to_first_failure`, `affected_descendant_count`,
  `upstream_anomaly_count`, `downstream_anomaly_count` e `event_chain_count` usam
  `GraphFeaturePolicy.plausible_causal_adjacency`.
- `failed_descendant_count` usa a mesma adjacency plausível, filtrando o alcance
  para spans anômalos.
- `service_depth` usa `structural_adjacency`.
- graus dirigidos usam todas as arestas dirigidas observadas.
- `centrality` é a visão all-evidence; centralidades estrutural e plausível são
  calculadas separadamente no extrator do grafo.

Nenhuma feature de alcance reconstrói classificações hardcoded. Uma policy com
`plausible_causal_adjacency=()` zera alcance downstream, spans descendentes falhos
e contagem de cadeias, e sua configuração exata aparece na proveniência.

Eventos de negócio formam pares apenas dentro de
`(business_entity_type, business_entity_id)`, ordenados por timestamp e
`EventReference`. Eventos sem identidade tipada permanecem evidências individuais
e não formam transições implícitas. `rejected_transition_count` conta somente
pares presentes em `BUSINESS_TRANSITION` cujo destino é `rejected` ou `declined`.
Uma sequência inesperada rejeitada incrementa apenas
`unexpected_transition_count`. `failed_descendant_count` conta somente spans
descendentes falhos; `downstream_anomaly_count` conta anomalias descendentes de
qualquer modalidade.

Templates de log são normalizados em minúsculas e números/identificadores são
substituídos por `<value>`. A novidade é sempre calculada contra templates normais
do treino. O grafo fornece evidência estrutural/semântica já derivada; proximidade
temporal isolada não é tratada como causalidade.
