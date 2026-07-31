# CausalODS Robustness Live v1

Implementa a fase live da campanha de robustez sobre a fundação já mesclada.

## Arquivos instalados

- `experiments/robustness_live/`: probes e drivers;
- `experiments/run_robustness_live.py`: campanha sequencial;
- `experiments/analyze_robustness_live.py`: agregação e relatório;
- `experiments/run_robustness_live.sh`: execução em segundo plano;
- `experiments/tests/test_robustness_live_*.py`: testes sem Docker;
- `docs/robustness-live-*.md`: metodologia e execução.

## Instalação

```bash
./install.sh /workspaces/CausalODS
```

Depois execute os testes descritos em `docs/robustness-live-execution.md`.
