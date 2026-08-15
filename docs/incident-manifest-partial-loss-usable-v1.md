# IncidentManifest v2 — revisão de invariante `partial-loss-usable-v1`

Motivo da evolução contratual:

> official RCAEval revealed representational mismatch for partial valid observations

O wire format, os campos e `schema_version="incident-manifest-v2"` permanecem
inalterados. A revisão modifica somente a validação semântica: um manifesto
`permissive` pode preservar perdas parciais contabilizadas e ainda ser utilizável.

- `usable_for_benchmark=true` exige `empty_incident_reason=None`.
- `usable_for_benchmark=false` em modo `permissive` exige motivo não vazio.
- manifests vazios continuam obrigatoriamente não utilizáveis e com motivo.
- todas as invariantes de `strict`, modalidades, hashes e accounting permanecem.

Hash anterior de `contracts.py`:
`270b47cecd502112a4393a99ba46422ee9c9b29d60b1fb53d6ad16374671f741`.

Hash após a revisão:
`a2264c1ceaa30d8354a8040a571c97e4eade7e8263210cd6a18daa4ba7175a96`.
