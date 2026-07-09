import os

KAFKA_BOOTSTRAP = os.getenv(
    "KAFKA_BOOTSTRAP",
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

OUTPUT_DIR = "output"

LOG_LEVEL = "INFO"