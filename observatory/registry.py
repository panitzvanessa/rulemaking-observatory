"""
Instrument self knowledge.

Two tables and one tracer. The artifacts registry records the ways this record
manufactures false findings, so that a known failure cannot be rediscovered as a
result. The corrections log records claims that were superseded, so that an
error is visible in the history rather than absorbed into a revision. The
provenance tracer resolves any stored claim back to the public addresses that
produced it.

The registry is seeded with the four failures already identified in the first
instrument. Each carries a guard, which is the rule a new measure must satisfy
before it ships.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Optional

from .core import utc_now

SEED_ARTIFACTS = [
    dict(
        slug="blank_fields_counted_as_values",
        title="Blank fields counted as a value",
        description=(
            "A geographic concentration statistic reported implausible uniformity because "
            "records with an empty state field were counted as sharing a value rather than "
            "as carrying no declaration."
        ),
        affected_measure="any distribution over a declared attribute",
        guard=(
            "Blank fields are absent declarations and not proven absences. Any measure over a "
            "declared attribute must report the count of missing declarations alongside the "
            "distribution, and must exclude them from the denominator unless absence is the "
            "quantity of interest."
        ),
        status="excluded_from_evidence",
    ),
    dict(
        slug="unpopulated_name_fields_read_as_anonymity",
        title="Unpopulated name fields read as commenter anonymity",
        description=(
            "The 2025 endangerment finding docket appeared to show complete commenter "
            "anonymity. The cause was an agency practice of not populating name fields in the "
            "public record, not a choice made by the commenters."
        ),
        affected_measure="anonymity rate, commenter identification",
        guard=(
            "An apparent behavioural uniformity at or near 100 percent within a single docket "
            "is treated as a publication practice until shown otherwise, and must be checked "
            "against at least one other docket from the same agency before use."
        ),
        status="excluded_from_evidence",
    ),
    dict(
        slug="posted_date_reconstructs_agency_batching",
        title="Arrival classes reconstruct agency posting batches",
        description=(
            "Arrival class categories on the 2026 labor docket sorted comments into bursts and "
            "steady streams that reproduced the agency's eight posting batches, median lag "
            "twelve days, rather than anything about submission behaviour."
        ),
        affected_measure="submission timing, arrival class, burst detection",
        guard=(
            "Timing measures use receive date and never posted date. No timing claim may be "
            "made at finer than day level, because the public interface exposes no sub day "
            "timestamp."
        ),
        status="excluded_from_evidence",
    ),
    dict(
        slug="identifier_ordered_retrieval",
        title="Identifier ordered retrieval misidentifies corpus composition",
        description=(
            "Retrieval ordered by identifier rather than randomised led an early partial run to "
            "identify the largest campaign of the 2022 labor docket as a professional "
            "association letter, which the fuller corpus contradicted."
        ),
        affected_measure="every share, every campaign ranking",
        guard=(
            "Shares from identifier ordered retrieval describe the retrieved portion and must "
            "say so in the same sentence. Rankings of campaign size require either census "
            "coverage or a random sample with a fixed seed."
        ),
        status="mitigated",
    ),
    dict(
        slug="archive_cursor_utc_as_eastern",
        title="Archive cursor passed UTC digits into an Eastern time filter",
        description=(
            "The first instrument's collector advanced its lastModifiedDate cursor by stripping "
            "the Z from the UTC timestamp without converting to Eastern time, which the GSA "
            "example says the filter expects. Passing a later than intended boundary risks "
            "silently skipping records at every five thousand record window. No loss was "
            "observed in practice, the largest archive corpus landed on exactly its posted "
            "count across ten windows, but the archive's window boundaries were never "
            "conservative."
        ),
        affected_measure="completeness of archive corpora collected across cursor windows",
        guard=(
            "Cursor boundaries are converted conservatively, five hours behind UTC, so that "
            "windows overlap rather than gap, and conflict resolution discards the repeats. "
            "Any completeness claim about an archive corpus must cite its posted count "
            "comparison rather than assuming the cursor was safe."
        ),
        status="mitigated",
    ),
    dict(
        slug="null_in_unique_constraint",
        title="A UNIQUE constraint spanning a nullable column never fires",
        description=(
            "The texts identity constraint spanned attachment_ref and the rule_texts constraint "
            "spanned fr_document, both nullable. In SQL a NULL never equals a NULL, so neither "
            "constraint could ever be violated, ON CONFLICT DO NOTHING never triggered, and "
            "every repeated extraction silently duplicated the corpus. A re-extraction produced "
            "forty rows for twenty comments, and the detector then counted thirty-two texts "
            "where sixteen existed, clustering each text with its own predecessor."
        ),
        affected_measure="every count, every share, every cluster size",
        guard=(
            "Uniqueness that spans a nullable column is declared as an expression index over "
            "COALESCE of that column, never as an inline UNIQUE. Any module that reads texts "
            "selects one generation per comment, source, and attachment reference, because "
            "extraction history is retained by design and must be selected rather than assumed."
        ),
        status="mitigated",
    ),
    dict(
        slug="counters_report_attempts_not_writes",
        title="Loop counters reported attempted writes as corpus figures",
        description=(
            "Both the collector and the extractor accumulated their reported totals in the "
            "insert loop, so a repeat run reported a full corpus while writing nothing. The same "
            "defect appeared twice, first through the connection's cumulative change counter in "
            "the collector and then through an unconditional increment in the extractor."
        ),
        affected_measure="reported record and text counts on any repeated run",
        guard=(
            "A figure describing a write comes from the cursor's own rowcount. A figure "
            "describing the corpus is read back from the table after the commit. The two are "
            "reported as separate quantities and never conflated."
        ),
        status="mitigated",
    ),
    dict(
        slug="archive_insert_or_replace",
        title="Archive re-collection overwrote first-retrieval provenance",
        description=(
            "The first instrument stored records with INSERT OR REPLACE, so re-running a "
            "collection silently replaced the stored payload, retrieval timestamp, and source "
            "URL of an already held record. The archive's provenance for any record therefore "
            "reflects the most recent retrieval and not the first, and intermediate states are "
            "unrecoverable."
        ),
        affected_measure="archive retrieval timestamps, any claim about when a record was first seen",
        guard=(
            "Inserts resolve conflicts by doing nothing, so the first stored version of a "
            "record is permanent and a changed record surfaces as a hash mismatch rather than "
            "as a silent overwrite."
        ),
        status="mitigated",
    ),
]

SEED_CORRECTIONS = [
    dict(
        slug="whd2022_campaign1_identity",
        subject="WHD-2022-0003 largest campaign",
        superseded_claim=(
            "The dominant campaign is the National Court Reporters Association letter, "
            "299 of the first 500 retrieved texts."
        ),
        corrected_claim=(
            "The largest campaign is a letter opposing worker misclassification and supporting "
            "the proposed rule, 2,165 texts. The court reporters letter is campaign 3, 375 texts."
        ),
        cause="identifier ordered retrieval over a partial corpus",
        source_run="run 11, 2026-07-24",
    ),
    dict(
        slug="whd2026_instacart_classification",
        subject="WHD-2026-0001 platform worker campaign",
        superseded_claim="The campaign is astroturf, corporate sponsored grassroots.",
        corrected_claim=(
            "The campaign is private interest grassroots, sponsor framed. On the field's own "
            "definitions astroturf turns on deception and paid participation, neither of which "
            "is documented here, and the mobilised workers have a genuine material interest."
        ),
        cause="classification error in the direction of suspicion",
        source_run="run 11, 2026-07-24",
    ),
    dict(
        slug="labor_pair_signal_share",
        subject="Labor pair synthetic text comparison",
        superseded_claim="The 2026 labor corpus shows 6.9 percent above the calibration threshold.",
        corrected_claim=(
            "The comparison is 4.8 percent against 2.8 percent across 1,309 texts, and the 2026 "
            "corpus is more personal than its predecessor. No test statistic is recorded for "
            "this comparison in the verified run."
        ),
        cause="superseded by fuller retrieval",
        source_run="2026-07-24 knowledge base",
    ),
    dict(
        slug="api_comment_filter_path",
        subject="Retrieval path against the Regulations.gov v4 interface",
        superseded_claim=(
            "Comments for a docket are retrieved from /comments with filter[docketId], with the "
            "API key supplied as an api_key query parameter."
        ),
        corrected_claim=(
            "The comments endpoint does not filter on a docket. It filters on commentOnId, which "
            "is the objectId of a document, so retrieval runs from /documents filtered by "
            "docketId to obtain each objectId and then to /comments filtered by that objectId. "
            "The key travels in the X-Api-Key header and never in the URL. A query is capped at "
            "twenty pages of two hundred and fifty records, so a docket above five thousand "
            "comments is truncated unless a lastModifiedDate cursor is used, and comment text "
            "lives on the detail record rather than in the listing."
        ),
        cause=(
            "URL constructed from assumption without checking the published interface "
            "documentation"
        ),
        source_run="phase zero, caught before any live collection",
    ),
    dict(
        slug="api_comment_filter_path_reversal",
        subject="Retrieval path against the Regulations.gov v4 interface, second correction",
        superseded_claim=(
            "The comments endpoint does not filter on a docket, so retrieval must run from "
            "/documents to obtain each objectId and then filter comments by commentOnId."
        ),
        corrected_claim=(
            "filter[docketId] on /v4/comments works in practice. The first instrument's "
            "request log shows 260 such requests, all returning status 200, storing 61,555 "
            "records, including the complete posted record of WHD-2022-0003 at 48,977 of "
            "48,977 across ten cursor windows. The docket filter is the primary path and the "
            "documented objectId path is the alternative. The rest of the first correction "
            "stands, the key travels in the X-Api-Key header, a query is capped at five "
            "thousand records without a lastModifiedDate cursor, and comment text lives on "
            "the detail record rather than in the listing, which the archive also confirms, "
            "since list records there carry no comment field at all."
        ),
        cause=(
            "overcorrection, a documented example was read as an exclusive requirement and "
            "not tested against the empirical record that was available"
        ),
        source_run="archive fetch_log, 260 requests, verified 2026-07-25",
    ),
]


def seed(conn: sqlite3.Connection) -> dict:
    """Idempotent. Safe to call on every initialisation."""
    a = c = 0
    for art in SEED_ARTIFACTS:
        cur = conn.execute(
            """INSERT INTO artifacts
               (slug, title, description, affected_measure, guard, detected_utc, status)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(slug) DO NOTHING""",
            (
                art["slug"], art["title"], art["description"],
                art["affected_measure"], art["guard"], utc_now(), art["status"],
            ),
        )
        a += cur.rowcount if cur.rowcount > 0 else 0
    for cor in SEED_CORRECTIONS:
        cur = conn.execute(
            """INSERT INTO corrections
               (slug, logged_utc, subject, superseded_claim, corrected_claim, cause, source_run)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(slug) DO NOTHING""",
            (
                cor["slug"], utc_now(), cor["subject"], cor["superseded_claim"],
                cor["corrected_claim"], cor["cause"], cor["source_run"],
            ),
        )
        c += cur.rowcount if cur.rowcount > 0 else 0
    conn.commit()
    return {"artifacts_added": a, "corrections_added": c}


def guards_for(conn: sqlite3.Connection, measure_keyword: str) -> list[sqlite3.Row]:
    """
    Return the guards a new measure must satisfy. Called by report generation so
    that a report cannot present a measure whose known failure mode is on file
    without restating the guard.
    """
    return conn.execute(
        """SELECT slug, title, guard, status FROM artifacts
           WHERE affected_measure LIKE ? OR affected_measure LIKE '%every%'
           ORDER BY slug""",
        (f"%{measure_keyword}%",),
    ).fetchall()


def log_correction(
    conn: sqlite3.Connection,
    slug: str,
    subject: str,
    superseded_claim: str,
    corrected_claim: str,
    cause: str,
    source_run: Optional[str] = None,
) -> None:
    conn.execute(
        """INSERT INTO corrections
           (slug, logged_utc, subject, superseded_claim, corrected_claim, cause, source_run)
           VALUES (?,?,?,?,?,?,?)""",
        (slug, utc_now(), subject, superseded_claim, corrected_claim, cause, source_run),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Provenance tracer
# ---------------------------------------------------------------------------

def trace_run(conn: sqlite3.Connection, run_id: int) -> dict:
    """
    Resolve a run to the chain that supports it. Returns the run's parameters,
    the requests it issued, and the public addresses behind its member records.
    This is the function that makes the phrase 'the observer is itself
    observable' operational rather than decorative.
    """
    run = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if run is None:
        raise KeyError(f"no such run: {run_id}")

    requests = conn.execute(
        """SELECT request_id, url, requested_utc, http_status, ok, error
           FROM requests WHERE run_id=? ORDER BY request_id""",
        (run_id,),
    ).fetchall()

    # Records reachable from this run, whichever module produced it.
    urls = conn.execute(
        """SELECT DISTINCT c.source_url
           FROM comments c
           JOIN texts t ON t.comment_id = c.comment_id
           JOIN cluster_members cm ON cm.text_id = t.text_id
           WHERE cm.run_id = ?""",
        (run_id,),
    ).fetchall()
    if not urls:
        urls = conn.execute(
            """SELECT DISTINCT c.source_url FROM comments c
               JOIN requests r ON r.request_id = c.request_id WHERE r.run_id=?""",
            (run_id,),
        ).fetchall()

    return {
        "run_id": run_id,
        "module": run["module"],
        "module_version": run["module_version"],
        "docket_id": run["docket_id"],
        "status": run["status"],
        "started_utc": run["started_utc"],
        "finished_utc": run["finished_utc"],
        "parameters": json.loads(run["parameters_json"]),
        "parameters_sha256": run["parameters_sha256"],
        "code_sha": run["code_sha"],
        "requests_issued": len(requests),
        "requests_failed": sum(1 for r in requests if not r["ok"]),
        "government_urls": [u["source_url"] for u in urls],
    }


def integrity_check(conn: sqlite3.Connection) -> dict:
    """
    Verify that stored raw responses still hash to their recorded digest, and
    that every text and cluster is attributable to a registered run.
    """
    from .core import sha256 as _s

    bad_hash = []
    for row in conn.execute("SELECT comment_id, raw_json, raw_sha256 FROM comments"):
        if _s(row["raw_json"]) != row["raw_sha256"]:
            bad_hash.append(row["comment_id"])
    for row in conn.execute("SELECT comment_id, raw_json, raw_sha256 FROM comment_details"):
        if _s(row["raw_json"]) != row["raw_sha256"]:
            bad_hash.append(row["comment_id"] + " (detail)")
    orphan_texts = conn.execute(
        "SELECT COUNT(*) FROM texts t LEFT JOIN runs r ON r.run_id=t.run_id WHERE r.run_id IS NULL"
    ).fetchone()[0]
    orphan_clusters = conn.execute(
        "SELECT COUNT(*) FROM clusters c LEFT JOIN runs r ON r.run_id=c.run_id WHERE r.run_id IS NULL"
    ).fetchone()[0]
    unclosed = conn.execute("SELECT COUNT(*) FROM runs WHERE status='running'").fetchone()[0]
    return {
        "raw_hash_mismatches": bad_hash,
        "orphan_texts": orphan_texts,
        "orphan_clusters": orphan_clusters,
        "unclosed_runs": unclosed,
        "ok": not bad_hash and orphan_texts == 0 and orphan_clusters == 0,
    }
