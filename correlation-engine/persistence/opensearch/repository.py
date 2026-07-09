from opensearch.mapper import (
    OpenSearchIncidentMapper
)



class OpenSearchIncidentRepository:



    INDEX = (
        "causalods-incidents"
    )



    def __init__(

        self,

        client

    ):

        self.client = client

        self.mapper = (
            OpenSearchIncidentMapper()
        )



    def save(

        self,

        incident

    ):


        document = self.mapper.map(

            incident

        )


        self.client.index(

            index=self.INDEX,

            id=incident.incident_id,

            body=document

        )


        return incident



    def find_by_id(

        self,

        incident_id

    ):


        response = self.client.get(

            index=self.INDEX,

            id=incident_id

        )


        return response["_source"]



    def find_all(

        self

    ):


        response = self.client.search(

            index=self.INDEX,

            body={

                "query":
                {

                    "match_all":{}

                }

            }

        )


        return [

            item["_source"]

            for item

            in response["hits"]["hits"]

        ]