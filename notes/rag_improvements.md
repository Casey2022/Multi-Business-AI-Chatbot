# RAG Quality Improvements — Future Reference

Captured during Stage 6 (RAG implementation). The current bot uses basic
embedding-based retrieval with paragraph-aware chunking, distance-threshold
filtering, and document-friendly headings. That's plenty for the current
scale, but here's what to reach for if quality starts being a problem.

## The core problem to remember

**Query-document asymmetry**: customers phrase questions differently than
documents phrase answers. Negation ("what don't you do"), jargon vs.
everyday language, and short/vague queries all create gaps that pure
embedding-based retrieval struggles with.

The fundamental constraint: embeddings produce one fixed vector per chunk
and one per query, completely independently. So matching is purely
vector-distance — no awareness of polarity, no question/answer modeling.

## When to upgrade — driven by real failure cases, not theory

In order, from "easiest, biggest payoff" to "heaviest artillery":

### 1. Tighten the documents themselves
- Put query-likely keywords in headings, not just bodies
- Rewrite negation-heavy sections to embed the topic in the heading
  (we did this with "Septic Tanks, Well Drilling, and Commercial Work
  (Not Offered)")
- Auto-generate metadata per chunk via an LLM call during ingestion:
  "questions this chunk answers", "topics covered". Store/embed those
  alongside the source text. Called "chunk enrichment."

### 2. Hybrid search (BM25 + embeddings)
- Combine dense retrieval (embeddings) with sparse retrieval (keyword
  matching via BM25). They fail in different directions, so together
  they cover each other's weaknesses.
- Almost every serious production RAG system uses this.
- ChromaDB doesn't ship with hybrid by default — would need `rank_bm25`
  or a switch to Weaviate/Qdrant.

### 3. Add a reranker
- A reranker reads (query, chunk) pairs *together* and scores relevance
  with much more nuance than independent vector comparison.
- Workflow: vector search grabs top 50 candidates → reranker scores
  them → return actual top 5.
- Cohere has a popular reranker API; `bge-reranker` is a solid
  open-source option.
- Catches things vector search blurs, like negation polarity.

### 4. Query rewriting / HyDE
- Transform the query before searching: paraphrase via LLM, generate
  multiple variants, rewrite questions into statements, etc.
- HyDE = "Hypothetical Document Embeddings": ask an LLM to generate a
  hypothetical answer to the query, embed *that*, and search with it.
  Works because answers tend to share embedding-space with the source
  documents that contain answers.
- Adds latency and cost (extra LLM call per query). Use last.

## The real insight worth remembering

RAG quality is a *system design* problem, not a *model* problem. The
embedder is a fixed component you can't tune. The levers you actually
have are: document phrasing, chunk formation, query transformation,
result combination, and result filtering. Real-world "RAG engineering"
is mostly craft work on those levers, not new models.

## Trigger for revisiting this
Come back to this list when:
- Real customers complain about specific patterns of bad answers
- Retrieval misses things you can see in the documents
- A specific failure mode (like negation) shows up repeatedly in logs

Don't preemptively add complexity. Each technique above adds latency,
cost, or maintenance burden. Measure the failure first.