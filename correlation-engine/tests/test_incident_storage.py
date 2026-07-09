from storage.memory_repository import (
    MemoryIncidentRepository
)


from storage.incident_service import (
    IncidentService
)


from storage.document_mapper import (
    IncidentDocumentMapper
)



ranking = {

    "service":
        "database",


    "confidence":
        0.903,


    "evidence":
    {

        "xgboost":0.92,

        "shap":0.8,

        "temporal":1,

        "graph":0.9

    }

}



repository = (
    MemoryIncidentRepository()
)



service = IncidentService(

    repository

)



incident = service.create_incident(

    ranking

)



print(
    incident.to_dict()
)



mapper = IncidentDocumentMapper()


print(
    mapper.map(
        incident
    )
)