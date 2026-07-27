from __future__ import annotations

from pathlib import Path

import pandas as pd

from dataset.feature_generator_v2 import (
    FeatureGeneratorV2,
)
from dataset.scenario_generator_v2 import (
    ScenarioGeneratorV2,
)
from models.rca_feature_schema_v2 import (
    METADATA_COLUMNS,
    MODEL_FEATURE_COLUMNS,
    TARGET_COLUMN,
)


DATASET_COLUMNS = (
    *METADATA_COLUMNS,
    *MODEL_FEATURE_COLUMNS,
    TARGET_COLUMN,
)


class DatasetBuilderV2:
    """
    Produz um dataset auditável com uma linha
    por serviço e duas linhas por amostra.
    """

    def __init__(
        self,
        *,
        seed: int = 42,
    ) -> None:

        self.seed = int(seed)

        self.scenario_generator = (
            ScenarioGeneratorV2(
                seed=self.seed
            )
        )

        # Usa outra sequência determinística
        # para evitar acoplamento direto entre
        # escolha do cenário e ruído das features.
        self.feature_generator = (
            FeatureGeneratorV2(
                seed=self.seed + 1
            )
        )

    def generate(
        self,
        *,
        samples: int = 5000,
    ) -> list[dict]:

        if samples <= 0:

            raise ValueError(
                "samples deve ser maior que zero."
            )

        rows: list[dict] = []

        for index in range(
            int(samples)
        ):

            scenario = (
                self.scenario_generator
                .generate(index)
            )

            rows.extend(
                self.feature_generator
                .generate(
                    scenario
                )
            )

        return rows

    def to_dataframe(
        self,
        *,
        samples: int = 5000,
    ) -> pd.DataFrame:

        rows = self.generate(
            samples=samples
        )

        dataframe = pd.DataFrame(
            rows,
            columns=list(
                DATASET_COLUMNS
            ),
        )

        self.validate(
            dataframe
        )

        return dataframe

    def save_csv(
        self,
        path: str | Path,
        *,
        samples: int = 5000,
    ) -> Path:

        destination = Path(
            path
        )

        destination.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        dataframe = self.to_dataframe(
            samples=samples
        )

        dataframe.to_csv(
            destination,
            index=False,
        )

        return destination

    @staticmethod
    def validate(
        dataframe: pd.DataFrame,
    ) -> None:

        expected_columns = list(
            DATASET_COLUMNS
        )

        if (
            dataframe.columns.tolist()
            != expected_columns
        ):

            raise ValueError(
                (
                    "Colunas do dataset v2 "
                    "estão fora do contrato. "
                    f"esperado={expected_columns}; "
                    "encontrado="
                    f"{dataframe.columns.tolist()}."
                )
            )

        if dataframe.empty:

            raise ValueError(
                "O dataset v2 está vazio."
            )

        required_non_null_columns = [
            "sample_id",
            "scenario",
            "service_name",
            "fault_delay_ms",
            "generation_seed",
            *MODEL_FEATURE_COLUMNS,
            TARGET_COLUMN,
        ]

        required_null_mask = (
            dataframe[
                required_non_null_columns
            ]
            .isna()
            .any()
        )

        if required_null_mask.any():

            null_columns = (
                required_null_mask[
                    required_null_mask
                ]
                .index
                .tolist()
            )

            raise ValueError(
                (
                    "Dataset v2 contém valores "
                    "nulos em colunas obrigatórias: "
                    f"{null_columns}."
                )
            )

        healthy_rows = dataframe[
            dataframe["scenario"]
            == "healthy"
        ]

        fault_rows = dataframe[
            dataframe["scenario"]
            != "healthy"
        ]

        if (
            healthy_rows[
                "root_cause_service"
            ]
            .notna()
            .any()
        ):

            raise ValueError(
                (
                    "Cenários healthy não devem "
                    "possuir root_cause_service."
                )
            )

        if (
            fault_rows[
                "root_cause_service"
            ]
            .isna()
            .any()
        ):

            raise ValueError(
                (
                    "Cenários com falha devem "
                    "possuir root_cause_service."
                )
            )

        rows_per_sample = (
            dataframe
            .groupby(
                "sample_id"
            )
            .size()
        )

        invalid_samples = (
            rows_per_sample[
                rows_per_sample != 2
            ]
        )

        if not invalid_samples.empty:

            raise ValueError(
                (
                    "Cada amostra deve conter "
                    "duas linhas. Amostras inválidas: "
                    f"{invalid_samples.to_dict()}."
                )
            )

        positive_per_sample = (
            dataframe
            .groupby(
                "sample_id"
            )[
                TARGET_COLUMN
            ]
            .sum()
        )

        scenario_per_sample = (
            dataframe
            .groupby(
                "sample_id"
            )[
                "scenario"
            ]
            .first()
        )

        healthy_ids = (
            scenario_per_sample[
                scenario_per_sample
                == "healthy"
            ]
            .index
        )

        fault_ids = (
            scenario_per_sample[
                scenario_per_sample
                != "healthy"
            ]
            .index
        )

        if (
            positive_per_sample
            .loc[
                healthy_ids
            ]
            .ne(0)
            .any()
        ):

            raise ValueError(
                (
                    "Cenário saudável contém "
                    "rótulo positivo."
                )
            )

        if (
            positive_per_sample
            .loc[
                fault_ids
            ]
            .ne(1)
            .any()
        ):

            raise ValueError(
                (
                    "Cada cenário de falha deve "
                    "conter exatamente uma causa raiz."
                )
            )