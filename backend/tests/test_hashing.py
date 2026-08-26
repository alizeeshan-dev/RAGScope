from backend.app.corpora.hashing import corpus_content_hash


def test_corpus_hash_is_order_and_uuid_independent() -> None:
    first = corpus_content_hash(
        document_hashes=["b" * 64, "a" * 64],
        parser_configuration={"version": "1", "ocr": False},
        chunker_configuration={"overlap": 20, "size": 200},
        embedding_configuration={"model": "fake", "dimension": 64},
    )
    second = corpus_content_hash(
        document_hashes=["a" * 64, "b" * 64],
        parser_configuration={"ocr": False, "version": "1"},
        chunker_configuration={"size": 200, "overlap": 20},
        embedding_configuration={"dimension": 64, "model": "fake"},
    )
    assert first == second


def test_corpus_hash_changes_for_content_or_material_configuration() -> None:
    common = {
        "parser_configuration": {"version": "1"},
        "chunker_configuration": {"size": 200},
        "embedding_configuration": {"model": "fake"},
    }
    baseline = corpus_content_hash(document_hashes=["a" * 64], **common)  # type: ignore[arg-type]
    changed_document = corpus_content_hash(document_hashes=["b" * 64], **common)  # type: ignore[arg-type]
    changed_config = corpus_content_hash(
        document_hashes=["a" * 64],
        **{**common, "chunker_configuration": {"size": 300}},  # type: ignore[arg-type]
    )
    assert baseline != changed_document
    assert baseline != changed_config
