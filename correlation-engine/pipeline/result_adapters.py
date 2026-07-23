from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from numbers import Real
from typing import Any


def object_to_dict(
    value: Any,
) -> dict[str, Any]:

    if value is None:
        return {}

    if isinstance(value, Mapping):
        return dict(value)

    if is_dataclass(value):
        return asdict(value)

    if (
        hasattr(value, "to_dict")
        and callable(value.to_dict)
    ):
        result = value.to_dict()

        if isinstance(result, Mapping):
            return dict(result)

    if hasattr(value, "__dict__"):
        return {
            key: item
            for key, item in vars(value).items()
            if not key.startswith("_")
        }

    return {}


def to_float(
    value: Any,
    default: float = 0.0,
) -> float:

    if value is None:
        return default

    if isinstance(value, bool):
        return float(value)

    if isinstance(value, Real):
        converted = float(value)

        if math.isfinite(converted):
            return converted

        return default

    if (
        hasattr(value, "item")
        and callable(value.item)
    ):
        try:
            return to_float(
                value.item(),
                default=default,
            )
        except (TypeError, ValueError):
            return default

    try:
        converted = float(value)
    except (TypeError, ValueError):
        return default

    if not math.isfinite(converted):
        return default

    return converted


def clamp_score(
    value: Any,
) -> float:

    converted = to_float(value)

    return min(
        max(converted, 0.0),
        1.0,
    )


def get_first(
    payload: Mapping[str, Any],
    names: tuple[str, ...],
    default: Any = None,
) -> Any:

    for name in names:
        if name in payload:
            return payload[name]

    return default


def extract_service_name(
    value: Any,
) -> str | None:

    payload = object_to_dict(value)

    service_name = get_first(
        payload,
        (
            "service_name",
            "service",
            "serviceName",
            "name",
        ),
    )

    if service_name is None:
        service_name = (
            getattr(
                value,
                "service_name",
                None,
            )
            or getattr(
                value,
                "service",
                None,
            )
        )

    if service_name is None:
        return None

    normalized = str(service_name).strip()

    return normalized or None


def normalize_feature_vectors(
    features: Any,
) -> dict[str, dict[str, Any]]:

    if features is None:
        return {}

    if isinstance(features, Mapping):

        result: dict[
            str,
            dict[str, Any],
        ] = {}

        for key, value in features.items():

            payload = object_to_dict(value)

            service_name = (
                extract_service_name(value)
                or str(key)
            )

            result[service_name] = payload

        return result

    if isinstance(
        features,
        Sequence,
    ) and not isinstance(
        features,
        (str, bytes, bytearray),
    ):

        result = {}

        for feature in features:

            payload = object_to_dict(feature)

            service_name = extract_service_name(
                feature
            )

            if not service_name:
                raise ValueError(
                    "Não foi possível identificar "
                    "service_name na feature: "
                    f"{feature!r}"
                )

            result[service_name] = payload

        return result

    payload = object_to_dict(features)

    service_name = extract_service_name(
        features
    )

    if not service_name:
        raise ValueError(
            "Não foi possível normalizar "
            "o vetor de features."
        )

    return {
        service_name: payload
    }


class TemporalResultAdapter:

    def adapt(
        self,
        raw: Any,
        services: Sequence[str],
    ) -> dict[str, dict[str, Any]]:

        if raw is None:
            return {
                service: self._empty()
                for service in services
            }

        raw_mapping = object_to_dict(raw)

        # A versão atual do TemporalCorrelator deve
        # produzir esta chave.
        service_payloads = raw_mapping.get(
            "services"
        )

        if not isinstance(
            service_payloads,
            Mapping,
        ):
            service_payloads = raw_mapping

        result = {}

        for service in services:

            current = service_payloads.get(
                service
            )

            if isinstance(current, bool):
                payload = {
                    "is_first_anomaly": current,
                    "score": float(current),
                }

            elif isinstance(current, Real):
                payload = {
                    "score": clamp_score(current),
                }

            else:
                payload = object_to_dict(current)

            score = get_first(
                payload,
                (
                    "score",
                    "temporal_score",
                    "correlation_score",
                    "correlation",
                ),
                default=0.0,
            )

            is_first = bool(
                get_first(
                    payload,
                    (
                        "is_first_anomaly",
                        "is_first_failure",
                        "first_anomaly",
                    ),
                    default=False,
                )
            )

            result[service] = {
                **payload,
                "score": clamp_score(score),
                "is_first_anomaly": is_first,
            }

        return result

    def _empty(self) -> dict[str, Any]:

        return {
            "score": 0.0,
            "is_first_anomaly": False,
        }


