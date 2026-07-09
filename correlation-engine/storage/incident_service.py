from datetime import datetime

import uuid



from models.incident import Incident



class IncidentService:



    def __init__(
        self,
        repository
    ):

        self.repository = repository



    def create_incident(

        self,

        ranking_item

    ):


        incident = Incident(

            incident_id=
                str(uuid.uuid4()),


            timestamp=
                datetime.utcnow(),


            root_cause_service=
                ranking_item["service"],


            confidence=
                ranking_item["confidence"],


            evidence=
                ranking_item["evidence"]

        )


        return self.repository.save(
            incident
        )