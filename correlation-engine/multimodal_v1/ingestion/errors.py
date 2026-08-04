class IngestionError(ValueError):
    """Erro esperado de decode, schema ou normalização."""


class UnsupportedSignalError(IngestionError):
    pass


class UnknownBusinessSchemaError(IngestionError):
    pass


class SemanticInputError(IngestionError):
    pass


class IngestionInvariantError(RuntimeError):
    """Falha interna: nunca deve ser classificada como entrada rejeitada."""
