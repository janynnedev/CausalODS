from storage.incident_service import (
    IncidentService
)


from persistence.opensearch.client import (
    OpenSearchClient
)


from persistence.opensearch.index_manager import (
    IndexManager
)


from persistence.opensearch.repository import (
    OpenSearchIncidentRepository
)



client = (

    OpenSearchClient()

    .get_client()

)



index = IndexManager(

    client

)


index.create_index()



repository = (

    OpenSearchIncidentRepository(

        client

    )

)



service = IncidentService(

    repository

)



ranking = {


"service":"database",


"confidence":0.903,


"evidence":

{

"xgboost":0.92,

"shap":0.8,

"temporal":1,

"graph":0.9

}

}



incident = (

    service.create_incident(

        ranking

    )

)



print(

    incident.to_dict()

)



print(

    repository.find_all()

)