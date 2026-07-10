from opensearchpy import OpenSearch

from config.settings import Settings


class OpenSearchClient:

    def __init__(self):

        self.client = OpenSearch(

            hosts=[

                {

                    "host": Settings.OPENSEARCH_HOST,

                    "port": Settings.OPENSEARCH_PORT

                }

            ],

            use_ssl=False,

            verify_certs=False

        )

    def get_client(self):

        return self.client