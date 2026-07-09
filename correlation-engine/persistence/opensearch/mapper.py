class OpenSearchIncidentMapper:



    def map(

        self,

        incident

    ):


        return {


            "incident_id":

                incident.incident_id,


            "timestamp":

                incident.timestamp,


            "root_cause":
            {

                "service":

                    incident.root_cause_service,


                "confidence":

                    incident.confidence

            },


            "evidence":

                incident.evidence,


            "status":

                incident.status

        }