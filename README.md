# Rulemaking Observatory, rebuilt

Phase zero, delivered. This is the provenance spine of the instrument, not yet
the instrument. It collects, extracts, detects coordination, records what it
knows about its own failure modes, and refuses to emit a report that violates the
standing evidence discipline. It does not yet measure sophistication, read
attachments, code commenter type, aggregate coalitions, or touch final rule
preambles, and each of those is a later phase with its own acceptance test.

## What this instrument is for

The primary question is distributional and not forensic. Whether the
distribution of sophistication among comments from unaffiliated individuals
shifted after generative artificial intelligence became widely available, and
whether the distance between that distribution and the distribution for
organisational comments narrowed, holding agency and regulatory question
constant where the design allows.

The forensic question that the first instrument asked, namely what fraction of a
corpus carries stylistic signals associated with machine generated text, is
retained as a secondary descriptive measure and will never again be the
headline. It has a five percent false positive floor by construction, it is
confounded by who happens to be writing, and it is not identified even in
principle, because no measure in that class separates text a model drafted from
text a model polished from text a person wrote carefully in a formal register.

## Commands

Everything runs from a normal terminal. No step needs the interactive Python
prompt.

    python run.py check
    python run.py collect WHD-2026-0001
    python run.py details WHD-2026-0001 --limit 50
    python run.py extract WHD-2026-0001
    python run.py detect  WHD-2026-0001
    python run.py status

Each command prints the next one to run, so the sequence does not have to be
remembered. The key comes from REGS_API_KEY or from --key, and the working
directory from OBSERVATORY_ROOT or from --root, defaulting to a data folder
beside run.py. The four dockets of the current design are known to run.py by
name, so agency and era are never retyped and cannot be mistyped.

Start with a small --limit on the details stage. It is the only expensive stage,
one request per comment, and a small limit confirms the key works in a few
minutes rather than after hours.

## Infrastructure analyses

Three further commands measure what the infrastructure of the record does to
participation, and each prints a generated report that states what its data
cannot show.

    python run.py rhythm WHD-2026-0001
    python run.py audit WHD-2026-0001
    python run.py aggregation WHD-2026-0001

`rhythm` measures the agency's posting rhythm, the receive to post lag and the
batch structure of posting days. Posted dates are the object of study here,
which deliberately inverts the standing guard on posted_date, and the report
says so. `audit` measures, from this instrument's own request log, what
assembling the corpus cost in requests and hours, and extrapolates a full text
audit at the measured rate, reporting a gap where the log carries no timing.
`aggregation` measures how much participation arrives compressed into single
posted records through the declaration by which one record may stand for many
received submissions, decomposing the received against posted gap where an
agency total is on file. A missing declaration is an absent declaration and
never a declaration of one. The optional `--enrich` flag issues one logged
request for the docket detail record and is off by default.

All three run on data the collector already stores, so they can be run today on
whatever has been collected, and rerun as collections grow.

## Verifying the instrument

Everything in Phase zero runs offline. There is no network dependency and no API
key is required to verify the instrument, which is deliberate, because a test
suite that needs a credential is a test suite that does not get run.

    python3 -m pytest tests/ -q
    python3 demo_phase_zero.py

The demonstration builds a temporary database, collects a fixture corpus,
extracts text, detects coordination, prints a generated report, and prints a
provenance trace. Read the output rather than only the exit code.

For live collection, set `REGS_API_KEY` in the environment and pass
`HttpTransport(api_key=...)` to the collector instead of `FixtureTransport`. Set
`OBSERVATORY_ROOT` to the working directory and `OBSERVATORY_ARCHIVE_DB` to the
read only database from the first instrument. No path is hard coded anywhere.

Collection runs in two stages. `collect_docket` retrieves list records through
`/v4/comments` filtered by docket, a path settled empirically from the first
instrument's request log, two hundred and sixty such requests all succeeded and
one of them assembled a complete 48,977 comment corpus across ten cursor
windows. The persisted cursor makes an interrupted collection resume rather
than restart, and the interface's totalElements count lands automatically in
retrieval_status as the posted total. `collect_details` then retrieves the
detail record for each comment, which is where the text lives, at one request
per comment, resumable, and that request budget is the binding constraint on
the whole project.

## What is in Phase zero

The provenance spine, meaning that every claim resolves to a run, then to member
records, then to raw responses, then to the public address that returned them,
verified by `trace_run` and `integrity_check` rather than asserted in prose.

The collector, which stores each record exactly as returned with its retrieval
time and request, and logs every request with its status so that a gap in a
corpus is visible as a gap rather than as an absence. A retrieval failure and a
docket with no comments are different facts and the log separates them.

The campaign detector, with five word shingling and locality sensitive hashing
over MinHash signatures implemented in this repository rather than imported, a
minimum cluster size of three, attachment placeholders separated as a category
rather than counted as short comments, and removal of phrases appearing in the
proposed rule before clustering.

The artifacts registry, seeded with the four failure modes already identified,
each carrying the guard a new measure must satisfy. The corrections log, seeded
with the three superseded claims from the first instrument. Report generation
that restates applicable guards and refuses to produce a report whose statement
of what the data cannot show is empty.

## Phase status

Phase zero, scaffolding and provenance, complete. Phase one, attachment
extraction, not started, and it is the next thing to build because the cleanest
identification available sits on the corpus with the thinnest text. Phase two,
randomised retrieval and completion of the 2022 labor corpus, not started.
Phase three, commenter type coding, blocked pending a decision on rubric
scoring. Phase four, sophistication measures and the distributional test, not
started. Phase five, coalition aggregation, not started. Phase six, preamble
citation extraction and the agency side triage arm, schema present and
unpopulated, scope not yet confirmed. Phase seven, monthly automation, not
started.

## Standing rules encoded in the code

No output labels an individual comment or an individual person. Claims are
corpus level and comparative. Percentages describe retrieved portions and are
never emitted without their denominator. Blank fields are absent declarations
and not proven absences. Timing uses receive date and never posted date, and no
timing claim is made at finer than day level. Corrections are logged rather than
overwritten. Reports are generated by the instrument and released by a human
being.
