from persistence.opensearch.trace_mapper import (
    OpenSearchTraceMapper
)



class OpenSearchTraceRepository:


    INDEX = (
        "causalods-traces"
    )


    def __init__(self, client):

        self.client = client

        self.mapper = (
            OpenSearchTraceMapper()
        )


    def save(self, trace):

        document = (
            self.mapper.map(trace)
        )


        self.client.index(

            index=self.INDEX,

            id=trace["trace_id"],

            body=document

        )


        return trace



    def find_by_id(self, trace_id):

        response = self.client.get(

            index=self.INDEX,

            id=trace_id

        )


        return response["_source"]