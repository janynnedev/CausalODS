from config.settings import Settings

from persistence.opensearch.client import OpenSearchClient


class Dependencies:

    settings = Settings()

    opensearch = OpenSearchClient().get_client()