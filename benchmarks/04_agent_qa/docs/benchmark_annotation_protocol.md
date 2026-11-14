# Benchmark Annotation Protocol (PlantTP Agent evaluation)

**Version 1.0 (public)**

## 1. Question construction (pool-first)

1. Topic seeds (transporter family × process, optionally × stress) are screened against the indexed corpus with production-isomorphic retrieval (merged BM25 and vector candidates scored by the reranker). A seed proceeds only if the candidate gold pool contains at least three distinct publications and no single publication dominates the pool.
2. Questions are written from the accepted pools with corpus-anchored wording ("According to the indexed literature …") and a synthesis-forcing structure. Question text never displays retrieval scores.
3. Final question wording is round-verified against the pool; questions whose wording was revised after verification are permanently tagged as development-pilot items and never enter the holdout track.

## 2. System execution

Per-question caches are cleared before each run; the configuration (models, chunking, retrieval and reranking settings, prompts, generation parameters) is snapshotted into a manifest before execution; questions are executed serially against the deployed system. Raw per-question outputs (answer, 18-chunk evidence window with per-chunk document identifiers and section labels) are retained.

## 3. Evidence packets

For each question an evaluation packet is assembled with three tracks: **E** — the retrieved evidence window; **G** — candidate gold passages (top corpus passages by source-word overlap); **D** — database records for the entities involved (including failed lookups). Database-grounded claims are always assessed against track D.

## 4. Document-level adjudication

Retrieval-side relevance is adjudicated per document with a two-phase review; the archived judgments are binary (relevant / irrelevant), where a document is relevant if it contributed at least one gold chunk to the frozen qrels: Phase A re-labels all candidates blind to the initial labels (from a sanitized input containing question and candidate titles/snippets only); Phase B compares blind labels with the initial labels, applies written consistency principles (source publication always relevant; cross-species functional analogy is partial relevance; review papers relevant under definitional questions), and records every accepted correction with rationale. Alias document identifiers are merged only after independent verification.

## 5. Claim-level annotation and adjudication

Answers are decomposed into atomic claims under the verbatim contract (claims are continuous verbatim fragments of the answer; discourse markers may be dropped; no rewording). Deliverables are English-only; each batch passes mechanical gates before acceptance (no non-English characters; claim text contained in the system answer; reference answers composed of verbatim source-passage text). Any item that cannot be resolved is dispatched for re-annotation with the specific failure recorded — no item is silently dropped.

Each claim receives one binary evidence-support label, assessed against the literature passages and structured database records available to the system; citation-bearing claims are assessed against their corresponding cited evidence:

1. **Supported** — the cited evidence establishes the claim as stated, including its stated conditions.
2. **Not supported** — the cited evidence fails to establish the claim as stated (no support for an essential component, or direct refutation).

Binding rules:

- **R1 (Database-grounded statements).** Claims marked `[DATABASE-GROUNDED]` are assessed against the database record track only; absence of literature support does not by itself justify a not-supported label.
- **R2 (Negative existential statements).** A claim asserting that the provided materials contain no X is judged against the complete evidence track supplied to the generator; it is not labeled not supported merely because X might exist elsewhere.
- **R3 (Evidence completeness premise).** Judgments of absence are made against the entire evidence window actually supplied to the generator.
- **Uncertainty wording.** Hedged source statements (suggest / presumably / putative / may) do not establish a categorical claim as stated.

Review procedure: initial labeling under the rules above, then expert review of every claim (reviewer verdict confirmed / not confirmed / uncertain; uncertain items referred to a third adjudicator; corrected labels selected from the same two categories; claim texts, identifiers and evidence rows are never edited). Claims created or restructured during review (splits/additions) pass a dedicated confirmation wave, so every final label has passed two-stage review. Corrections are recorded per claim with rationale in the final adjudication record (`data/claims/claim_adjudication_record.xlsx`), which retains each claim's initial and final label.

## 6. Freeze

The finalized question set, relevance judgments, and claim labels are frozen before any performance metric is computed and are not used for system tuning. Each frozen batch carries a manifest (script hashes, configuration, input hashes, timestamps). Metrics are recomputed end-to-end from the frozen artifacts by the scoring scripts in `code/final_scoring/`.
