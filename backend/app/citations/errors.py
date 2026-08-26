class CitationValidationError(RuntimeError):
    code = "INVALID_CITATION"


class InventedCitationError(CitationValidationError):
    code = "INVALID_CITATION"


class CitationResolutionError(CitationValidationError):
    code = "INVALID_CITATION"
