# Feature worker memory ownership v3

Before this hardening, the parent serialized the complete official baseline in
every incident `.worker.json`. The child CLI parsed that file into a dictionary,
retained its `baseline_json` string, passed the string to the worker, and built
a `BaselineSnapshotV3`. During feature processing the same process then owned
normalized events, optional event fingerprints, the incident bundle, graph,
feature vectors, and output rows. Checkpoint binding parsed the baseline string
again. Thus the parsed spec, baseline string, baseline object, and scientific
working set overlapped.

The worker spec now owns only an absolute snapshot path, expected baseline
SHA-256, and immutable snapshot bindings. The CLI extracts these scalar/path
values and releases the parsed spec before feature processing. The worker
strictly validates the referenced completed snapshot, preprocessing version,
base/split/training binding and baseline hash. After constructing the identical
`BaselineSnapshotV3`, it releases the snapshot mapping and baseline JSON string
and runs `gc.collect()` before `CACHE_START`. Checkpoint binding reuses the
validated expected hash and does not parse baseline JSON again.

The bounded `.phases.json` sidecar records only phase names, elapsed time,
current RSS and peak RSS. `CACHE_START` is persisted before normalized cache
loading, followed by `CACHE_DONE`, `FINGERPRINTS_DONE`, `BUNDLE_DONE`,
`GRAPH_DONE`, and `FEATURES_DONE`. It contains no events or scientific payload.
The parent removes spec and sidecar only after reading a completed checkpoint;
both remain diagnostic evidence on subprocess failure or SIGKILL.