class PredictionResultAdapter:

    def adapt(
        self,
        raw: Any,
        services: Sequence[str],
    ) -> dict[str, dict[str, Any]]:

        raw_mapping: dict[
            str,
            Any,
        ] = {}

        if (
            isinstance(
                raw,
                Sequence,
            )
            and not isinstance(
                raw,
                (
                    str,
                    bytes,
                    bytearray,
                ),
            )
        ):

            for item in raw:

                payload = object_to_dict(
                    item
                )

                service_name = (
                    extract_service_name(
                        item
                    )
                )

                if not service_name:
                    continue

                raw_mapping[
                    service_name
                ] = payload

        else:

            raw_mapping = (
                object_to_dict(
                    raw
                )
            )

            nested = get_first(
                raw_mapping,
                (
                    "probabilities",
                    "predictions",
                    "scores",
                    "services",
                ),
            )

            if isinstance(
                nested,
                Mapping,
            ):
                raw_mapping = dict(
                    nested
                )

        result: dict[
            str,
            dict[str, Any],
        ] = {}

        for service in services:

            current = raw_mapping.get(
                service,
                0.0,
            )

            if isinstance(
                current,
                Mapping,
            ):

                payload = dict(
                    current
                )

                probability = get_first(
                    payload,
                    (
                        "probability",
                        "confidence",
                        "score",
                        "prediction",
                    ),
                    default=0.0,
                )

            else:

                payload = {}

                probability = current

            result[service] = {
                **payload,

                "probability": clamp_score(
                    probability
                ),
            }

        return result

