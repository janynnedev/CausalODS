# Limitações do congelamento parcial H4

Classificação: **H4_PARTIAL_FROZEN_EVIDENCE**. Este diretório nunca deve ser usado como store de resume.

1. Foram concluídas 967 das 1300 execuções aplicáveis.
2. A cobertura aplicável é 74,38% (967/1300).
3. Foram observados 844 dos 940 skips estruturais; skips não são inferências científicas.
4. Não há FAILED no conjunto pré-OOM selecionado.
5. A campanha foi interrompida por OOM, conforme o histórico operacional informado. Este freeze não constitui comprovação independente do evento do kernel.
6. A tentativa posterior de resume foi rejeitada para fins científicos.
7. Os 145 outputs duplicados dessa tentativa foram excluídos das cópias científicas e não representam progresso novo.
8. A ausência de índice histórico independente de checksums impede a classificação como campanha final canônica.
9. A provenance de modelo nos payloads históricos é incompleta segundo o validator posterior; nenhum campo foi acrescentado retroativamente.
10. Os hashes deste freeze estabelecem integridade a partir deste congelamento e permitem detectar alterações futuras. Não comprovam identidade de bytes anterior ao resume. Permissões somente leitura não equivalem a armazenamento WORM.
11. H4 não pode receber veredito confirmatório final: não se declara H4 confirmada, rejeitada ou completamente executada.
12. Os resultados podem sustentar somente análise descritiva, exploratória ou de tendência, ou trabalho futuro com protocolo explícito. Não autorizam retomada automática deste store.

A cobertura estrutural é 80,85% (1811/2240). Permanecem ausentes 333 execuções aplicáveis e 96 skips, totalizando 429 posições. A interrupção pode induzir seleção não aleatória de incidentes, operadores e severidades; análises devem apresentar essa limitação e a cobertura por estrato.

A identidade científica disponível foi comparada ao plano original separadamente da completude da provenance histórica. O índice registra FULL como informação do plano, validada pelo feature_view_hash persistido. O contrato tem uma repetição, armazenada com índice 0.

Nenhuma execução de campanha, inferência, modelo ou degradation harness foi realizada para produzir este freeze. Arquivos do framework previamente modificados no worktree não foram alterados por esta operação.
