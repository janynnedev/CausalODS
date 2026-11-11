# Segunda falha live: diagnóstico e preparação

scientific_result=false. Nenhum commit/push. Nenhuma nova chamada paga. Artefatos científicos e modelos congelados não alterados. A investigação do timeout antigo não foi repetida.

## O que está comprovado

Campanha original: 8be28a2be3e7484c8cd1dbca87e27c4e.

Último PASS: stage 11, real_rca_result_created, às 10:19:17.639938Z.
Primeiro FAIL: stage 12, evidence_and_explanation_created, às 10:21:18.410829Z.
O reason completo armazenado é apenas `TimeoutError`. O RuntimeError no demo_run é o erro genérico lançado pela apresentação ao receber o relatório FAILED.

Consultas diretas ao OpenSearch confirmaram:

- 10 UnifiedEvents para trace 3d746b35cb23ae683d55b2fa8d9b0f0c.
- Incidente inc-v3-067588b72a415a6c31d21ac1, event_count=10, correlation_ids contendo somente o ID da campanha.
- RCA rca-v3-f701dd89e2927ed3a9b5db52 com root_cause_service=payment-service, confidence=0.7679715815164457. As projeções reais foram copiadas para original-incidents.json e original-rca-results.json.
- Zero explicações vinculadas a esse incidente.

O runner leu corretamente root_cause_service, confidence, hash do modelo e registry antes de aprovar o stage 11. Portanto não se trata de campo RCA renomeado ou leitura parcial do RCA.

## Limite da conclusão sobre a causa específica

TimeoutError é o timeout da espera do runner pela explicação, não prova de timeout do provedor. O runtime anterior registrava somente o tipo de exceção em stdout e seu container live foi removido pelo cleanup. Os logs disponíveis pertencem ao runtime deterministic restaurado, iniciado em 10:21:42Z. Não existe registro durável para recuperar o erro específico da chamada/resposta/parsing daquela execução. Não é correto afirmar que foi incompatibilidade do modelo, truncamento, erro HTTP ou qualquer outra hipótese.

O fluxo do runner exige readiness com provider=openai antes do pedido; o incidente tinha somente correlation IDs presentation-demo, portanto o código selecionaria o cliente live. Isso é inferência sustentada pelo fluxo, não um log retido da invocação. Não há prova durável da chamada específica ao ExplanationService anterior.

Uma reprodução somente em memória, com os eventos reais reconstruídos e o cliente frozen, passou pela montagem do contexto e chegou à fronteira de rede, bloqueada deliberadamente por mock. Não fez chamada paga. Uma conexão DNS/TLS sem requisição HTTP ao endpoint também funcionou. Esses resultados não reconstituem a resposta GPT perdida.

## Correções comprováveis desta sessão

1. Runtime persiste explanation_status=RUNNING/FAILED/COMPLETED no incidente, provider, timestamps e código de erro em lista segura. A proteção abrange geração, parsing, projeção e gravação da explicação. Não persiste resposta remota, segredo ou explicação fictícia.
2. O runner detecta FAILED explicitamente e reporta seu código sem esperar 120 segundos por um documento que não será produzido.
3. A apresentação preserva no summary os valores RCA/eventos já confirmados mesmo quando a explicação falha. Não inventa explanation_id nem fallback.
4. Mapping aditivo para os cinco campos operacionais novos no índice de incidentes.

Arquivos alterados nesta sessão:

- correlation-engine/platform_v3/runtime.py
- correlation-engine/platform_v3/e2e_live.py
- correlation-engine/platform_v3/presentation_demo.py
- correlation-engine/platform_v3/explanation_status.py (novo)
- correlation-engine/tests/test_presentation_demo.py
- infrastructure/platform-v3/opensearch/templates/incidents.json

Não foi feita alteração especulativa no transporte OpenAI, no prompt científico, no orçamento, no contrato frozen ou nos modelos. O defeito de diagnóstico e a perda de progresso no summary foram corrigidos; a causa específica da falha GPT original permanece indeterminada.

## Testes e smoke

- test_presentation_demo.py + test_explanations_v2.py + test_multimodal_ingestion_v1.py: 200 passed, 2 warnings (tests.txt).
- test_platform_v3.py -k 'templates or projection or repository': 4 passed, 32 deselected.
- py_compile dos cinco arquivos Python alterados: PASS.
- git diff --check: PASS.
- Regressões: erro explícito sem timeout; ausência de segredo no código de erro; preservação do RCA parcial; parsing/contexto/projeção com resposta offline aderente ao contrato; preservação do RCA sem fabricar explicação quando o provider falha.

Campanha deterministic nova: b97a4a8a068d4d4895e988abcf268f80.

- campaign_status=SUCCESS, 1/1 aprovado.
- root_cause_service=payment-service, confidence=0.7679715815164457.
- trace_id=d47adb3e62e0802bdfad4f636593c557.
- incident_id=inc-v3-6074c0f63c56b41535f3d9d5.
- rca_result_id=rca-v3-d94dca099f44c12514be5a71.
- explanation_id=explanation-v3-39cc4c02433a270b43ce806f.
- Explicação real deterministic persistida; explanation_status=COMPLETED; explanation_provider=deterministic (smoke-incident.json).
- fault_restored=true; runtime_provider_restored=true; scientific_result=false.
- OpenSearch, Tempo direto/proxy e consulta Grafana aprovados pelos stages operacionais.
- Provider do smoke: deterministic-fallback; fallback=true. Não é validação live sem fallback.

Summary: artifacts/presentation-demo/b97a4a8a068d4d4895e988abcf268f80/summary.json.
Manifest: artifacts/presentation-demo/b97a4a8a068d4d4895e988abcf268f80/manifest.json.

Exatamente sete PNGs no diretório artifacts/presentation-demo/b97a4a8a068d4d4895e988abcf268f80/featured/graph/:

1. 01-observed-events.png
2. 02-structural-relations.png
3. 03-semantic-relations.png
4. 04-temporal-statistical-evidence.png
5. 05-causal-candidates.png
6. 06-pruning.png
7. 07-rca-result.png

## Próximo live

OPENAI_API_KEY está ausente no processo executor. Foi verificada somente presença no container antigo, sem ler/extrair/transferir o valor e sem usá-lo para contornar essa ausência. Nenhum novo live foi tentado.

O código e o smoke estão preparados. Ainda é necessário um live no WSL host para comprovar sucesso GPT-5.4 Mini sem fallback ou obter o erro específico com os novos registros. Não afirmamos que falta apenas comprovar uma correção já conhecida do provedor.

Com a chave configurada no terminal WSL host, executar uma única vez:

```bash
PYTHONPATH=.:correlation-engine python -m platform_v3 presentation-demo --runs 1 --scenario payment_failure --live-llm --export-graphs --output artifacts/presentation-demo
```

Git status final completo: git-status.txt. Modificações e arquivos não rastreados de etapas anteriores foram preservados. Os encaminhamentos locais temporários foram usados apenas para acessar os serviços do Docker Desktop a partir do devcontainer e encerrados ao final.
