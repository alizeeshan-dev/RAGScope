import pytest
from backend.app.core.errors import DomainError
from backend.app.pipelines.schemas import QueryProcessingConfiguration
from backend.app.query_processing import QueryProcessor


def test_query_processing_preserves_original_and_normalizes_unicode_whitespace() -> None:
    processor = QueryProcessor(
        QueryProcessingConfiguration(
            classification_enabled=True,
            rewriting_enabled=True,
            rewriting_strategy="deterministic-keywords",
        )
    )
    result = processor.process("  Compare   DATASET-A\u00a0vs Dataset-B in 2024  ")
    assert result.original_query.startswith("  Compare")
    assert result.normalized_query == "Compare DATASET-A vs Dataset-B in 2024"
    assert result.rewritten_query == "compare dataset-a vs dataset-b 2024"
    assert result.classification["category"] == "comparison"
    assert result.extracted_metadata["publication_years"] == [2024]


def test_classification_can_be_bypassed_and_empty_query_rejected() -> None:
    processor = QueryProcessor(QueryProcessingConfiguration(classification_enabled=False))
    assert processor.process("question").classification["category"] == "bypassed"
    with pytest.raises(DomainError) as raised:
        processor.process(" \n\t ")
    assert raised.value.code == "INVALID_QUERY"
