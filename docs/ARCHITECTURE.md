# Architecture and design decisions

This document explains how Schema Mapper is built, why each part is shaped the way it is, and what it trades away.

## 1. Generate code once, run it on every row

The obvious design sends each row to an LLM. That costs one model call per row, and each row can be interpreted differently.

Schema Mapper does the opposite. The model sees a few sample rows and the target schema, and writes one transform(row) function. That function runs locally on every row.

| | Row-by-row LLM | Generated transform (this project) |
|---|---|---|
| Model calls | One per row | A few per file, regardless of size |
| Consistency | Rows can be interpreted differently | Every row goes through the same logic |
| Debuggability | Opaque | The generated code is shown and readable |
| Failure mode | Silent, row-level | Explicit row-level errors with reasons |

Tradeoff: the model can only handle patterns visible in the sample rows or stated in the schema. A pattern that appears only deep in a file will break the transform, and validation reports it.

## 2. The agent loop

The retry loop is a LangGraph state machine:

```
generate -> execute -> validate -> (retry -> generate) or finish
```

- generate: calls the model with the schema, sample rows, and any feedback from the last attempt.
- execute: runs the transform in the sandbox, in chunks of 500 rows.
- validate: checks each output row against the JSON Schema.
- finish: returns records and errors after at most three attempts.

Two failure branches are handled explicitly and unit-tested: the model returns text that is not valid JSON, and the generated code fails with a syntax or runtime error. Each sends a specific error message back to the model.

Why a graph and not a plain loop: the retry logic has branches and shared state. An explicit graph makes each path visible and testable, and makes it easy to add a human approval node later.

Honest note on value: on the nine evaluation cases almost every run succeeds on the first attempt. The retry loop fired in 1 of 210 agent runs and recovered. It is a safety net, not the source of accuracy.

## 3. Safety: sandbox and redaction

Sandbox. Generated code runs in a local subprocess with a 5-second timeout, a 512 MB memory limit, a CPU limit, a cleared environment (no API keys reach the generated code), and network isolation with unshare -n when the platform allows it.

This is not a security boundary. Code that controls its own execution could still escape a local subprocess. Production use needs a container or an isolated service such as E2B.

Redaction. Emails, phone numbers, and API-key-shaped strings are replaced with placeholders before they reach a prompt, a log, or the UI.

Tradeoff: regex redaction catches common formats, not every secret. It reduces exposure; it does not guarantee none.

## 4. Grounding: checking values against the source

Model output can look correct and still be invented. Each output value is classified:

- copied: appears in the source exactly.
- normalized: matches the source after number or whitespace normalization, or is a recognized conversion (for example 2.5 kg becomes 2500 g).
- unsupported: not found in the source. Flagged for review.
- null: empty.

What this proves: every value traces back to some value in the source.

What it does not prove: which column the value came from. A coincidental match is possible, and the check is lenient on purpose so correct conversions are not rejected. Column-level provenance is on the roadmap.

Refusal over invention: if a target field has no source column, the transform raises an error. It never fills in 0 or an empty string, because a silent default looks like valid data downstream.

## 5. The schema is the contract

| Mode | When to use | Tradeoff |
|---|---|---|
| C. Paste the schema | The client already has a target format (the normal case) | The user must write it |
| A. Infer from a clean example | The client can provide one correct output file | Inferred types can be too narrow; we fixed integer inference by inferring number |
| B. AI draft | No example exists | Can guess wrong types; labeled experimental, must be reviewed |

We tested the AI draft against nine hand-written schemas. Its type accuracy was low, mostly because the client's field names cannot be guessed from the data. That is why mode C is the default.

## 6. Knowledge reuse

Approved mappings are saved and retrieved for similar files. Similarity uses TF-IDF on column names and sample values, and the best matches are added to the prompt as examples.

Why TF-IDF and not embeddings: it needs no extra model calls, no vector database, and no quota. For a few hundred approved mappings, keyword similarity is adequate.

Tradeoff: it matches surface features, so two files with the same columns but different meanings can retrieve the same example. The approval step is where a human decides what gets saved.

## 7. Integration with downstream systems

Records can be pushed to a CRM-style endpoint with:

- an idempotency key per record (a hash of its content), so a retry cannot create a duplicate,
- retries on 429 and 5xx responses, with exponential backoff capped at 30 seconds, honoring Retry-After when present,
- a dead-letter list for records that still fail after the retry limit, so nothing is lost silently.

The default target is a mock that simulates 429 responses, so the retry path is exercised without a live system. Setting a real endpoint switches it to an HTTP sender.

Tradeoff: retries run synchronously in the UI session. Production would use a queue and a worker so a slow endpoint does not block the user.

## 8. Scale: batches and chunking

- Batches: many files, one schema. Each file gets its own status row, and one failure does not stop the others.
- Chunking: execution runs in chunks of 500 rows, with a timeout per chunk.

Current limit: batches run one file after another. In a measured run the second file waited on the first, so the total time was the sum. The next step is a worker pool with concurrency.

Other limit: very large files need a background job rather than a browser session. The current design suits files of thousands of rows, not millions.

## 9. Cost control

- A daily cap on model calls, counted by the app and shown in the sidebar.
- The counter is stored in a local file, so it resets when the hosting platform restarts the app.

Honest limit: the cap is a guard, not a hard budget. The reliable backstop is a billing budget with alerts. The public demo currently runs on the free tier with no billing account, so Google returns rate-limit errors instead of charging.

## 10. Evaluation

Nine hand-labeled cases, each with an input, a schema, and expected output. Each runs in two modes: a single attempt (baseline) and up to three attempts (agent).

Measured:
- 9/9 in both modes, one run of each case.
- 29/30 single-attempt and 30/30 with retries, across six cases with five runs each.
- 30/30 on a strict-rules case across 30 runs.

What the evaluation taught us:
- The first version checked only row counts, so it passed wrong outputs. Value-level checks were added after this was found.
- A scoring rule that penalized renames no tool could know was removed. The fair test of schema suggestion is against the client's real field names, which is why mode C is the default.
- A regression appeared when the model call moved to LangChain: a date conversion stopped being applied. Stating the format in the schema field and the prompt fixed it, and the case has passed in every later run.

## 11. Known gaps and next steps

| Gap | Why it matters | Next step |
|---|---|---|
| Local sandbox | Not safe for untrusted code | Container or E2B |
| No column-level provenance | A coincidental match is possible | Track the source column per value |
| Sequential batches | Slow for many files | Worker pool with concurrency |
| Keyword retrieval | Will not scale to thousands of mappings | Embeddings and a vector store |
| Mock CRM by default | No real integration tested | Staging endpoint and contract tests |
| Quota counter resets on restart | Not a hard limit | Persistent store, plus a billing budget |
| No human approval on output | Records go straight to the CRM | Per-record approve step |

## 12. How the pieces fit

```
Streamlit UI (app.py)
  |
  +-- schema: paste | infer (infer_types.py) | AI draft (suggest.py)
  |
  +-- SchemaMapperAgent (agent.py)   <- LangGraph loop
  |     +-- generate -> LangChain structured output -> Gemini
  |     +-- execute  -> sandbox.py (subprocess, limits)
  |     +-- validate -> JSON Schema
  |
  +-- provenance.py   grounding check against the source
  +-- rag.py          approved mappings, TF-IDF retrieval
  +-- crm.py          idempotent push, retries, dead letters
  +-- limits.py       daily model-call cap
```

Input and output are plain CSV and JSON. The model is used only to write the transform. Everything after that is deterministic code that can be tested without a model.
