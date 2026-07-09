from opensearchpy import OpenSearch



class OpenSearchClient:


    def __init__(self):

        self.client = OpenSearch(

            hosts=[
                {
                    "host":"localhost",
                    "port":9200
                }
            ],

            use_ssl=False,

            verify_certs=False

        )



    def get_client(self):

        return self.client