from models.rca_feature_schema import (
    MODEL_FEATURE_COLUMNS,
)

from training.predictor import Predictor


def test_trained_model_uses_canonical_feature_schema():

    predictor = Predictor()

    model_feature_names = (
        predictor.model
        .get_booster()
        .feature_names
    )

    assert tuple(
        model_feature_names
    ) == MODEL_FEATURE_COLUMNS