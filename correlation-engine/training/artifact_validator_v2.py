from __future__ import annotations

import hashlib
import json

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib

from models.rca_feature_schema_v2 import (
    FEATURE_SCHEMA_VERSION,
    MODEL_FEATURE_COLUMNS,
)


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)


class ArtifactValidationError(
    RuntimeError
):
    """
    Indica incompatibilidade entre modelo,
    manifesto e schema de features.
    """


@dataclass(
    frozen=True
)
class ValidatedArtifactBundleV2:

    model: Any

    model_path: Path

    manifest_path: Path

    manifest: dict[str, Any]

    @property
    def model_version(
        self,
    ) -> str:

        return str(
            self.manifest[
                "model_version"
            ]
        )

    @property
    def feature_schema_version(
        self,
    ) -> str:

        return str(
            self.manifest[
                "feature_schema_version"
            ]
        )

    @property
    def feature_columns(
        self,
    ) -> tuple[str, ...]:

        return tuple(
            self.manifest[
                "feature_columns"
            ]
        )


class ArtifactValidatorV2:

    EXPECTED_MODEL_VERSION = (
        "xgboost-rca-v2"
    )

    def load_and_validate(
        self,
        *,
        model_path: str | Path,
        manifest_path: str | Path,
    ) -> ValidatedArtifactBundleV2:

        resolved_model_path = (
            self.resolve_path(
                model_path
            )
        )

        resolved_manifest_path = (
            self.resolve_path(
                manifest_path
            )
        )

        self._require_file(
            resolved_model_path,
            description=(
                "modelo XGBoost v2"
            ),
        )

        self._require_file(
            resolved_manifest_path,
            description=(
                "manifesto do modelo v2"
            ),
        )

        manifest = (
            self._load_manifest(
                resolved_manifest_path
            )
        )

        self.validate_manifest(
            manifest=manifest,
            model_path=(
                resolved_model_path
            ),
        )

        try:

            model = joblib.load(
                resolved_model_path
            )

        except Exception as exception:

            raise ArtifactValidationError(
                (
                    "Não foi possível carregar "
                    "o modelo v2 em "
                    f"{resolved_model_path}: "
                    f"{type(exception).__name__}: "
                    f"{exception}"
                )
            ) from exception

        self.validate_model(
            model=model,
            manifest=manifest,
        )

        self._validate_optional_hash(
            path=resolved_model_path,
            expected_hash=(
                manifest.get(
                    "model_sha256"
                )
            ),
            artifact_name="modelo",
        )

        return ValidatedArtifactBundleV2(
            model=model,
            model_path=(
                resolved_model_path
            ),
            manifest_path=(
                resolved_manifest_path
            ),
            manifest=manifest,
        )

    @staticmethod
    def resolve_path(
        path_value: str | Path,
    ) -> Path:

        path = Path(
            path_value
        ).expanduser()

        if not path.is_absolute():

            path = (
                PROJECT_ROOT
                / path
            )

        return path.resolve()

    def validate_manifest(
        self,
        *,
        manifest: dict[str, Any],
        model_path: Path,
    ) -> None:

        required_fields = (
            "model_version",
            "model_filename",
            "feature_schema_version",
            "feature_columns",
            "trained_at",
            "metrics",
            "training_metadata",
        )

        missing_fields = [
            field_name
            for field_name
            in required_fields
            if field_name not in manifest
        ]

        if missing_fields:

            raise ArtifactValidationError(
                (
                    "Manifesto v2 incompleto. "
                    "Campos ausentes: "
                    f"{missing_fields}."
                )
            )

        model_version = str(
            manifest[
                "model_version"
            ]
        )

        if (
            model_version
            != self.EXPECTED_MODEL_VERSION
        ):

            raise ArtifactValidationError(
                (
                    "Versão de modelo incompatível. "
                    f"Esperado={self.EXPECTED_MODEL_VERSION!r}; "
                    f"encontrado={model_version!r}."
                )
            )

        schema_version = str(
            manifest[
                "feature_schema_version"
            ]
        )

        if (
            schema_version
            != FEATURE_SCHEMA_VERSION
        ):

            raise ArtifactValidationError(
                (
                    "Versão de schema incompatível. "
                    f"Esperado={FEATURE_SCHEMA_VERSION!r}; "
                    f"encontrado={schema_version!r}."
                )
            )

        manifest_columns = (
            manifest[
                "feature_columns"
            ]
        )

        if not isinstance(
            manifest_columns,
            list,
        ):

            raise ArtifactValidationError(
                (
                    "feature_columns deve ser "
                    "uma lista ordenada."
                )
            )

        if len(
            manifest_columns
        ) != len(
            set(
                manifest_columns
            )
        ):

            raise ArtifactValidationError(
                (
                    "Manifesto contém features "
                    "duplicadas."
                )
            )

        expected_columns = list(
            MODEL_FEATURE_COLUMNS
        )

        if (
            manifest_columns
            != expected_columns
        ):

            raise ArtifactValidationError(
                (
                    "Features do manifesto não "
                    "correspondem ao schema v2. "
                    f"Esperado={expected_columns}; "
                    f"encontrado={manifest_columns}."
                )
            )

        model_filename = str(
            manifest[
                "model_filename"
            ]
        )

        if (
            model_filename
            != model_path.name
        ):

            raise ArtifactValidationError(
                (
                    "Nome do modelo não corresponde "
                    "ao manifesto. "
                    f"Manifesto={model_filename!r}; "
                    f"arquivo={model_path.name!r}."
                )
            )

    def validate_model(
        self,
        *,
        model: Any,
        manifest: dict[str, Any],
    ) -> None:

        if not callable(
            getattr(
                model,
                "predict_proba",
                None,
            )
        ):

            raise ArtifactValidationError(
                (
                    "O artefato carregado não "
                    "implementa predict_proba()."
                )
            )

        if not callable(
            getattr(
                model,
                "get_booster",
                None,
            )
        ):

            raise ArtifactValidationError(
                (
                    "O artefato carregado não "
                    "parece ser um modelo XGBoost."
                )
            )

        booster = (
            model.get_booster()
        )

        model_columns = (
            booster.feature_names
        )

        if not model_columns:

            raise ArtifactValidationError(
                (
                    "O modelo XGBoost não possui "
                    "feature_names persistidas."
                )
            )

        manifest_columns = list(
            manifest[
                "feature_columns"
            ]
        )

        if (
            list(model_columns)
            != manifest_columns
        ):

            raise ArtifactValidationError(
                (
                    "Features do modelo não "
                    "correspondem ao manifesto. "
                    f"Manifesto={manifest_columns}; "
                    f"modelo={list(model_columns)}."
                )
            )

        classes = list(
            getattr(
                model,
                "classes_",
                (),
            )
        )

        if classes and 1 not in classes:

            raise ArtifactValidationError(
                (
                    "O modelo não contém a classe "
                    "positiva root_cause=1. "
                    f"classes={classes}."
                )
            )

    @staticmethod
    def _require_file(
        path: Path,
        *,
        description: str,
    ) -> None:

        if not path.is_file():

            raise FileNotFoundError(
                (
                    f"{description} não encontrado. "
                    f"Caminho resolvido: {path}."
                )
            )

    @staticmethod
    def _load_manifest(
        path: Path,
    ) -> dict[str, Any]:

        try:

            payload = json.loads(
                path.read_text(
                    encoding="utf-8"
                )
            )

        except json.JSONDecodeError as exception:

            raise ArtifactValidationError(
                (
                    "Manifesto v2 não contém "
                    "JSON válido: "
                    f"{path}: {exception}"
                )
            ) from exception

        if not isinstance(
            payload,
            dict,
        ):

            raise ArtifactValidationError(
                (
                    "Manifesto v2 deve ser "
                    "um objeto JSON."
                )
            )

        return payload

    def _validate_optional_hash(
        self,
        *,
        path: Path,
        expected_hash: Any,
        artifact_name: str,
    ) -> None:

        if not expected_hash:
            return

        actual_hash = (
            self.sha256_file(
                path
            )
        )

        if (
            actual_hash
            != str(expected_hash)
        ):

            raise ArtifactValidationError(
                (
                    f"SHA-256 do {artifact_name} "
                    "não corresponde ao manifesto. "
                    f"Esperado={expected_hash}; "
                    f"encontrado={actual_hash}."
                )
            )

    @staticmethod
    def sha256_file(
        path: Path,
    ) -> str:

        digest = (
            hashlib.sha256()
        )

        with path.open(
            "rb"
        ) as file_handle:

            for chunk in iter(
                lambda: file_handle.read(
                    1024 * 1024
                ),
                b"",
            ):

                digest.update(
                    chunk
                )

        return digest.hexdigest()