class ShapResultAdapter:

    def adapt(
        self,
        raw: Any,
        services: Sequence[str],
        feature_vectors: Mapping[
            str,
            Mapping[str, Any],
        ],
    ) -> dict[str, dict[str, Any]]:

        raw_mapping = object_to_dict(raw)

        nested = get_first(
            raw_mapping,
            (
                "services",
                "results",
                "explanations",
            ),
        )

        if isinstance(nested, Mapping):
            raw_mapping = dict(nested)

        result = {}

        raw_support_scores = {}

        for service in services:

            current = raw_mapping.get(
                service
            )

            payload = object_to_dict(current)

            contributions = (
                self._extract_contributions(
                    current=current,
                    feature_values=(
                        feature_vectors.get(
                            service,
                            {},
                        )
                    ),
                )
            )

            positive_support = sum(
                max(
                    contribution[
                        "shap_value"
                    ],
                    0.0,
                )
                for contribution in contributions
            )

            raw_support_scores[
                service
            ] = positive_support

            result[service] = {
                **payload,
                "raw_positive_support": (
                    positive_support
                ),
                "contributions": contributions,
            }

        normalized = self._normalize_support(
            raw_support_scores
        )

        for service in services:
            result[service][
                "support_score"
            ] = normalized.get(
                service,
                0.0,
            )

        return result

    def _extract_contributions(
        self,
        current: Any,
        feature_values: Mapping[
            str,
            Any,
        ],
    ) -> list[dict[str, Any]]:

        if current is None:
            return []

        if isinstance(current, Real):

            value = to_float(current)

            return [
                {
                    "feature_name": (
                        "__aggregate__"
                    ),
                    "feature_value": None,
                    "shap_value": value,
                    "direction": (
                        self._direction(value)
                    ),
                }
            ]

        payload = object_to_dict(current)

        explicit = get_first(
            payload,
            (
                "contributions",
                "feature_contributions",
            ),
        )

        if isinstance(explicit, Sequence):

            result = []

            for item in explicit:

                contribution = object_to_dict(
                    item
                )

                feature_name = get_first(
                    contribution,
                    (
                        "feature_name",
                        "feature",
                        "name",
                    ),
                )

                shap_value = get_first(
                    contribution,
                    (
                        "shap_value",
                        "contribution",
                        "value",
                    ),
                )

                if feature_name is None:
                    continue

                converted_value = to_float(
                    shap_value
                )

                result.append(
                    {
                        "feature_name": str(
                            feature_name
                        ),
                        "feature_value": (
                            contribution.get(
                                "feature_value",
                                feature_values.get(
                                    str(
                                        feature_name
                                    )
                                ),
                            )
                        ),
                        "shap_value": (
                            converted_value
                        ),
                        "direction": (
                            self._direction(
                                converted_value
                            )
                        ),
                    }
                )

            return result

        ignored = {
            "service",
            "service_name",
            "score",
            "support_score",
            "predicted",
        }

        result = []

        for feature_name, value in payload.items():

            if feature_name in ignored:
                continue

            if not isinstance(value, Real):
                continue

            converted_value = to_float(value)

            result.append(
                {
                    "feature_name": str(
                        feature_name
                    ),
                    "feature_value": (
                        feature_values.get(
                            str(feature_name)
                        )
                    ),
                    "shap_value": (
                        converted_value
                    ),
                    "direction": (
                        self._direction(
                            converted_value
                        )
                    ),
                }
            )

        return result

    def _normalize_support(
        self,
        values: Mapping[str, float],
    ) -> dict[str, float]:

        if not values:
            return {}

        maximum = max(values.values())

        if maximum <= 0.0:
            return {
                service: 0.0
                for service in values
            }

        return {
            service: clamp_score(
                value / maximum
            )
            for service, value in values.items()
        }

    def _direction(
        self,
        value: float,
    ) -> str:

        if value > 0:
            return "supporting"

        if value < 0:
            return "opposing"

        return "neutral"


class FeatureEvidenceAdapter:

    def adapt_graph(
        self,
        feature_vectors: Mapping[
            str,
            Mapping[str, Any],
        ],
    ) -> dict[str, dict[str, Any]]:

        result = {}

        for service, payload in (
            feature_vectors.items()
        ):

            score = get_first(
                payload,
                (
                    "graph_score",
                    "structural_score",
                    "centrality",
                ),
                default=0.0,
            )

            result[service] = {
                "score": clamp_score(score),
                "centrality": payload.get(
                    "centrality"
                ),
                "in_degree": payload.get(
                    "in_degree"
                ),
                "out_degree": payload.get(
                    "out_degree"
                ),
            }

        return result

    def adapt_anomaly(
        self,
        feature_vectors: Mapping[
            str,
            Mapping[str, Any],
        ],
    ) -> dict[str, dict[str, Any]]:

        raw_scores = {}

        result = {}

        for service, payload in (
            feature_vectors.items()
        ):

            zscore = get_first(
                payload,
                (
                    "latency_zscore",
                    "zscore",
                    "z_score",
                    "anomaly_zscore",
                ),
            )

            explicit_score = get_first(
                payload,
                (
                    "anomaly_score",
                    "normalized_anomaly_score",
                ),
            )

            if explicit_score is not None:
                raw_score = to_float(
                    explicit_score
                )
            elif zscore is not None:
                raw_score = abs(
                    to_float(zscore)
                )
            else:
                raw_score = 0.0

            raw_scores[service] = raw_score

            result[service] = {
                "raw_score": raw_score,
                "zscore": (
                    to_float(zscore)
                    if zscore is not None
                    else None
                ),
                "error_count": payload.get(
                    "error_count",
                    payload.get("errors"),
                ),
                "error_rate": payload.get(
                    "error_rate"
                ),
            }

        maximum = max(
            raw_scores.values(),
            default=0.0,
        )

        for service in result:

            result[service]["score"] = (
                clamp_score(
                    raw_scores[service]
                    / maximum
                )
                if maximum > 0
                else 0.0
            )

        return result