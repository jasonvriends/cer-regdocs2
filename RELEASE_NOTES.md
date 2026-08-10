# REGDOCS Atlas 0.1.0

Version **0.1.0** is the first release described as a working REGDOCS data pipeline rather than an experiment.

The goal of this release is simple: make the project easier to understand and safer to operate without changing the basic five-stage design.

---

## What REGDOCS Atlas does

REGDOCS Atlas provides one command-line program:

```bash
python pipeline.py ...
```

That command controls the complete workflow:

```text
1. Scout     find CER REGDOCS records and preserve source evidence
2. Download  download source files and record their identities
3. Analyze   process documents with Azure Content Understanding or Docling
4. Normalize convert analyzer output into consistent JSONL
5. Index     publish search-ready chunks to Azure AI Search
```

The project stores operational state in SQLite and keeps important source and analysis artifacts on disk so work can be checked, resumed, and rebuilt.

---

## Documentation rewritten for new users

The main documentation has been reorganized around a beginner-first approach.

### `README.md`

The README now explains:

- what the project is for;
- what each stage does;
- which stages use the network;
- which operations can create Azure charges;
- how to install the project;
- the normal Scout → Download → Analyze → Normalize → Index workflow;
- where important files are stored;
- how recovery works;
- how logs and locks protect the workspace; and
- common technical terms in plain language.

### `SYNTAX.md`

The command guide remains the detailed operator reference, but commands are now grouped around tasks a new user is likely to perform. Safety notes appear beside commands that write data, contact external services, replace canonical output, or can create Azure charges.

---

## Safer public command line

The public CLI is action-oriented.

A stage name by itself does not start work:

```bash
python pipeline.py scout
python pipeline.py download
python pipeline.py analyze azure
python pipeline.py analyze docling
python pipeline.py normalize
python pipeline.py index
```

Real work requires an explicit action such as:

```bash
python pipeline.py scout run ...
python pipeline.py download run
python pipeline.py analyze azure run ...
python pipeline.py analyze docling run ...
python pipeline.py normalize run ...
python pipeline.py index publish
```

Planning and status commands are separate so an operator can inspect work before starting it.

Azure Content Understanding additionally requires an explicit candidate scope such as `--all`, `--limit N`, or `--document-id ID`.

---

## Stage 1 — Scout

Scout can:

- search REGDOCS by an explicit filing-date range;
- preserve raw HTML evidence;
- save document and snapshot manifests;
- track durable date-range coverage;
- inspect status and schema state;
- audit saved Scout evidence; and
- repair known Folder and Compound Document relationships.

Normal `scout run` and `scout probe` commands require both `--start-date` and `--end-date`.

---

## Stage 2 — Download

Download can:

- preview eligible downloads without contacting REGDOCS;
- download source files;
- validate and hash downloaded files;
- reconcile the SQLite ledger with files already on disk;
- archive replaced versions when configured to do so; and
- write deterministic metadata sidecars.

The default workflow is still:

```bash
python pipeline.py download plan
python pipeline.py download run
```

---

## Stage 3 — Azure Content Understanding

Azure analysis uses a single-threaded supervisor that launches one isolated child process per document.

Important protections include:

- explicit `plan` and `run` actions;
- explicit candidate scope;
- source SHA-256 verification by default;
- reuse of matching local analysis artifacts when possible;
- durable per-document supervisor state; and
- support for large PDFs through Content-Range page processing.

Large PDFs are processed in page ranges that fit the Azure request limit. Valid completed range artifacts can be reused locally.

Automatic application-level Azure resubmission retries remain disabled. A failed document is retried on a later normal pipeline run instead of being repeatedly resubmitted inside the same supervisor run.

---

## Stage 3 — Docling

Docling provides a local document-analysis path.

It uses isolated child processes and supports:

- status inspection;
- bounded runs with `--max-documents`;
- per-document attempt limits;
- quarantine state; and
- retrying quarantined documents when explicitly requested.

Docling does not create Azure Content Understanding charges.

---

## Stage 4 — Normalize

Normalize converts Azure or Docling artifacts into a consistent local corpus containing document, page, chunk, table, and provenance records.

The stage supports:

- explicit provider selection with `--provider azure|docling`;
- planning without replacing canonical output;
- isolated worker processes;
- configurable local concurrency;
- deterministic final document order; and
- recorded selection, worker, merge, and total wall-clock timing.

The final JSONL merge is streamed so complete worker shard files do not need to be loaded into memory at once.

A real Normalize run still replaces the canonical Stage 4 JSONL with the documents selected for that run. Operators should use `plan` or a separate `--output-dir` for small tests.

---

## Stage 5 — Azure AI Search

Index can:

- validate normalized chunks locally;
- map chunks into Azure AI Search documents;
- publish in bounded batches;
- optionally recreate an index; and
- query an existing index from the same root CLI.

`index plan` does not contact Azure AI Search. `index publish` and `index query` do.

---

## Durable recovery design

Stages 1-3 are the main durable recovery boundary because they contain source evidence, downloaded source bytes, or expensive analysis results.

Important recovery material includes:

```text
workspace/1_scout/
workspace/2_download/
workspace/3_analyze/
```

The project can use those artifacts to build a new SQLite ledger beside the active database and compare Stage 1-3 identities before the operator decides whether to use the rebuilt copy.

A flat rebuild is also available:

```bash
python pipeline.py rebuild create --flat
```

It first performs the manifest-backed Stage 1-3 reconstruction, then removes historical run/error/recovery bookkeeping from the new database. It does not overwrite `database/regdocs.db` and does not contact REGDOCS or Azure.

Stage 4 is intentionally regenerated locally from Stage 3 artifacts. Stage 5 is republished from Stage 4.

---

## Logging and process protection

State-changing root commands share a global pipeline lock. Stage-specific locks remain additional protection.

The current state-changing run is written to:

```text
workspace/pipeline.log
```

Before the next state-changing run starts, the previous log is compressed under `workspace/logs/`. Archives are bounded by count and age so log history does not grow without limit.

The root console keeps stage output consistent while preserving detailed child diagnostics in the pipeline log.

---

## Versioning

The project version is stored in one place:

```text
VERSION
```

The public command reads that file:

```bash
python pipeline.py version
```

For this release it returns:

```text
0.1.0
```

Analyzer identities, parser identities, API versions, and other compatibility values stored in durable artifacts are separate from the project release number. They continue to identify whether saved artifacts are compatible with the code that produced or consumes them.
