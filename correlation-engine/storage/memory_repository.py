from storage.repository import IncidentRepository



class MemoryIncidentRepository(
    IncidentRepository
):


    def __init__(self):

        self.storage = {}



    def save(
        self,
        incident
    ):

        self.storage[
            incident.incident_id
        ] = incident


        return incident



    def find_by_id(
        self,
        incident_id
    ):


        return self.storage.get(
            incident_id
        )



    def find_all(
        self
    ):


        return list(
            self.storage.values()
        )