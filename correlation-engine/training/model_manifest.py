from __future__ import annotations

import json

from dataclasses import (
    asdict,
    dataclass,
    field,
)
from datetime import (
    datetime,
    timezone,
)
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ModelManifest:
    """
    Metadados que acompanham um modelo treinado.

    O manifesto impede que um modelo treinado com
    um schema seja carregado com uma ordem diferente
    de features durante a inferência.
    """

    model_version: str

    feature_schema_version: str

    feature_columns: tuple[
        str,
        ...
    ]

    model_filename: str

    trained_at: str

    metrics: dict[
        str,
        Any,
    ] = field(
        default_factory=dict
    )

    training_metadata: dict[
        str,
        Any,
    ] = field(
        default_factory=dict
    )

    def __post_init__(
        self,
    ) -> None:

        if not self.model_version.strip():

            raise ValueError(
                "model_version não pode ser vazio."
            )

        if not (
            self.feature_schema_version
            .strip()
        ):

            raise ValueError(
                (
                    "feature_schema_version "
                    "não pode ser vazio."
                )
            )

        if not self.model_filename.strip():

            raise ValueError(
                (
                    "model_filename não pode "
                    "ser vazio."
                )
            )

        if not self.feature_columns:

            raise ValueError(
                (
                    "feature_columns deve conter "
                    "ao menos uma feature."
                )
            )

        if len(
            self.feature_columns
        ) != len(
            set(
                self.feature_columns
            )
        ):

            raise ValueError(
                (
                    "feature_columns contém "
                    "nomes duplicados."
                )
            )

    @classmethod
    def create(
        cls,
        *,
        model_version: str,
        feature_schema_version: str,
        feature_columns: tuple[
            str,
            ...
        ],
        model_filename: str,
        metrics: dict[
            str,
            Any,
        ] | None = None,
        training_metadata: dict[
            str,
            Any,
        ] | None = None,
    ) -> ModelManifest:

        return cls(
            model_version=model_version,

            feature_schema_version=(
                feature_schema_version
            ),

            feature_columns=tuple(
                feature_columns
            ),

            model_filename=model_filename,

            trained_at=(
                datetime.now(
                    timezone.utc
                )
                .isoformat()
            ),

            metrics=(
                dict(metrics)
                if metrics
                else {}
            ),

            training_metadata=(
                dict(
                    training_metadata
                )
                if training_metadata
                else {}
            ),
        )

    def to_dict(
        self,
    ) -> dict[
        str,
        Any,
    ]:

        payload = asdict(
            self
        )

        payload[
            "feature_columns"
        ] = list(
            self.feature_columns
        )

        return payload

    def save(
        self,
        path: str | Path,
    ) -> Path:

        destination = Path(
            path
        )

        destination.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        destination.write_text(
            json.dumps(
                self.to_dict(),
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        return destination

    @classmethod
    def load(
        cls,
        path: str | Path,
    ) -> ModelManifest:

        source = Path(
            path
        )

        payload = json.loads(
            source.read_text(
                encoding="utf-8"
            )
        )

        return cls(
            model_version=str(
                payload[
                    "model_version"
                ]
            ),

            feature_schema_version=str(
                payload[
                    "feature_schema_version"
                ]
            ),

            feature_columns=tuple(
                payload[
                    "feature_columns"
                ]
            ),

            model_filename=str(
                payload[
                    "model_filename"
                ]
            ),

            trained_at=str(
                payload[
                    "trained_at"
                ]
            ),

            metrics=dict(
                payload.get(
                    "metrics",
                    {},
                )
            ),

            training_metadata=dict(
                payload.get(
                    "training_metadata",
                    {},
                )
            ),
        )

    def validate_feature_columns(
        self,
        actual_columns: tuple[
            str,
            ...
        ],
    ) -> None:

        actual_columns = tuple(
            actual_columns
        )

        if (
            actual_columns
            != self.feature_columns
        ):

            raise ValueError(
                (
                    "Schema de features incompatível "
                    "com o manifesto do modelo. "
                    f"esperado={self.feature_columns}; "
                    f"encontrado={actual_columns}."
                )
            )