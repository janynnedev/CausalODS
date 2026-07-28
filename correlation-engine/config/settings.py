import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]

class Settings:

    KAFKA_BOOTSTRAP_SERVERS = os.getenv(
        "KAFKA_BOOTSTRAP_SERVERS",
        "kafka:9092"
    )

    KAFKA_TOPIC = os.getenv(
        "KAFKA_TOPIC",
        "traces"
    )

    CONSUMER_GROUP = os.getenv(
        "CONSUMER_GROUP",
        "correlation-engine"
    )

    OUTPUT_DIR = os.getenv(
        "OUTPUT_DIR",
        "output"
    )

    LOG_LEVEL = os.getenv(
        "LOG_LEVEL",
        "INFO"
    )

    OPENSEARCH_HOST = os.getenv(
        "OPENSEARCH_HOST",
        "causalods-opensearch"
    )

    OPENSEARCH_PORT = int(
        os.getenv(
            "OPENSEARCH_PORT",
            "9200"
        )
    )

    MODEL_PATH = os.getenv(
        "MODEL_PATH",
        str(
            BASE_DIR
            / "training"
            / "models"
            / "xgboost_rca.pkl"
        ),
    )

    RCA_MODEL_VERSION = (
        os.getenv(
            "RCA_MODEL_VERSION",
            "v1",
        )
        .strip()
        .lower()
    )

    MODEL_PATH_V2 = os.getenv(
        "MODEL_PATH_V2",
        (
            "training/models/"
            "xgboost_rca_v2.pkl"
        ),
    )

    MODEL_MANIFEST_PATH_V2 = os.getenv(
        "MODEL_MANIFEST_PATH_V2",
        (
            "training/models/"
            "xgboost_rca_v2.manifest.json"
        ),
    )

    SHAP_CACHE = os.getenv(
        "SHAP_CACHE",
        "training/models/shap.pkl"
    )