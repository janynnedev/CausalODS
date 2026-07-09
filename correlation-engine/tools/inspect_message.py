from kafka import KafkaConsumer


consumer = KafkaConsumer(
    "traces",
    bootstrap_servers="kafka:9092"
)

msg = next(consumer)

print(type(msg.value))

print(len(msg.value))

print(msg.value[:80])