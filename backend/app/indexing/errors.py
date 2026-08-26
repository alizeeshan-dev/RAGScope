class IndexingError(RuntimeError):
    code = "INDEX_BUILD_FAILED"


class CorpusVersionNotFound(IndexingError):
    code = "CORPUS_VERSION_NOT_FOUND"


class CorpusVersionImmutable(IndexingError):
    code = "CORPUS_VERSION_IMMUTABLE"


class IndexConfigurationMismatch(IndexingError):
    code = "INDEX_CONFIGURATION_MISMATCH"


class IndexNotReady(IndexingError):
    code = "INDEX_NOT_READY"


class IndexIntegrityError(IndexingError):
    code = "INDEX_INTEGRITY_FAILED"


class EmbeddingProviderFailure(IndexingError):
    code = "EMBEDDING_PROVIDER_FAILURE"
