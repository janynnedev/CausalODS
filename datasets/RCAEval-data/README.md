---
license: mit
language:
  - en
size_categories:
  - n<1K
configs:
  - config_name: cases
    data_files: cases.parquet
tags:
  - arxiv:2412.17015
  - root-cause-analysis
  - microservices
  - observability
  - aiops
  - telemetry
  - anomaly-detection
---

# RCAEval: A Benchmark for Root Cause Analysis of Microservice Systems

This repository hosts the datasets of **RCAEval**, an open-source benchmark for root
cause analysis (RCA) in microservice systems. It contains **735 failure cases**
collected from three microservice systems, organized into three benchmark suites.

- 📄 Paper: [RCAEval: A Benchmark for Root Cause Analysis of Microservice Systems with Telemetry Data](https://huggingface.co/papers/2412.17015) (WWW 2025)
- 💻 Code: https://github.com/phamquiluan/RCAEval
- 📦 PyPI: https://pypi.org/project/RCAEval

## Datasets

| Dataset | System | Cases | Fault Types | Metrics | Logs | Traces |
|---------|--------|-------|-------------|---------|------|--------|
| RE1-OB | Online Boutique | 125 | cpu, mem, disk, delay, loss | 49-59 | N/A | N/A |
| RE1-SS | Sock Shop | 125 | cpu, mem, disk, delay, loss | 57-63 | N/A | N/A |
| RE1-TT | Train Ticket | 125 | cpu, mem, disk, delay, loss | 198-238 | N/A | N/A |
| RE2-OB | Online Boutique | 90 | cpu, mem, disk, delay, loss, socket | 69-77 | Yes | Yes |
| RE2-SS | Sock Shop | 90 | cpu, mem, disk, delay, loss, socket | 74-82 | Yes | N/A |
| RE2-TT | Train Ticket | 90 | cpu, mem, disk, delay, loss, socket | 340-376 | Yes | Yes |
| RE3-OB | Online Boutique | 30 | f1, f2, f3, f4, f5 | 68-101 | Yes | Yes |
| RE3-SS | Sock Shop | 30 | f1, f2, f3, f4, f5 | 80-107 | Yes | N/A |
| RE3-TT | Train Ticket | 30 | f1, f2, f3, f4, f5 | 294-322 | Yes | Yes |

**RE1 (375 cases)** — Metric-only data supporting metric-based RCA methods. Five fault
types across five services per system, five repetitions per fault-service pair.

**RE2 (270 cases)** — Multi-source data (metrics, logs, traces) supporting multi-source
RCA methods. Six fault types across five services per system, three repetitions per pair.

**RE3 (90 cases)** — Multi-source data focusing on code-level faults (F1-F5), supporting
diagnosis through stack traces in logs or response codes in traces. Unlike RE1 and RE2,
RE3 is not a uniform grid: F5 appears only in RE3-OB, RE3-TT covers 2 root-cause services
while RE3-OB covers 4, and repetition counts vary between 3 and 6. Each RE3 dataset still
totals 30 cases. Query `cases.parquet` for the exact breakdown.

## Case index

`cases.parquet` (735 rows) is a browsable index of every case, useful for filtering a
subset without downloading any telemetry:

| column | meaning |
|---|---|
| `case` | directory name |
| `dataset`, `suite`, `system`, `system_name` | which benchmark and system |
| `root_cause_service` | **ground-truth root cause** |
| `fault`, `fault_description`, `repetition` | injected fault and repeat index |
| `inject_time` | unix timestamp of injection |
| `n_metrics`, `n_timesteps`, `time_start`, `time_end`, `duration_minutes` | metric coverage |
| `normal_timesteps`, `faulty_timesteps` | rows before / after injection |
| `has_logs`, `n_logs`, `has_traces`, `n_traces` | log and trace availability and size |
| `has_root_cause_file` | whether `root_cause.txt` is present |

```python
import pandas as pd

idx = pd.read_parquet("hf://datasets/phamquiluan/RCAEval/cases.parquet")
cpu_tt = idx[(idx.dataset == "RE2-TT") & (idx.fault == "cpu")]
print(cpu_tt[["case", "root_cause_service", "n_metrics", "n_traces"]])
```

## Layout

Each failure case is one directory named `{suite}{system}_{service}_{fault}_{repetition}`,
e.g. `re2tt_ts-order-service_cpu_1`. The directory name encodes the ground truth: the
**root cause service** is the `{service}` component and the injected fault is `{fault}`.

```
re2tt_ts-order-service_cpu_1/
├── metrics.parquet   # time (int64) + one float64 column per metric
├── logs.parquet      # timestamp (int64), container_name, message
├── traces.parquet    # time, traceID, spanID, serviceName, ... duration, statusCode, parentSpanID
└── inject_time.txt   # unix timestamp of fault injection
```

- `{suite}` ∈ `re1`, `re2`, `re3` — `{system}` ∈ `ob` (Online Boutique), `ss` (Sock Shop), `tt` (Train Ticket)
- `metrics.parquet` has a `time` column (unix seconds) followed by one column per metric, named `{service}_{metric}`.
- `inject_time.txt` splits each case into a normal period (before) and a faulty period (after).

### Format note

These files are **Parquet (zstd)** conversions of the original `metrics.json` / `logs.csv` /
`traces.csv`, which reduces the benchmark from ~38 GB to a few GB with no loss of data.
The unmodified originals remain available on
[Zenodo](https://zenodo.org/records/14590730) and
[figshare](https://figshare.com/articles/dataset/RCAEval_A_Benchmark_for_Root_Cause_Analysis_of_Microservice_Systems/31048672).

The conversion is value-for-value lossless, verified by re-parsing the originals and
comparing every metric value at every timestamp and every log/trace cell:

- Metric timestamps are unioned, never resampled, filled, or interpolated. Where a metric
  was not observed at a timestamp the value is null; no forward-fill is applied at rest.
- Trace and span IDs are stored as strings, so all-digit IDs keep their leading zeros.
- Columns that are empty in some systems (e.g. `methodName` in Train Ticket) keep their
  string type, so every case shares one schema.

The dataset viewer shows `cases.parquet`, the case index. The per-case telemetry is not
shown there because each case is a separate wide table whose column set differs per system
(49-59 metric columns for Online Boutique vs 198-238 for Train Ticket), so the cases do not
share a single tabular schema.

## Usage

Download a single suite:

```python
from huggingface_hub import snapshot_download

snapshot_download(
    repo_id="phamquiluan/RCAEval",
    repo_type="dataset",
    allow_patterns="re1*",      # or "re2*", "re3*", "re2tt_*", ...
    local_dir="data",
)
```

Load one case:

```python
import pandas as pd

case = "data/re2tt_ts-order-service_cpu_1"

df = pd.read_parquet(f"{case}/metrics.parquet")
inject_time = int(open(f"{case}/inject_time.txt").read().strip())

# nulls mark timestamps where a metric was not observed; fill as your method requires
df = df.set_index("time").ffill().bfill().fillna(0.0)

normal, faulty = df.loc[df.index < inject_time], df.loc[df.index >= inject_time]

logs = pd.read_parquet(f"{case}/logs.parquet")      # RE2 / RE3 only
traces = pd.read_parquet(f"{case}/traces.parquet")  # Online Boutique + Train Ticket
```

Run an RCA baseline with the RCAEval library:

```bash
pip install RCAEval[default]
```

```python
from RCAEval.e2e import baro

root_causes = baro(df, inject_time)["ranks"]
```

See the [GitHub repository](https://github.com/phamquiluan/RCAEval) for the 15
reproducible baselines and the evaluation framework.

## Known data notes

- `re2tt_ts-auth-service_cpu_1` has no log data (89 of 90 RE2-TT cases include logs).
- In the source data, 3 RE1 cases (`re1ob_adservice_cpu_1`, `re1tt_ts-order-service_cpu_1`,
  `re1tt_ts-travel-service_cpu_5`) and 13 RE3-SS cases carried a header-only `logs.csv`
  or `traces.csv` with zero data rows. RE1 is metric-only and Sock Shop is not traced,
  so these files held no signal and are omitted here rather than shipped as empty
  Parquet files.
- 8 RE3-SS cases include an extra `root_cause.txt` holding the root-cause log line.

## Citation

```bibtex
@inproceedings{pham2025rcaeval,
  title={RCAEval: A Benchmark for Root Cause Analysis of Microservice Systems with Telemetry Data},
  author={Pham, Luan and Zhang, Hongyu and Ha, Huong and Salim, Flora and Zhang, Xiuzhen},
  booktitle={Companion Proceedings of the ACM on Web Conference 2025},
  pages={777--780},
  year={2025}
}
```

## License

MIT
