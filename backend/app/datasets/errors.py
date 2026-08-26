from backend.app.core.errors import DomainError


class DatasetNotFoundError(DomainError):
    def __init__(self, message: str) -> None:
        super().__init__("DATASET_RECORD_NOT_FOUND", message, status_code=404)


class DatasetExtractionError(DomainError):
    def __init__(self, code: str, message: str, *, status_code: int = 422) -> None:
        super().__init__(code, message, status_code=status_code)


class InvalidDatasetOutputError(DatasetExtractionError):
    def __init__(self, message: str) -> None:
        super().__init__("INVALID_DATASET_STRUCTURED_OUTPUT", message)


class InvalidDatasetEvidenceError(DatasetExtractionError):
    def __init__(self, message: str) -> None:
        super().__init__("INVALID_DATASET_EVIDENCE", message)


class DatasetReviewError(DatasetExtractionError):
    def __init__(self, message: str) -> None:
        super().__init__("INVALID_DATASET_REVIEW", message, status_code=409)
