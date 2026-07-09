import random



class FeatureGenerator:



    def generate(
        self,
        scenario,
        service
    ):


        base = {


            "service_name":
                service,


            "request_count":
                random.randint(
                    50,
                    500
                ),


            "error_count":
                0,


            "error_rate":
                0.0,


            "avg_latency_ms":
                random.randint(
                    20,
                    100
                ),


            "incoming_dependencies":
                random.randint(
                    0,
                    3
                ),


            "outgoing_dependencies":
                random.randint(
                    0,
                    3
                ),


            "is_first_failure":
                0,


            "downstream_impact":
                0.0,


            "propagation_depth":
                0,


            "latency_zscore":
                0.0,


            "anomaly_score":
                0.0

        }



        if scenario == "database_latency" and service=="database":


            base.update({

                "error_count":50,

                "error_rate":0.8,

                "avg_latency_ms":1500,

                "is_first_failure":1,

                "latency_zscore":8.5,

                "anomaly_score":0.95,

                "downstream_impact":0.9,

                "propagation_depth":3

            })



        if scenario == "payment_failure" and service=="payment":


            base.update({

                "error_count":80,

                "error_rate":0.9,

                "is_first_failure":1,

                "anomaly_score":0.98

            })


        return base