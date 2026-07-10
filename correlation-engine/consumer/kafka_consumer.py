from kafka import KafkaConsumer
from config.settings import Settings
from consumer.otlp_deserializer import OTLPDeserializer
from consumer.otlp_inspector import OTLPInspector


def main():

    consumer = KafkaConsumer(
        Settings.KAFKA_TOPIC,
        bootstrap_servers=Settings.KAFKA_BOOTSTRAP_SERVERS
    )

    deserializer = OTLPDeserializer()

    inspector = OTLPInspector()

    for message in consumer:

        request = deserializer.deserialize(message.value)

        inspector.inspect(request)


if __name__ == "__main__":
    main()