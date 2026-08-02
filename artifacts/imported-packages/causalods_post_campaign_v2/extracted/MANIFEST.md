# Manifesto — CausalODS análise pós-campanha v2

Inclui:

- `experiments/analysis_utils.py`
- `experiments/compare_e2e_results.py`
- `experiments/export_rca_evidence.py` (versão autônoma)
- `experiments/run_baselines.py`
- `experiments/post_campaign_analysis.py`
- `experiments/build_experimental_report.py`
- `experiments/baselines/`
- `experiments/tests/`
- documentação em `docs/`

Validações realizadas:

- compilação de todos os módulos Python;
- 7 testes automatizados aprovados;
- comparação sobre 250 resultados v1 + 250 resultados v2;
- saída esperada: v1 150/250 e v2 250/250.
