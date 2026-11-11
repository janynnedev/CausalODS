"""Safe operational diagnostics; never persist provider responses or secrets."""
from __future__ import annotations

SAFE_CODES = frozenset({
    'invalid_schema', 'empty_response', 'invalid_evidence_reference',
    'missing_evidence_citation', 'LLM_PROVIDER_INVALID_RESPONSE',
    'LLM_PROVIDER_RETURNED_MODEL_MISMATCH', 'LLM_PROVIDER_EMPTY_RESPONSE',
    'LLM_PROVIDER_UNACCEPTABLE_FINISH_REASON:length',
    'LLM_PROVIDER_UNACCEPTABLE_FINISH_REASON:content_filter',
    'LLM_PROVIDER_ERROR:HTTPError', 'LLM_PROVIDER_ERROR:URLError',
    'LLM_PROVIDER_ERROR:TimeoutError', 'LLM_PROVIDER_ERROR:JSONDecodeError',
})


def explanation_error_code(exception: Exception) -> str:
    message = str(exception)
    if message in SAFE_CODES:
        return message
    if type(exception).__name__ in {
        'H3InputTokenBudgetExceeded', 'H3LLMFreezeError', 'RootCauseMismatchError',
        'TimeoutError', 'JSONDecodeError', 'ValueError', 'TypeError',
    }:
        return type(exception).__name__
    return 'EXPLANATION_PROCESSING_ERROR'


class OperationalExplanationError(RuntimeError):
    def __init__(self, code: str):
        self.code = code if code in SAFE_CODES or code in {
            'H3InputTokenBudgetExceeded', 'H3LLMFreezeError', 'RootCauseMismatchError',
            'TimeoutError', 'JSONDecodeError', 'ValueError', 'TypeError',
        } else 'EXPLANATION_PROCESSING_ERROR'
        super().__init__(self.code)
