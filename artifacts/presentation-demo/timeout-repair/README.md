# Investigação e reparo operacional — presentation-demo

scientific_result=false. Nenhum commit ou push realizado. Nenhuma chamada OpenAI realizada.

## Causa e evidências

A falha original não era ausência de trace nem perda do correlation ID na normalização. O trace `1eadee46a8f3ecae09890c84be1ba0ca` existe no Tempo e nos offsets 14155/14156 de `traces-v3`. Os spans POST /orders e POST /payments contêm `business.correlation.id=presentation-demo-bb6bfd2db10d4e998bd49eba324b3be5-run-01`. A reprodução somente em memória com TraceEventNormalizer preservou essas identidades.

Durante a campanha original, às 08:49:47Z o runtime indexava eventos de 08:20:32Z, quase 30 minutos atrasado. RuntimeSink fazia uma chamada OpenSearch com `refresh=wait_for` para cada evento. Um pacote OTLP de métricas observado possui 105 eventos; as esperas acumulam antes do próximo poll/commit Kafka. No snapshot inicial o runtime estava com consumer_running=false, CommitFailedError e o grupo sem membros ativos. Não há logs preservados do container anterior para atribuir esse mesmo CommitFailedError à campanha original; o backlog durante aquela campanha está comprovado pelos documentos históricos. Após o reparo, o consumo normal dos offsets existentes alcançou e indexou os dois spans antigos com o correlation ID intacto. Não houve reset de offsets ou republicação artificial.

O primeiro smoke após corrigir a persistência demonstrou também um defeito de associação: os logs/spans sem correlation ID do trace `acb432c0c2b31c6434060ed317cb3c35` entraram no incidente `inc-v3-852646496e77b6c2b6b92433`, iniciado com outro trace (`23c7196f1b4e8c4fbb358435334717df`). Seus spans explícitos de /orders e /payments não apareceram no índice por correlation ID. A associação temporal/topológica de workflows distintos pode contaminar o bundle antes da chegada da identidade explícita; o filtro de RuntimeSink descarta eventos não associados. A regressão reproduz essa ordem e exige que a sonda e a requisição tenham bundles distintos. Não se atribui retroativamente esse segundo defeito à campanha original sem estado em memória preservado.

Classificação: F (backlog de ingestão no timeout original) e D (associação/indexação demonstrada no smoke subsequente), sem evidência de defeito na projeção do campo correlation_id.

## Alterações desta sessão

- `correlation-engine/platform_v3/opensearch.py`: bulk idempotente por batch, refresh normal do índice, falha explícita para qualquer item rejeitado; incidentes/RCA mantêm a API anterior.
- `correlation-engine/platform_v3/runtime.py`: RuntimeSink usa bulk e confirma contadores após persistência; poll de um pacote por vez; readiness exige atribuição Kafka real; health expõe o grupo.
- `correlation-engine/multimodal_v1/evidence/association.py`: traces distintos não se associam apenas por tempo/topologia, sem identidade comum, entidade de negócio ou ligação explícita. A correlação entre múltiplos traces com a mesma identidade continua permitida.
- `correlation-engine/platform_v3/presentation_demo.py`: overlay também no modo deterministic, confirmação do grupo esperado, provider explícito por ambiente; manifest live registra openai após validar projeção llm e modelo congelado.
- `docker-compose.presentation-demo.yml`: grupo exclusivo em ambos os modos, provider parametrizado e chave opcional para smoke deterministic. O caminho live continua validando a chave antes da recriação.
- `correlation-engine/tests/test_presentation_demo.py` e `correlation-engine/tests/test_incident_evidence_assembler_v3.py`: regressões de bulk, retry sem duplicação, identidades, isolamento de workflows e readiness/provider.

Alterações preexistentes estão em `initial-snapshot.json`. Em particular, os arquivos fault-e2e-live.json e seu checksum já estavam modificados; não foram regenerados nesta sessão. Nenhum resultado/modelo/manifest/dataset científico congelado foi editado.

## Validação operacional final

Campanha deterministic final: `../5d35245631c444d8a73134475efe215a/manifest.json` (também summary.json).

- SUCCESS; runs_completed=1; runs_successful=1; runs_failed=0; featured_run=1.
- root_cause_service=payment-service; confidence=0.7679715815164457; reliable=true.
- trace_id=220c9f5603d443f355d25fd785b84dad.
- incident_id=inc-v3-ab25a3ac5064ad07a1d80318.
- rca_result_id=rca-v3-058b10a754a0085e1e4d9b26.
- explanation_id=explanation-v3-4fca8494eca31a120b31a8c1.
- 10 eventos reais de logs/traces; EventEvidenceGraph com 10 nós e 20 arestas.
- Documentos vinculados no OpenSearch; XGBoost operacional real; Tempo direto e proxy Grafana aprovados; consulta do incidente pelo backend Grafana aprovada.
- scientific_result=false; fault_restored=true; runtime_provider_restored=true.
- Exatamente 7 PNGs reais em `../5d35245631c444d8a73134475efe215a/featured/graph/`: 01-observed-events.png, 02-structural-relations.png, 03-semantic-relations.png, 04-temporal-statistical-evidence.png, 05-causal-candidates.png, 06-pruning.png, 07-rca-result.png.
- A explicação deste smoke é deterministic-fallback, fallback=true, valid=false. Isso não comprova os critérios do run GPT sem fallback.

O primeiro ensaio desta sessão expirou antes de enviar qualquer pedido por usar o destino host.docker.internal, que deixou de responder aos ports publicados. Foi corrigido usando encaminhamento local temporário para o gateway Docker 172.18.0.1. Outro smoke revelou o defeito de associação acima. Depois das correções, duas campanhas deterministic de um pedido passaram; ambas permanecem nos artefatos para auditoria. Não houve campanha operacional de 10 runs.

## Testes e limites

Os testes focados em `tests.txt` passaram: 183 passed, 2 warnings in 8.79s. A suíte adicional test_platform_v3.py passou integralmente: 36 passed, 43 warnings in 544.14s (`platform-v3-tests.txt`). Total: 219 testes aprovados. py_compile dos seis arquivos Python alterados passou. git diff --check passou. Não foi necessário modificar Java.

O Docker é Docker Desktop via socket compartilhado, com caminhos de bind mounts no host diferentes do devcontainer. A implantação usou uma imagem derivada da imagem operacional existente contendo somente os módulos reparados e compose com o project-directory original do host; nenhum volume/tópico foi apagado. Os encaminhamentos locais foram apenas auxiliares operacionais.

O run live final não foi tentado: OPENAI_API_KEY está ausente tanto no ambiente do executor quanto no runtime deterministic inicial. Não houve consulta paga nem alteração dos freezes H3. Não é possível afirmar sucesso GPT-5.4 Mini, ausência de fallback live ou auditabilidade live sem executar esse run.

No terminal do host, com a chave já configurada com segurança e este checkout atualizado, resta somente o comando autorizado:

```bash
PYTHONPATH=.:correlation-engine python -m platform_v3 presentation-demo --runs 1 --scenario payment_failure --live-llm --export-graphs --output artifacts/presentation-demo
```

`final-validation.json`, `diagnostic-evidence.json`, `before-health.json`, `after-health.json`, `before-offsets.txt`, `after-offsets.txt` e `git-status.txt` contêm os registros resumidos desta sessão. Todos são operacionais e não científicos.
