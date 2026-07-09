from kafka import KafkaConsumer

from consumer.otlp_deserializer import OTLPDeserializer
from inspector.otlp_inspector import OTLPInspector


consumer = KafkaConsumer(
    "traces",
    bootstrap_servers="kafka:9092"
)

deserializer = OTLPDeserializer()

inspector = OTLPInspector()


for message in consumer:

    request = deserializer.deserialize(message.value)

    inspector.inspect(request)

    break