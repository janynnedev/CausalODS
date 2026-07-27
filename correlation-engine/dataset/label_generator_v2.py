from __future__ import annotations


class LabelGeneratorV2:
    """
    Produz um rótulo binário por serviço.

    1 = o serviço é a causa raiz da amostra;
    0 = o serviço não é a causa raiz.
    """

    @staticmethod
    def generate(
        *,
        service_name: str,
        root_cause_service: (
            str | None
        ),
    ) -> int:

        if root_cause_service is None:
            return 0

        return int(
            service_name
            == root_cause_service
        )