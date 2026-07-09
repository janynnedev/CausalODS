from models.rca_feature import RCAFeature


feature = RCAFeature(

    service_name="payment-service",

    request_count=100,

    error_count=20,

    error_rate=0.2,

    avg_latency_ms=500,

    incoming_dependencies=2,

    outgoing_dependencies=1,

    is_first_failure=1

)


print(
    feature.to_dict()
)