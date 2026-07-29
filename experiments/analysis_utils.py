from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import statistics

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any


JsonValue = Any


def read_json(path: Path) -> JsonValue:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: JsonValue) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = [dict(row) for row in rows]

    if not normalized:
        path.write_text("", encoding="utf-8")
        return

    fieldnames: list[str] = []
    seen: set[str] = set()

    for row in normalized:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(normalized)


def as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None

    try:
        converted = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(converted):
        return None

    return converted


def as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def percentile(values: Sequence[float], quantile: float) -> float | None:
    if not values:
        return None

    if not 0.0 <= quantile <= 1.0:
        raise ValueError("quantile deve estar entre 0 e 1")

    ordered = sorted(float(value) for value in values)

    if len(ordered) == 1:
        return ordered[0]

    position = (len(ordered) - 1) * quantile
    lower_index = math.floor(position)
    upper_index = math.ceil(position)

    if lower_index == upper_index:
        return ordered[lower_index]

    fraction = position - lower_index
    return (
        ordered[lower_index] * (1.0 - fraction)
        + ordered[upper_index] * fraction
    )


def describe(values: Iterable[float | int | None]) -> dict[str, float | int | None]:
    numbers = [
        converted
        for value in values
        if (converted := as_float(value)) is not None
    ]

    if not numbers:
        return {
            "count": 0,
            "mean": None,
            "median": None,
            "standard_deviation": None,
            "minimum": None,
            "p05": None,
            "p25": None,
            "p75": None,
            "p95": None,
            "maximum": None,
        }

    return {
        "count": len(numbers),
        "mean": statistics.fmean(numbers),
        "median": statistics.median(numbers),
        "standard_deviation": (
            statistics.stdev(numbers) if len(numbers) >= 2 else 0.0
        ),
        "minimum": min(numbers),
        "p05": percentile(numbers, 0.05),
        "p25": percentile(numbers, 0.25),
        "p75": percentile(numbers, 0.75),
        "p95": percentile(numbers, 0.95),
        "maximum": max(numbers),
    }


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float | None, float | None]:
    if total <= 0:
        return None, None

    proportion = successes / total
    denominator = 1.0 + (z * z / total)
    center = (
        proportion + (z * z / (2.0 * total))
    ) / denominator
    half_width = (
        z
        * math.sqrt(
            (proportion * (1.0 - proportion) / total)
            + (z * z / (4.0 * total * total))
        )
        / denominator
    )

    return max(0.0, center - half_width), min(1.0, center + half_width)


def proportion_summary(values: Iterable[bool | None]) -> dict[str, float | int | None]:
    evaluated = [value for value in values if value is not None]
    total = len(evaluated)
    successes = sum(bool(value) for value in evaluated)
    lower, upper = wilson_interval(successes, total)

    return {
        "evaluated": total,
        "successes": successes,
        "failures": total - successes,
        "rate": successes / total if total else None,
        "ci95_lower": lower,
        "ci95_upper": upper,
    }


def bootstrap_mean_interval(
    values: Sequence[float],
    *,
    samples: int = 5000,
    seed: int = 42,
    confidence: float = 0.95,
) -> tuple[float | None, float | None]:
    if not values:
        return None, None

    if len(values) == 1:
        return float(values[0]), float(values[0])

    if samples < 100:
        raise ValueError("samples deve ser pelo menos 100")

    generator = random.Random(seed)
    n = len(values)
    means: list[float] = []

    for _ in range(samples):
        means.append(
            statistics.fmean(values[generator.randrange(n)] for _ in range(n))
        )

    alpha = (1.0 - confidence) / 2.0
    return percentile(means, alpha), percentile(means, 1.0 - alpha)


def paired_bootstrap_difference(
    left: Sequence[float],
    right: Sequence[float],
    *,
    samples: int = 5000,
    seed: int = 42,
    confidence: float = 0.95,
) -> dict[str, float | int | None]:
    if len(left) != len(right):
        raise ValueError("As amostras pareadas devem ter o mesmo tamanho")

    differences = [float(r) - float(l) for l, r in zip(left, right)]

    if not differences:
        return {
            "pairs": 0,
            "mean_difference_right_minus_left": None,
            "median_difference_right_minus_left": None,
            "ci95_lower": None,
            "ci95_upper": None,
        }

    lower, upper = bootstrap_mean_interval(
        differences,
        samples=samples,
        seed=seed,
        confidence=confidence,
    )

    return {
        "pairs": len(differences),
        "mean_difference_right_minus_left": statistics.fmean(differences),
        "median_difference_right_minus_left": statistics.median(differences),
        "ci95_lower": lower,
        "ci95_upper": upper,
    }


def exact_binomial_two_sided(successes: int, trials: int, probability: float = 0.5) -> float | None:
    if trials <= 0:
        return None

    if not math.isclose(probability, 0.5, abs_tol=1e-12):
        raise NotImplementedError("Esta implementação exata suporta p=0.5")

    extreme = min(successes, trials - successes)
    cumulative = sum(
        math.comb(trials, k) * (0.5 ** trials)
        for k in range(extreme + 1)
    )
    return min(1.0, 2.0 * cumulative)


def mcnemar_exact(left: Sequence[bool], right: Sequence[bool]) -> dict[str, float | int | None]:
    if len(left) != len(right):
        raise ValueError("As amostras pareadas devem ter o mesmo tamanho")

    left_only = 0
    right_only = 0
    both_correct = 0
    both_wrong = 0

    for left_value, right_value in zip(left, right):
        if left_value and right_value:
            both_correct += 1
        elif left_value and not right_value:
            left_only += 1
        elif not left_value and right_value:
            right_only += 1
        else:
            both_wrong += 1

    discordant = left_only + right_only
    p_value = (
        exact_binomial_two_sided(left_only, discordant)
        if discordant
        else 1.0
    )

    return {
        "pairs": len(left),
        "both_correct": both_correct,
        "both_wrong": both_wrong,
        "left_only_correct": left_only,
        "right_only_correct": right_only,
        "discordant_pairs": discordant,
        "p_value_two_sided": p_value,
    }


def flatten_mapping(
    payload: Mapping[str, Any],
    *,
    prefix: str = "",
    separator: str = ".",
) -> dict[str, Any]:
    flattened: dict[str, Any] = {}

    for key, value in payload.items():
        path = f"{prefix}{separator}{key}" if prefix else str(key)

        if isinstance(value, Mapping):
            flattened.update(
                flatten_mapping(value, prefix=path, separator=separator)
            )
        elif isinstance(value, (list, tuple)):
            flattened[path] = json.dumps(value, ensure_ascii=False)
        else:
            flattened[path] = value

    return flattened


def generate_sha256sums(root: Path) -> Path:
    output = root / "SHA256SUMS"
    lines: list[str] = []

    for path in sorted(root.rglob("*")):
        if not path.is_file() or path == output:
            continue

        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        relative = path.relative_to(root).as_posix()
        lines.append(f"{digest}  {relative}")

    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output
