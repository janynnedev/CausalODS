from pathlib import Path

import joblib

from config.settings import Settings


class ModelRegistry:

    def save(
        self,
        model,
        path=None,
    ):

        resolved_path = Path(
            path
            if path is not None
            else Settings.MODEL_PATH
        ).expanduser()

        if not resolved_path.is_absolute():

            resolved_path = (
                Path.cwd()
                / resolved_path
            )

        resolved_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        joblib.dump(
            model,
            resolved_path,
        )

        return resolved_path

    def load(
        self,
        path=None,
    ):

        resolved_path = Path(
            path
            if path is not None
            else Settings.MODEL_PATH
        ).expanduser()

        if not resolved_path.is_file():

            raise FileNotFoundError(
                "Modelo não encontrado: "
                f"{resolved_path}"
            )

        return joblib.load(
            resolved_path
        )