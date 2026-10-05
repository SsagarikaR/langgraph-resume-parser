# RAG (Retrieval-Augmented Generation) Interview Guidelines

Interviewers should focus on:
- The pipeline end to end: loading, chunking, embedding, indexing, retrieval, generation.
- Chunking trade-offs: chunk size, overlap, and structure-aware splitting.
- Retrieval quality: vector vs keyword (hybrid) search, re-ranking, metadata filters.
- Evaluation: measuring retrieval recall and answer faithfulness, not just "it looks right".
- Reducing hallucinations: grounding prompts, citations, and "I don't know" behaviour.

Red flags: cannot explain why retrieval fails; has never evaluated a RAG system.
