class GenerationError(RuntimeError):
    code = "GENERATION_FAILED"


class InvalidStructuredOutputError(GenerationError):
    code = "INVALID_STRUCTURED_OUTPUT"


class GenerationProviderError(GenerationError):
    code = "MODEL_PROVIDER_FAILURE"


class GenerationTimeoutError(GenerationProviderError):
    code = "TIMEOUT"


class QueryRunNotFoundError(GenerationError):
    code = "QUERY_RUN_NOT_FOUND"


class QueryRunStateError(GenerationError):
    code = "QUERY_RUN_INVALID_STATE"
