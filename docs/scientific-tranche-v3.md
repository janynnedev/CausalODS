# Scientific Tranche v3

Tranche A is a controlled preliminary execution of 180 incidents (110 train,
30 validation, 40 test) selected from the frozen 735-incident
`within-dataset`/seed 42 split. It exists to stage scientific execution under
compute and time constraints. It does not change CausalODS feature, graph,
anomaly, pruning, reliability, model, calibration, metric, or statistical
semantics, and it must not be described as having the statistical power of the
full campaign.

Selection is frozen before observing H1–H4 outcomes. Inside each original
split, candidates are canonically ordered, assigned deterministic event-size
rank quartiles using manifest-only event counts, and selected by a seeded
greedy marginal-balancing allocation over system, CLEAN/PARTIAL quality,
available modalities, fault family, and event-size tier. At every step the
largest uncovered-category and proportional marginal deficit is preferred;
SHA-256 of seed, split, and incident ID is the stable tie-break. If an exact
joint stratum is exhausted, the same global marginal-deficit rule
deterministically redistributes its quota. No model output, prediction, or
hypothesis result participates.

The manifest hash binds the schema and selection versions, tranche ID,
protocol, seed, parent base/split keys, requested counts, policy, and the three
sorted ID lists. Timestamps and optional provenance are excluded. Validation
rejects tampering, duplication, overlap, split migration, count drift, parent
binding drift, and optional feature-registry/preprocessing drift.

Preprocessing first resolves the unchanged parent planner assignment. Tranche
incidents retain their original split. Tranche artifacts use an isolated
checkpoint identity and record the tranche ID/hash plus parent keys. The
authoritative baseline remains the completed 470-parent-training-incident
snapshot; a 110-incident baseline is never computed. H1, H2, and H4 may consume
tranche datasets; H3 retains its independently frozen 40-incident design unless
an explicit future alignment contract is added.

Running without `--scientific-tranche` follows the original full path and
still makes all 735 parent incidents available. The complement helper computes
the remaining 555 incidents without changing any split. No methods,
hyperparameters, thresholds, or selection policy may be adapted from Tranche A
results. Later full completion recomputes aggregate confirmatory results.
