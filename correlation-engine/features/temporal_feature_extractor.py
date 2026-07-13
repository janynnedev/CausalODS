class TemporalFeatureExtractor:

    def extract(self, temporal_result):

        result = {}

        ordered = temporal_result.get(
            "ordered_failures",
            []
        )

        if not ordered:

            return result

        first = ordered[0]

        initial_time = first["timestamp"]

        for index, failure in enumerate(ordered):

            result[
                failure["service"]
            ] = {

                "failure_order":

                    index,

                "is_first_failure":

                    index == 0,

                "failure_lead_time_ms":

                    (
                        failure["timestamp"]
                        -
                        initial_time
                    ).total_seconds() * 1000

            }

        return result