class LabelGenerator:



    def generate(
        self,
        feature,
        scenario
    ):


        service = (
            feature["service_name"]
        )


        if scenario=="database_latency":

            return int(
                service=="database"
            )


        if scenario=="payment_failure":

            return int(
                service=="payment"
            )


        return 0