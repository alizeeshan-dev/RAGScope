# Chunk 1 indexing and scoring contract

Every search index is keyed by corpus-version ID, index type, and a canonical
configuration hash. Every retrieval query joins back to `chunks` and repeats the
corpus-version predicate as defense in depth. An integrity check rejects missing,
foreign, malformed, or failed entries before an index is treated as usable.
The canonical index configuration includes the corpus version's active chunker
snapshot. Alternative chunk outputs can coexist for inspection, but they cannot
leak into that index or its readiness counts.

## Lexical scoring

RAGScope does **not** claim BM25 scoring in Chunk 1.

- PostgreSQL uses `to_tsvector('english', text)`,
  `plainto_tsquery('english', query)`, and `ts_rank_cd`. This is PostgreSQL's
  cover-density full-text rank, not BM25.
- SQLite and other portable development databases use deterministic TF-IDF cosine.
  Text is Unicode-word-tokenized and case-folded. Term frequency is `1 + ln(count)`;
  inverse document frequency is `ln((N + 1) / (df + 1)) + 1`. Query and document
  vectors are cosine-normalized. Metadata/document filters are applied before the
  portable corpus statistics are calculated, zero-score chunks are omitted, and
  exact score ties are broken by stable chunk ID.

The retrieval result identifies the method as `postgresql_fts_ts_rank_cd` or
`lexical_tfidf_cosine`, so later experiments cannot silently confuse the two. A
true BM25 engine can replace either implementation behind the same result contract.

## Dense scoring

The offline fake provider is a signed SHA-256 feature-hashed bag-of-words model. It
uses the same Unicode/case-folded tokens, sublinear term frequency, and L2
normalization. It is deterministic and useful for integration tests, but it is not
a semantic embedding model.

Both backends rank by cosine similarity. PostgreSQL stores embeddings in a native
`pgvector` column and uses the `<=>` cosine-distance operator, returning
`1 - distance` as similarity. SQLite stores portable JSON and computes the same
cosine formula in process. Dense records persist provider, model, dimension,
preprocessing version, and similarity method; query-time providers must match all
of them.

PostgreSQL search is currently exact. Because Chunk 1 permits providers with
different dimensions, it does not create a single fixed-dimension HNSW/IVFFlat
index. A later migration may create per-configuration typed vector tables or
dimension-specific ANN indexes without changing the service contract.

## Restart and readiness behavior

Build dispatches use a stable database job idempotency key. Index entries are
unique by `(index_id, chunk_id)` and retries reuse valid entries, so interrupted or
duplicate builds are safely restartable. Both lexical and dense integrity reports
must pass before the corpus version becomes `ready`. Any partial dense failure
leaves the dense index and corpus version failed; it never promotes a partial build.
