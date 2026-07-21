import pandas as pd

from models.rca_feature_schema import (
    BOOLEAN_FEATURE_COLUMNS,
    FLOAT_FEATURE_COLUMNS,
    INTEGER_FEATURE_COLUMNS,
    MODEL_FEATURE_COLUMNS,
    TARGET_COLUMN,
)


class Preprocessor:

    def prepare(
        self,
        dataframe,
    ):

        df = dataframe.copy()

        required_columns = set(
            MODEL_FEATURE_COLUMNS
        )

        required_columns.add(
            TARGET_COLUMN
        )

        missing_columns = (
            required_columns
            - set(df.columns)
        )

        if missing_columns:

            raise ValueError(
                "Dataset incompatível com o schema RCA. "
                "Colunas ausentes: "
                f"{sorted(missing_columns)}"
            )

        X = df.loc[
            :,
            list(MODEL_FEATURE_COLUMNS),
        ].copy()

        for column in BOOLEAN_FEATURE_COLUMNS:

            X[column] = (
                X[column]
                .fillna(False)
                .astype(int)
            )

        for column in INTEGER_FEATURE_COLUMNS:

            X[column] = pd.to_numeric(
                X[column],
                errors="raise",
            ).astype(int)

        for column in FLOAT_FEATURE_COLUMNS:

            X[column] = pd.to_numeric(
                X[column],
                errors="raise",
            ).astype(float)

        y = pd.to_numeric(
            df[TARGET_COLUMN],
            errors="raise",
        ).astype(int)

        return X, y