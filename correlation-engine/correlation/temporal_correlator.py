from datetime import datetime



class TemporalCorrelator:


    def analyze(
        self,
        trace
    ):


        timeline = []


        for span in trace.spans.values():

            timeline.append({

                "service":
                    span.service_name,

                "start":
                    span.start_time,

                "duration":
                    span.duration_ms,

                "error":
                    span.status_code == "ERROR"

            })


        timeline.sort(
            key=lambda x:x["start"]
        )


        first_error = None


        for item in timeline:


            if item["error"]:

                first_error = item

                break



        return {


            "first_failure":
                first_error,


            "timeline":
                timeline

        }