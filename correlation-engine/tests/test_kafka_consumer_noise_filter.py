from types import SimpleNamespace

import pytest

from consumer.kafka_consumer import (
    is_noise_span,
)


@pytest.mark.parametrize(
    "operation_name",
    [
        "GET /actuator/prometheus",
        "GET /actuator/health",
    ],
)
def test_noise_operations_are_ignored(
    operation_name,
):

    span = SimpleNamespace(
        operation_name=operation_name
    )

    assert is_noise_span(span) is True


@pytest.mark.parametrize(
    "operation_name",
    [
        "POST /orders",
        "POST /payments",
        "POST",
        "GET /orders",
    ],
)
def test_business_operations_are_not_ignored(
    operation_name,
):

    span = SimpleNamespace(
        operation_name=operation_name
    )

    assert is_noise_span(span) is False


def test_missing_operation_name_is_not_noise():

    span = SimpleNamespace()

    assert is_noise_span(span) is False


def test_none_operation_name_is_not_noise():

    span = SimpleNamespace(
        operation_name=None
    )

    assert is_noise_span(span) is False