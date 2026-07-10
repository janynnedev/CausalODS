from kafka import KafkaConsumer

from config.settings import Settings

consumer = KafkaConsumer(
    Settings.KAFKA_TOPIC,
    bootstrap_servers=Settings.KAFKA_BOOTSTRAP_SERVERS
)

msg = next(consumer)

print(type(msg.value))

print(len(msg.value))

print(msg.value[:80])