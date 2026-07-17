import os
from uuid import uuid4

import pytest


pytestmark = pytest.mark.integration


# A verificação ocorre antes dos imports e da criação
# de qualquer cliente OpenSearch.
if os.getenv(
    "RUN_OPENSEARCH_TESTS",
    "0",
) != "1":

    pytest.skip(
        (
            "Teste de integração com OpenSearch. "
            "Execute com RUN_OPENSEARCH_TESTS=1."
        ),
        allow_module_level=True,
    )


from storage.incident_service import (
    IncidentService,
)

from persistence.opensearch.client import (
    OpenSearchClient,
)

from persistence.opensearch.index_manager import (
    IndexManager,
)

from persistence.opensearch.repository import (
    OpenSearchIncidentRepository,
)


@pytest.fixture(scope="module")
def opensearch_components():
    """
    Cria os componentes somente quando o teste de integração
    estiver explicitamente habilitado.
    """

    client = (
        OpenSearchClient()
        .get_client()
    )

    index_manager = IndexManager(
        client
    )

    index_manager.create_index()

    repository = (
        OpenSearchIncidentRepository(
            client
        )
    )

    incident_service = (
        IncidentService(
            repository
        )
    )

    return (
        client,
        repository,
        incident_service,
    )


def contains_value(
    payload,
    expected_value,
) -> bool:
    """
    Procura recursivamente um valor no documento retornado pelo
    repositório.

    Isso evita acoplar o teste ao formato exato dos hits do
    OpenSearch.
    """

    if isinstance(payload, dict):

        return any(
            contains_value(
                value,
                expected_value,
            )
            for value in payload.values()
        )

    if isinstance(
        payload,
        (list, tuple),
    ):

        return any(
            contains_value(
                value,
                expected_value,
            )
            for value in payload
        )

    return payload == expected_value


def test_creates_and_persists_incident(
    opensearch_components,
):

    client, repository, incident_service = (
        opensearch_components
    )

    # Nome único para distinguir o documento criado
    # nesta execução dos documentos antigos.
    service_name = (
        "database-integration-"
        f"{uuid4().hex[:8]}"
    )

    ranking = {
        "service": service_name,
        "confidence": 0.903,
        "evidence": {
            "xgboost": 0.92,
            "shap": 0.80,
            "temporal": 1.0,
            "graph": 0.90,
        },
    }

    incident = (
        incident_service
        .create_incident(
            ranking
        )
    )

    stored_by_id = (
        repository.find_by_id(
            incident.incident_id
        )
    )

    assert stored_by_id is not None

    assert contains_value(
        stored_by_id,
        service_name,
    )

    assert incident is not None

    incident_payload = (
        incident.to_dict()
    )

    assert isinstance(
        incident_payload,
        dict,
    )

    assert contains_value(
        incident_payload,
        service_name,
    )

    client.indices.refresh(
        index=(
            OpenSearchIncidentRepository
            .INDEX
        )
    )

    stored_documents = (
        repository.find_all()
    )

    assert stored_documents is not None

    assert contains_value(
        stored_documents,
        service_name,
    )