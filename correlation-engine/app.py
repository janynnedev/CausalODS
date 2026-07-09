from consumer.kafka_consumer import TraceConsumer


def main():

    print()

    print("=" * 70)
    print("Correlation Engine")
    print("=" * 70)

    consumer = TraceConsumer()

    consumer.consume()


if __name__ == "__main__":
    main()