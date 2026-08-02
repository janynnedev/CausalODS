# Migração dos pacotes importados

Data UTC: 2026-08-02T05:31:45Z

Commit antes da remoção das duplicatas:

```text
db4f7ac6eceb153ecbc8bd5d79f9d06997c9fb6b
```

Branch:

```text
feat/rca-contract-v2
```

Pacotes preservados:

- `causalods_explanations_v1`
- `causalods_explanations_v1_1`
- `causalods_latency_sensitivity`
- `causalods_post_campaign_v2`
- `causalods_robustness_live_v1`

Destino:

- `artifacts/imported-packages/`

Critério de preservação:

- cópia integral do diretório extraído;
- preservação do ZIP original;
- preservação do checksum original;
- manifesto SHA-256 por pacote;
- manifesto SHA-256 global validado.
