class IndexManager:


    INDEX_NAME = (
        "causalods-incidents"
    )



    def __init__(
        self,
        client
    ):

        self.client = client



    def create_index(self):


        if self.client.indices.exists(

            self.INDEX_NAME

        ):

            return



        mapping = {


            "mappings":
            {

                "properties":
                {


                    "incident_id":
                    {
                        "type":"keyword"
                    },


                    "timestamp":
                    {
                        "type":"date"
                    },


                    "root_cause":
                    {

                        "properties":
                        {

                            "service":
                            {
                                "type":"keyword"
                            },


                            "confidence":
                            {
                                "type":"float"
                            }

                        }

                    },


                    "evidence":
                    {
                        "type":"object"
                    },


                    "status":
                    {
                        "type":"keyword"
                    }

                }

            }

        }


        self.client.indices.create(

            index=self.INDEX_NAME,

            body=mapping

        )