class TemporalFeatureExtractor:



    def extract(
        self,
        temporal_result
    ):


        result = {}


        first_failure = (
            temporal_result
            .get(
                "first_failure"
            )
        )


        if not first_failure:

            return result



        service = (
            first_failure["service"]
        )


        result[service] = {


            "is_first_failure":1,


            "failure_lead_time_ms":
                0

        }


        return result