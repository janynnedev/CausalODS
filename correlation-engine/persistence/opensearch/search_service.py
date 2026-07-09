class IncidentSearchService:



    def __init__(

        self,

        repository

    ):

        self.repository = repository



    def all_incidents(self):


        return (

            self.repository.find_all()

        )