from pathlib import Path

import joblib
import pytest

from config.settings import Settings
from shap_analysis.shap_service import SHAPService
from training.data_loader import DataLoader
from training.preprocessing import Preprocessor


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)


def resolve_model_path() -> Path:
    """
    Resolve o MODEL_PATH da mesma forma no host,
    devcontainer e Docker.
    """

    configured_path = Path(
        Settings.MODEL_PATH
    )

    if configured_path.is_absolute():
        return configured_path

    return (
        PROJECT_ROOT
        / configured_path
    )


@pytest.fixture(scope="module")
def trained_model():

    model_path = resolve_model_path()

    if not model_path.exists():

        pytest.fail(
            "Modelo XGBoost não encontrado. "
            f"Caminho configurado: {model_path}"
        )

    return joblib.load(
        model_path
    )


@pytest.fixture(scope="module")
def sample_features():

    dataset_path = (
        PROJECT_ROOT
        / "results_rca_dataset.csv"
    )

    if not dataset_path.exists():

        pytest.fail(
            "Dataset RCA não encontrado. "
            f"Caminho esperado: {dataset_path}"
        )

    loader = DataLoader()

    dataframe = loader.load(
        str(dataset_path)
    )

    preprocessor = Preprocessor()

    features, labels = (
        preprocessor.prepare(
            dataframe
        )
    )

    assert features is not None
    assert not features.empty

    sample = features.iloc[:5]

    assert len(sample) > 0

    return sample


def test_shap_service_generates_explanation(
    trained_model,
    sample_features,
):

    service = SHAPService(
        trained_model
    )

    result = service.explain(
        sample_features
    )

    assert result is not None

    assert hasattr(
        result,
        "__len__",
    )

    assert len(result) > 0