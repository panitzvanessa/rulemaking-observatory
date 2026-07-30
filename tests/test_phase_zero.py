"""
Phase zero test suite.

Runs entirely offline against a fixture transport and a temporary database, so
the instrument can be verified without a network connection and without an API
key. Every test that asserts a number also asserts the provenance of that
number, because a figure the instrument cannot trace is not a figure this
project reports.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from observatory import (  # noqa: E402
    Collector,
    Config,
    Epistemics,
    FixtureTransport,
    ReportDisciplineError,
    campaign_report,
    connect,
    detect,
    estimated_jaccard,
    extract_field_texts,
    init_db,
    integrity_check,
    is_placeholder,
    seed,
    shingles,
    signature,
    trace_run,
)
from observatory.core import register_docket  # noqa: E402
from observatory.collect import eastern_cursor  # noqa: E402

DOCKET = "TEST-AGENCY-2026-0001"

FORM_LETTER = (
    "I am writing to express my strong support for the proposed rule. "
    "As a working person in this industry I value the flexibility and independence "
    "that my current arrangement provides, and I believe the agency should preserve it. "
    "The proposed change would protect workers like me from misclassification while "
    "allowing us to continue choosing our own schedules and our own clients. "
    "I urge the agency to adopt the rule as proposed and to act without further delay."
)

SATELLITE_SUFFIXES = [
    " I have worked in this field for eleven years and I have never wanted a different arrangement.",
    " My family depends on the income I earn on my own schedule, which no employer could offer me.",
    " I drive in the evenings after caring for my mother during the day, and flexibility is why.",
    " I started doing this work after a layoff and it has been the most stable income I have had.",
]

UNIQUE_TEXTS = [
    (
        "The Department should reconsider the economic analysis accompanying this proposal. "
        "The regulatory impact analysis at 89 FR 1638 relies on wage data from 2019 that no "
        "longer reflects current labour market conditions, and the resulting cost estimate "
        "understates compliance burdens for small entities under 5 U.S.C. 603. We recommend "
        "that the agency republish the analysis for comment before finalising any rule."
    ),
    (
        "Please do not finalise this rule. I have read the proposal and I do not think the "
        "agency understands what this work is actually like for the people who do it. "
        "Nobody asked us before writing this and the parts about scheduling do not match "
        "anything I recognise from my own week."
    ),
    (
        "On behalf of our member organisations we submit the following comments. The proposal "
        "correctly identifies the harm of misclassification, and we support the agency's "
        "authority under the Fair Labor Standards Act to address it. We suggest three "
        "clarifications to the economic realities test set out in the preamble, each of which "
        "would reduce litigation risk without weakening the substantive protection."
    ),
]

PROPOSED_RULE = (
    "The Department proposes to revise its regulation interpreting employee or independent "
    "contractor status under the Fair Labor Standards Act. The Department believes this "
    "proposal would reduce the risk that employees are misclassified as independent "
    "contractors while providing added certainty for businesses that engage individuals who "
    "are in business for themselves. The Department seeks comment on all aspects of this proposal."
)


def _record(cid: str, comment: str, has_attachment: bool = False, receive: str = "2026-02-10"):
    return {
        "id": cid,
        "type": "comments",
        "attributes": {
            "comment": comment,
            "receiveDate": receive,
            "postedDate": "2026-02-22",
            "hasAttachments": has_attachment,
            "trackingNbr": f"trk-{cid}",
        },
    }


OBJECT_ID = "0900006480fixture"


def _document_page(cfg: Config) -> dict[str, str]:
    """The documents listing, which yields objectId and the docket inventory."""
    c = Collector(None, cfg, FixtureTransport({}))
    doc = {
        "id": f"{DOCKET}-0001",
        "type": "documents",
        "attributes": {"objectId": OBJECT_ID, "documentType": "Proposed Rule"},
    }
    return {c.document_page_url(DOCKET, 1, 250): json.dumps(
        {"data": [doc], "meta": {"hasNextPage": False}}
    )}


def _list_record(cid: str, has_attachment: bool = False, receive: str = "2026-02-10"):
    """
    A LIST record, faithful to the interface. The archive of the first
    instrument confirms that list records carry no comment field at all, so
    the fixture must not carry one either.
    """
    return {
        "id": cid,
        "type": "comments",
        "attributes": {
            "receiveDate": receive,
            "postedDate": "2026-02-22",
            "lastModifiedDate": "2026-02-22T14:00:00Z",
            "hasAttachments": has_attachment,
            "trackingNbr": f"trk-{cid}",
            "objectId": f"obj-{cid}",
        },
    }


def _detail_record(cid: str, comment: str, has_attachment: bool = False):
    """The DETAIL record, which is where the comment text lives."""
    return {
        "id": cid,
        "type": "comments",
        "attributes": {
            "comment": comment,
            "receiveDate": "2026-02-10",
            "postedDate": "2026-02-22",
            "hasAttachments": has_attachment,
            "trackingNbr": f"trk-{cid}",
        },
    }


def _corpus() -> list[tuple[str, str, bool]]:
    """(comment_id, text, has_attachment) for the fixture corpus of 20."""
    out = []
    n = 0
    for _ in range(6):
        n += 1
        out.append((f"c{n:03d}", FORM_LETTER, False))
    for suffix in SATELLITE_SUFFIXES:
        n += 1
        out.append((f"c{n:03d}", FORM_LETTER + suffix, False))
    micro = (
        "This proposal is an overreach and the agency knows it. The public was given sixty days "
        "to respond to a document that took three years to write, which is not consultation, "
        "and the record should reflect that objection clearly and on its own terms."
    )
    for _ in range(3):
        n += 1
        out.append((f"c{n:03d}", micro, False))
    for t in UNIQUE_TEXTS:
        n += 1
        out.append((f"c{n:03d}", t, False))
    for _ in range(4):
        n += 1
        out.append((f"c{n:03d}", "See attached.", True))
    return out


def _fixture_pages(cfg: Config) -> dict[str, str]:
    """List page plus one detail page per comment, mirroring the live shape."""
    collector = Collector(None, cfg, FixtureTransport({}))
    corpus = _corpus()
    pages = _document_page(cfg)
    listing = [_list_record(cid, att) for cid, _t, att in corpus]
    pages[collector.comment_page_url(DOCKET, 1, 250)] = json.dumps(
        {"data": listing, "meta": {"hasNextPage": False, "totalElements": 24}}
    )
    for cid, text, att in corpus:
        pages[collector.comment_detail_url(cid)] = json.dumps(
            {"data": _detail_record(cid, text, att)}
        )
    return pages


@pytest.fixture()
def db(tmp_path) -> tuple[sqlite3.Connection, Config]:
    cfg = Config(root=tmp_path, api_key=None)
    conn = connect(cfg)
    init_db(conn)
    seed(conn)
    register_docket(
        conn, DOCKET, agency="TEST", title="Fixture docket", era="post",
        design_role="unit test",
    )
    conn.execute(
        """INSERT INTO retrieval_status (docket_id, posted_count, received_count,
           received_source, sampling, as_of_utc)
           VALUES (?,?,?,?,?,datetime('now'))
           ON CONFLICT(docket_id) DO UPDATE SET
             posted_count=excluded.posted_count,
             received_count=excluded.received_count,
             received_source=excluded.received_source""",
        (DOCKET, 24, 400, "fixture preamble", "identifier_ordered"),
    )
    conn.commit()
    return conn, cfg


# ---------------------------------------------------------------------------
# Schema and provenance spine
# ---------------------------------------------------------------------------

def test_schema_has_every_phase_zero_table(db):
    conn, _ = db
    names = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        )
    }
    required = {
        "runs", "requests", "dockets", "retrieval_status", "comments", "texts",
        "commenter_codes", "clusters", "cluster_members", "coalitions",
        "coalition_members", "rule_texts", "citations", "samples",
        "sample_members", "artifacts", "corrections", "v_retrieval_share",
    }
    assert required <= names


def test_registry_seeded_with_known_artifacts_and_corrections(db):
    conn, _ = db
    arts = {r["slug"] for r in conn.execute("SELECT slug FROM artifacts")}
    assert "blank_fields_counted_as_values" in arts
    assert "identifier_ordered_retrieval" in arts
    assert "posted_date_reconstructs_agency_batching" in arts
    cors = {r["slug"] for r in conn.execute("SELECT slug FROM corrections")}
    assert "whd2022_campaign1_identity" in cors
    assert "whd2026_instacart_classification" in cors


def test_seed_is_idempotent(db):
    conn, _ = db
    before = conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
    seed(conn)
    seed(conn)
    assert conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == before


def test_failed_run_is_recorded_as_failed_not_hidden(db):
    conn, _ = db
    from observatory.core import Run
    with pytest.raises(ValueError):
        with Run(conn, "deliberate_failure", {"x": 1}) as run:
            rid = run.run_id
            raise ValueError("boom")
    row = conn.execute("SELECT status, note FROM runs WHERE run_id=?", (rid,)).fetchone()
    assert row["status"] == "failed"
    assert "boom" in row["note"]


def test_parameter_hash_is_order_independent(db):
    conn, _ = db
    from observatory.core import Run
    with Run(conn, "m", {"a": 1, "b": 2}) as r1:
        pass
    with Run(conn, "m", {"b": 2, "a": 1}) as r2:
        pass
    h1 = conn.execute("SELECT parameters_sha256 FROM runs WHERE run_id=?", (r1.run_id,)).fetchone()[0]
    h2 = conn.execute("SELECT parameters_sha256 FROM runs WHERE run_id=?", (r2.run_id,)).fetchone()[0]
    assert h1 == h2


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------

def test_collection_stores_verbatim_and_logs_every_request(db):
    conn, cfg = db
    pages = _fixture_pages(cfg)
    collector = Collector(conn, cfg, FixtureTransport(pages), progress=False)
    summary = collector.collect_docket(DOCKET, max_pages=3)

    assert summary["records_stored"] == 20
    stored = conn.execute("SELECT COUNT(*) FROM comments WHERE docket_id=?", (DOCKET,)).fetchone()[0]
    assert stored == 20

    # A short page means the corpus is exhausted, so the collector must stop
    # rather than spend a request from a shared hourly budget.
    reqs = conn.execute(
        "SELECT url, ok, error FROM requests WHERE run_id=? ORDER BY request_id",
        (summary["run_id"],),
    ).fetchall()
    assert len(reqs) == 2, "one documents listing plus one comments page"
    assert all(r["ok"] == 1 for r in reqs)
    assert "/documents?" in reqs[0]["url"]
    assert "filter%5BdocketId%5D" in reqs[1]["url"]
    assert summary["posted_total_from_api"] == 24
    posted = conn.execute(
        "SELECT posted_count FROM retrieval_status WHERE docket_id=?", (DOCKET,)
    ).fetchone()[0]
    assert posted == 24, "totalElements from the interface must land in retrieval_status"

    # Raw payload is byte identical to what the transport returned, and the
    # list record carries no comment field, which mirrors the live interface.
    row = conn.execute("SELECT raw_json FROM comments WHERE comment_id='c001'").fetchone()
    assert "trk-c001" in row["raw_json"]
    assert '"comment"' not in row["raw_json"]


def test_a_failed_request_is_logged_as_a_gap(db):
    """
    A retrieval failure must be visible in the request log. An empty corpus that
    resulted from a failed request and an empty corpus that resulted from a
    docket with no comments are different facts, and the log is what separates
    them.
    """
    conn, cfg = db
    collector = Collector(conn, cfg, FixtureTransport({}), progress=False)  # every URL misses
    summary = collector.collect_docket(DOCKET, max_pages=2)
    assert summary["records_stored"] == 0
    assert summary["pages_failed"] == 2, "documents listing and comments listing each fail"
    assert not summary["complete"]
    reqs = conn.execute(
        "SELECT ok, http_status, error FROM requests WHERE run_id=?", (summary["run_id"],)
    ).fetchall()
    assert len(reqs) == 2
    assert all(r["ok"] == 0 and r["http_status"] == 404 and r["error"] for r in reqs)


def test_recollection_does_not_inflate_the_stored_count(db):
    conn, cfg = db
    pages = _fixture_pages(cfg)
    first = Collector(conn, cfg, FixtureTransport(pages), progress=False).collect_docket(DOCKET)
    second = Collector(conn, cfg, FixtureTransport(pages), progress=False).collect_docket(DOCKET)
    assert first["records_stored"] == 20
    assert second["records_stored"] == 0, "conflicting inserts must not count as writes"
    assert conn.execute(
        "SELECT COUNT(*) FROM comments WHERE docket_id=?", (DOCKET,)
    ).fetchone()[0] == 20


def test_every_comment_is_attributable_to_a_url_and_a_request(db):
    conn, cfg = db
    collector = Collector(conn, cfg, FixtureTransport(_fixture_pages(cfg)), progress=False)
    collector.collect_docket(DOCKET)
    orphans = conn.execute(
        """SELECT COUNT(*) FROM comments c
           LEFT JOIN requests r ON r.request_id=c.request_id
           WHERE r.request_id IS NULL OR c.source_url IS NULL"""
    ).fetchone()[0]
    assert orphans == 0


def test_placeholder_detection(db):
    assert is_placeholder("See attached.")
    assert is_placeholder("see attachment")
    assert is_placeholder("   ")
    assert is_placeholder("N/A")
    assert not is_placeholder(FORM_LETTER)
    # A long comment mentioning an attachment is content, not a pointer.
    assert not is_placeholder(
        "Our detailed technical analysis is attached, and we summarise its three central "
        "conclusions here so that the agency can respond to them directly in the preamble "
        "without needing to open the file at all."
    )


def test_extraction_separates_placeholders_from_content(db):
    conn, cfg = db
    pages = _fixture_pages(cfg)
    Collector(conn, cfg, FixtureTransport(pages), progress=False).collect_docket(DOCKET)
    Collector(conn, cfg, FixtureTransport(pages), progress=False).collect_details(DOCKET)
    res = extract_field_texts(conn, DOCKET)
    assert res["texts_in_corpus"] == 20
    assert res["rows_written"] == 20
    assert res["placeholders"] == 4
    assert res["placeholder_share_pct"] == pytest.approx(20.0, abs=0.1)
    assert res["from_detail_records"] == 20
    assert not res["text_missing"]


def test_listing_without_details_is_flagged_as_text_missing(db):
    """
    The archive proved that list records carry no comment field, so a corpus
    collected without the detail stage must refuse to pass as a text corpus.
    """
    conn, cfg = db
    Collector(conn, cfg, FixtureTransport(_fixture_pages(cfg)), progress=False).collect_docket(DOCKET)
    res = extract_field_texts(conn, DOCKET)
    assert res["text_missing"]
    assert res["records_without_a_comment_field"] == res["texts_in_corpus"] == 20
    assert "warning" in res


def test_detail_stage_is_resumable_and_stores_verbatim(db):
    conn, cfg = db
    pages = _fixture_pages(cfg)
    Collector(conn, cfg, FixtureTransport(pages), progress=False).collect_docket(DOCKET)
    first = Collector(conn, cfg, FixtureTransport(pages), progress=False).collect_details(DOCKET)
    assert first["fetched"] == 20 and first["failed"] == 0 and first["remaining"] == 0
    row = conn.execute(
        "SELECT raw_json, source_url FROM comment_details WHERE comment_id='c001'"
    ).fetchone()
    assert "flexibility and independence" in row["raw_json"]
    assert row["source_url"].endswith("/comments/c001")
    # Resumable, a second pass finds nothing pending and issues no requests.
    second = Collector(conn, cfg, FixtureTransport(pages), progress=False).collect_details(DOCKET)
    assert second["pending_before"] == 0 and second["fetched"] == 0
    reqs = conn.execute(
        "SELECT COUNT(*) FROM requests WHERE run_id=?", (second["run_id"],)
    ).fetchone()[0]
    assert reqs == 0


def test_cursor_persists_across_windows_and_resumes(db):
    """
    A docket above the five thousand record cap is collected in windows joined
    by a persisted lastModifiedDate cursor, converted conservatively to
    Eastern time. Simulated here with a page size of two and one page per
    window, so the cap is reached after two records.
    """
    conn, cfg = db
    c = Collector(None, cfg, FixtureTransport({}))
    w1 = [_list_record("w001"), _list_record("w002")]
    w1[-1]["attributes"]["lastModifiedDate"] = "2026-02-10T10:00:00Z"
    cursor = eastern_cursor("2026-02-10T10:00:00Z")
    assert cursor == "2026-02-10 05:00:00", "five hours behind UTC"
    w2 = [_list_record("w003"), _list_record("w004")]
    pages = _document_page(cfg)
    # The documents listing inherits the same page size as the comments listing.
    doc = {"id": f"{DOCKET}-0001", "type": "documents",
           "attributes": {"objectId": OBJECT_ID, "documentType": "Proposed Rule"}}
    pages[c.document_page_url(DOCKET, 1, 2)] = json.dumps(
        {"data": [doc], "meta": {"hasNextPage": False}}
    )
    pages[c.comment_page_url(DOCKET, 1, 2)] = json.dumps(
        {"data": w1, "meta": {"hasNextPage": True, "totalElements": 4}}
    )
    pages[c.comment_page_url(DOCKET, 1, 2, cursor=cursor)] = json.dumps(
        {"data": w2, "meta": {"hasNextPage": False}}
    )
    summary = Collector(conn, cfg, FixtureTransport(pages), progress=False).collect_docket(
        DOCKET, max_pages=1, page_size=2
    )
    assert summary["records_stored"] == 4
    assert summary["complete"]
    row = conn.execute(
        "SELECT cursor, complete FROM collection_cursors WHERE task=?",
        (f"comments:{DOCKET}",),
    ).fetchone()
    assert row["complete"] == 1
    assert row["cursor"] == cursor


# ---------------------------------------------------------------------------
# Similarity primitives
# ---------------------------------------------------------------------------

def test_shingles_are_five_word_sequences():
    s = shingles("one two three four five six")
    assert "one two three four five" in s
    assert "two three four five six" in s
    assert len(s) == 2


def test_identical_texts_have_identical_signatures():
    a = signature(shingles(FORM_LETTER))
    b = signature(shingles(FORM_LETTER))
    assert a == b
    assert estimated_jaccard(a, b) == 1.0


def test_unrelated_texts_have_low_estimated_similarity():
    a = signature(shingles(UNIQUE_TEXTS[0]))
    b = signature(shingles(UNIQUE_TEXTS[1]))
    assert estimated_jaccard(a, b) < 0.2


def test_satellite_variants_are_similar_to_their_core():
    core = signature(shingles(FORM_LETTER))
    sat = signature(shingles(FORM_LETTER + SATELLITE_SUFFIXES[0]))
    assert estimated_jaccard(core, sat) > 0.6


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def _collected(db):
    conn, cfg = db
    pages = _fixture_pages(cfg)
    Collector(conn, cfg, FixtureTransport(pages), progress=False).collect_docket(DOCKET)
    Collector(conn, cfg, FixtureTransport(pages), progress=False).collect_details(DOCKET)
    extract_field_texts(conn, DOCKET)
    return conn, cfg


def test_detector_recovers_form_letter_with_personalised_satellites(db):
    conn, _ = _collected(db)
    summary = detect(conn, DOCKET)
    clusters = conn.execute(
        "SELECT size, kind FROM clusters WHERE run_id=? ORDER BY size DESC",
        (summary["run_id"],),
    ).fetchall()
    assert clusters, "detector found no clusters in a fixture built around one"
    # Six exact copies plus four personalised satellites is the campaign.
    assert clusters[0]["size"] == 10
    assert clusters[0]["kind"] == "near"


def test_detector_sees_micro_coordination_below_the_conventional_threshold(db):
    conn, _ = _collected(db)
    summary = detect(conn, DOCKET, min_cluster=3)
    sizes = sorted(
        r["size"] for r in conn.execute(
            "SELECT size FROM clusters WHERE run_id=?", (summary["run_id"],)
        )
    )
    assert 3 in sizes, "a coordinated batch of three must be visible by construction"


def test_min_cluster_threshold_is_respected(db):
    conn, _ = _collected(db)
    s = detect(conn, DOCKET, min_cluster=11)
    sizes = [r["size"] for r in conn.execute(
        "SELECT size FROM clusters WHERE run_id=?", (s["run_id"],)
    )]
    assert all(x >= 11 for x in sizes)


def test_placeholders_never_enter_clusters(db):
    conn, _ = _collected(db)
    s = detect(conn, DOCKET)
    ph = conn.execute(
        """SELECT COUNT(*) FROM cluster_members cm
           JOIN texts t ON t.text_id=cm.text_id
           WHERE cm.run_id=? AND t.is_placeholder=1""",
        (s["run_id"],),
    ).fetchone()[0]
    assert ph == 0


def test_rule_text_is_stripped_before_clustering(db):
    """
    Two comments that share only quoted rule text must not be clustered once the
    rule's own phrases are removed.
    """
    conn, cfg = db
    # The quoted block dominates each comment, which is the realistic case. Each
    # commenter then adds a short and genuinely distinct sentence of their own.
    quote = PROPOSED_RULE
    records = [
        ("q1", quote + " I oppose it."),
        ("q2", quote + " I support it."),
        ("q3", quote + " I am undecided."),
    ]
    collector = Collector(conn, cfg, FixtureTransport({}))
    pages = _document_page(cfg)
    listing = [_list_record(cid) for cid, _t in records]
    pages[collector.comment_page_url(DOCKET, 1, 250)] = json.dumps(
        {"data": listing, "meta": {"hasNextPage": False, "totalElements": len(records)}}
    )
    for cid, text in records:
        pages[collector.comment_detail_url(cid)] = json.dumps(
            {"data": _detail_record(cid, text)}
        )
    Collector(conn, cfg, FixtureTransport(pages), progress=False).collect_docket(DOCKET)
    Collector(conn, cfg, FixtureTransport(pages), progress=False).collect_details(DOCKET)
    extract_field_texts(conn, DOCKET)

    conn.execute(
        """INSERT INTO rule_texts (docket_id, stage, fr_document, content, content_sha256,
           source_url, retrieved_utc) VALUES (?,?,?,?,?,?,datetime('now'))""",
        (DOCKET, "proposed", "FR-TEST-1", PROPOSED_RULE, "x", "http://example.test"),
    )
    conn.commit()

    with_strip = detect(conn, DOCKET, strip_rule_text=True)
    without_strip = detect(conn, DOCKET, strip_rule_text=False)
    assert with_strip["rule_shingles_stripped"] > 0
    assert without_strip["rule_shingles_stripped"] == 0
    assert with_strip["texts_in_clusters"] < without_strip["texts_in_clusters"], (
        "stripping quoted rule text must reduce spurious clustering"
    )


def test_detection_summary_always_carries_its_denominator(db):
    conn, _ = _collected(db)
    s = detect(conn, DOCKET)
    assert s["texts_considered"] > 0
    assert s["clustered_share_pct"] is not None
    assert s["texts_in_clusters"] <= s["texts_considered"]


# ---------------------------------------------------------------------------
# Provenance and integrity
# ---------------------------------------------------------------------------

def test_run_traces_back_to_government_urls(db):
    conn, _ = _collected(db)
    s = detect(conn, DOCKET)
    trace = trace_run(conn, s["run_id"])
    assert trace["module"] == "detect"
    assert trace["parameters"]["min_cluster"] == 3
    assert trace["government_urls"], "a cluster must resolve to the address that produced it"
    assert all(u.startswith("http") for u in trace["government_urls"])


def test_integrity_check_passes_on_a_clean_database(db):
    conn, _ = _collected(db)
    detect(conn, DOCKET)
    result = integrity_check(conn)
    assert result["ok"], result


def test_integrity_check_catches_tampering(db):
    conn, _ = _collected(db)
    conn.execute("UPDATE comments SET raw_json='{\"tampered\":true}' WHERE comment_id='c001'")
    conn.commit()
    result = integrity_check(conn)
    assert not result["ok"]
    assert "c001" in result["raw_hash_mismatches"]


# ---------------------------------------------------------------------------
# Report discipline
# ---------------------------------------------------------------------------

def test_report_refuses_to_omit_what_the_data_cannot_show(db):
    conn, _ = _collected(db)
    s = detect(conn, DOCKET)
    with pytest.raises(ReportDisciplineError):
        campaign_report(conn, s["run_id"], Epistemics(show=["something"], cannot_show=[]))
    with pytest.raises(ReportDisciplineError):
        campaign_report(conn, s["run_id"], Epistemics(show=[], cannot_show=["something"]))


def test_report_states_coverage_guards_and_epistemics(db):
    conn, _ = _collected(db)
    s = detect(conn, DOCKET)
    text = campaign_report(
        conn,
        s["run_id"],
        Epistemics(
            show=["Ten of thirteen texts considered fall into one campaign with a shared core."],
            consistent_with=["A single sponsor circulating a template with a personalisation slot."],
            cannot_show=[
                "Who drafted any individual comment, or whether any drafting tool was used.",
                "The composition of the received record, which exceeds the posted record.",
            ],
        ),
    )
    assert "of 24 posted records stored" in text
    assert "percent of it" in text          # received against posted
    assert "Guards in force" in text
    assert "Blank fields are absent declarations" in text
    assert "What they cannot show." in text
    assert "corpus level and comparative" in text
    assert "placeholder text field" in text


# ---------------------------------------------------------------------------
# Idempotency, the NULL in UNIQUE trap
# ---------------------------------------------------------------------------

def test_re_extraction_does_not_duplicate_the_corpus(db):
    """
    Regression. The texts identity constraint spans attachment_ref, which is
    nullable, and in SQL a NULL never equals a NULL, so an inline UNIQUE over it
    never fires and ON CONFLICT DO NOTHING never triggers. Every re-extraction
    silently doubled the corpus, which inflated every count downstream. The
    constraint is now an expression index over COALESCE(attachment_ref, '').
    """
    conn, cfg = db
    pages = _fixture_pages(cfg)
    Collector(conn, cfg, FixtureTransport(pages), progress=False).collect_docket(DOCKET)
    Collector(conn, cfg, FixtureTransport(pages), progress=False).collect_details(DOCKET)

    first = extract_field_texts(conn, DOCKET)
    second = extract_field_texts(conn, DOCKET)

    assert first["rows_written"] == 20
    assert second["rows_written"] == 0, "a repeat extraction must write nothing"
    assert second["texts_in_corpus"] == 20, "and must report the corpus, not the attempt"
    assert conn.execute("SELECT COUNT(*) FROM texts").fetchone()[0] == 20


def test_detector_sees_one_generation_of_each_text(db):
    """
    Even when two extractor versions coexist by design, the detector must load
    only the newest text per comment, or every text would cluster with its own
    predecessor and every count would double.
    """
    conn, cfg = db
    pages = _fixture_pages(cfg)
    Collector(conn, cfg, FixtureTransport(pages), progress=False).collect_docket(DOCKET)
    Collector(conn, cfg, FixtureTransport(pages), progress=False).collect_details(DOCKET)
    extract_field_texts(conn, DOCKET)
    baseline = detect(conn, DOCKET)["texts_considered"]

    # Simulate a second extractor generation, which the schema permits on purpose.
    conn.execute(
        """INSERT INTO texts (comment_id, run_id, source, attachment_ref, extractor,
               extractor_version, content, content_sha256, char_len, word_len, is_placeholder)
           SELECT comment_id, run_id, source, attachment_ref, extractor,
                  '3.0.0', content, content_sha256, char_len, word_len, is_placeholder
           FROM texts"""
    )
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM texts").fetchone()[0] == 40

    after = detect(conn, DOCKET)["texts_considered"]
    assert after == baseline, "two generations must not double the corpus"


def test_rule_texts_identity_survives_a_null_document_number(db):
    """The same NULL in UNIQUE trap applied to rule_texts.fr_document."""
    conn, _ = db
    for _ in range(2):
        conn.execute(
            """INSERT INTO rule_texts (docket_id, stage, fr_document, content,
                   content_sha256, source_url, retrieved_utc)
               VALUES (?,?,?,?,?,?,datetime('now'))
               ON CONFLICT DO NOTHING""",
            (DOCKET, "proposed", None, PROPOSED_RULE, "x", "http://example.test"),
        )
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM rule_texts").fetchone()[0] == 1


def test_integrity_check_covers_detail_records(db):
    conn, cfg = db
    pages = _fixture_pages(cfg)
    Collector(conn, cfg, FixtureTransport(pages), progress=False).collect_docket(DOCKET)
    Collector(conn, cfg, FixtureTransport(pages), progress=False).collect_details(DOCKET)
    assert integrity_check(conn)["ok"]
    conn.execute("UPDATE comment_details SET raw_json='{}' WHERE comment_id='c001'")
    conn.commit()
    result = integrity_check(conn)
    assert not result["ok"]
    assert any("detail" in m for m in result["raw_hash_mismatches"])


# ---------------------------------------------------------------------------
# Command line entry point, and two defects that only surface on Windows
# ---------------------------------------------------------------------------

def test_cli_pins_output_to_utf8(tmp_path):
    """
    Regression. Comment text carries characters outside the Windows legacy code
    page, the subscript two of CO2 and the Greek mu of microgram among them, and
    the detect command prints excerpts. A redirected stream on Windows falls back
    to the locale encoding, so run.py must pin its streams to UTF-8 before
    printing anything or a saved report would crash mid-run.
    """
    import subprocess
    root = Path(__file__).resolve().parents[1]
    script = root / "run.py"
    assert script.exists(), "the command line entry point must ship with the package"
    src = script.read_text(encoding="utf-8")
    assert 'reconfigure(encoding="utf-8"' in src
    # And the reconfiguration must happen before the first print in the module.
    assert src.index("reconfigure") < src.index("def show(")

    out = subprocess.run(
        [sys.executable, str(script), "--root", str(tmp_path), "check"],
        capture_output=True, text=True,
        env={**os.environ, "PYTHONIOENCODING": "cp1252"},
    )
    assert out.returncode == 0, out.stderr
    assert "Integrity" in out.stdout


def test_cli_closes_its_database_connection(tmp_path):
    """
    Regression. Each command opened its own connection and none was closed, so a
    chained command left several open at once. On Windows an open handle keeps
    the database and its write ahead log locked, which can fail the next command
    or leave a folder that cannot be deleted.
    """
    import subprocess
    root = Path(__file__).resolve().parents[1]
    script = root / "run.py"
    src = script.read_text(encoding="utf-8")
    assert "conn.close()" in src
    assert src.count("open_db(cfg)") == 1, "exactly one place may open a connection"

    # Two invocations in sequence must both succeed against the same database.
    for _ in range(2):
        out = subprocess.run(
            [sys.executable, str(script), "--root", str(tmp_path), "status"],
            capture_output=True, text=True,
        )
        assert out.returncode == 0, out.stderr


def test_cli_refuses_to_collect_without_a_key(tmp_path):
    import subprocess
    root = Path(__file__).resolve().parents[1]
    env = {k: v for k, v in os.environ.items() if k != "REGS_API_KEY"}
    out = subprocess.run(
        [sys.executable, str(root / "run.py"), "--root", str(tmp_path),
         "collect", "WHD-2026-0001"],
        capture_output=True, text=True, env=env,
    )
    assert out.returncode != 0
    assert "No API key" in out.stdout + out.stderr


def test_cli_known_dockets_cover_the_design(tmp_path):
    """Agency and era must never need retyping, so all four dockets are known."""
    import importlib.util
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("_run", root / "run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for docket in ("EPA-HQ-OAR-2021-0317", "WHD-2022-0003",
                   "EPA-HQ-OAR-2025-0194", "WHD-2026-0001"):
        assert docket in mod.KNOWN_DOCKETS
        agency, era, note = mod.KNOWN_DOCKETS[docket]
        assert agency and era and note
        assert era in ("pre", "post", "spanning")


def test_documents_are_stored_with_their_object_id(db):
    """
    The objectId on each document is what the comments endpoint filters on in
    the documented retrieval path, and later phases need these records to reach
    rule preambles. The table existed before anything wrote to it, which this
    test prevents from recurring.
    """
    conn, cfg = db
    Collector(conn, cfg, FixtureTransport(_fixture_pages(cfg)), progress=False).collect_docket(DOCKET)
    rows = conn.execute(
        "SELECT document_id, object_id, document_type FROM documents WHERE docket_id=?",
        (DOCKET,),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["object_id"] == OBJECT_ID
    assert rows[0]["document_type"] == "Proposed Rule"
    assert conn.execute(
        "SELECT raw_json FROM documents WHERE document_id=?", (rows[0]["document_id"],)
    ).fetchone()["raw_json"]


def test_progress_is_reported_during_collection(db, capsys):
    """
    Regression. A stage that prints nothing for hours cannot be distinguished
    from one that has hung, and the details stage runs for tens of hours on the
    larger dockets. Both stages must report as they go.
    """
    conn, cfg = db
    pages = _fixture_pages(cfg)
    Collector(conn, cfg, FixtureTransport(pages)).collect_docket(DOCKET)
    listing = capsys.readouterr().out
    assert "page 1" in listing
    assert "stored so far" in listing

    Collector(conn, cfg, FixtureTransport(pages)).collect_details(DOCKET, commit_every=5)
    details = capsys.readouterr().out
    assert "of 20 details" in details
    assert "remaining" in details


# ---------------------------------------------------------------------------
# Infrastructure modules, rhythm, audit, aggregation
# ---------------------------------------------------------------------------

from observatory import aggregation, analysis_report, audit_cost, posting_rhythm  # noqa: E402
from observatory.report import Epistemics as _Ep  # noqa: E402


def test_posting_rhythm_hand_computed_on_fixture(db):
    """
    Every fixture comment is received 2026-02-10 and posted 2026-02-22, so the
    lag is exactly twelve days, one posting day carries one hundred percent,
    and the guard inversion sentence must appear in the report.
    """
    conn, cfg = db
    Collector(conn, cfg, FixtureTransport(_fixture_pages(cfg)), progress=False).collect_docket(DOCKET)
    r = posting_rhythm(conn, DOCKET)
    assert r["dated_pairs"] == 20
    assert r["lag_median_days"] == 12.0
    assert r["lag_p90_days"] == 12.0
    assert r["posting_days"] == 1
    assert r["receive_days"] == 1
    assert r["top_day_share_pct"] == 100.0
    batches = conn.execute(
        "SELECT posted_date, n FROM posting_batches WHERE run_id=?", (r["run_id"],)
    ).fetchall()
    assert len(batches) == 1 and batches[0]["n"] == 20


def test_audit_cost_counts_requests_by_stage(db):
    conn, cfg = db
    pages = _fixture_pages(cfg)
    Collector(conn, cfg, FixtureTransport(pages), progress=False).collect_docket(DOCKET)
    Collector(conn, cfg, FixtureTransport(pages), progress=False).collect_details(DOCKET)
    r = audit_cost(conn, DOCKET)
    # One documents listing plus one comments listing, and one detail per comment.
    assert r["listing_requests"] == 2
    assert r["detail_requests"] == 20
    assert r["failed_requests"] == 0
    assert r["comments_stored"] == 20
    assert r["requests_per_comment"] == round(22 / 20, 2)
    # Fixture runs finish within the same second, so no measured rate exists,
    # and the module must report the gap rather than invent a rate.
    if r["measured_seconds_per_detail"] is None:
        assert r["timing_gap"] and "gap" in r["timing_note"].lower()
        assert r["full_audit_hours_measured"] is None


def test_aggregation_treats_missing_declaration_as_absent(db):
    """
    Required regression. The declaration field may be absent, null, one, or
    greater than one, and only values above one are declarations of
    aggregation. A missing field is an absent declaration, never a one.
    """
    conn, cfg = db
    c = Collector(None, cfg, FixtureTransport({}))
    cases = [("a1", None), ("a2", "null"), ("a3", 1), ("a4", 5), ("a5", 2500)]
    listing = [_list_record(cid) for cid, _v in cases]
    pages = _document_page(cfg)
    pages[c.comment_page_url(DOCKET, 1, 250)] = json.dumps(
        {"data": listing, "meta": {"hasNextPage": False, "totalElements": 5}}
    )
    for cid, v in cases:
        detail = _detail_record(cid, "A substantive comment long enough to matter here.")
        if v == "null":
            detail["attributes"]["numItemsReceived"] = None
        elif v is not None:
            detail["attributes"]["numItemsReceived"] = v
        pages[c.comment_detail_url(cid)] = json.dumps({"data": detail})
    Collector(conn, cfg, FixtureTransport(pages), progress=False).collect_docket(DOCKET)
    Collector(conn, cfg, FixtureTransport(pages), progress=False).collect_details(DOCKET)

    conn.execute(
        """UPDATE retrieval_status SET posted_count=5, received_count=3000,
           received_source='fixture preamble' WHERE docket_id=?""", (DOCKET,))
    conn.commit()

    r = aggregation(conn, DOCKET)
    assert r["details_available"] == 5
    assert r["no_declaration"] == 2, "absent and null are both absent declarations"
    assert r["declared_single"] == 1
    assert r["aggregated_records"] == 2
    assert r["declared_total"] == 2505
    assert r["multiplier_max"] == 2500
    # Gap decomposition, received 3000 against posted 5 leaves 2995, of which
    # the two aggregated records explain (5-1) + (2500-1) = 2503.
    assert r["received_minus_posted"] == 2995
    assert r["explained_by_aggregation"] == 2503
    assert r["explained_share_pct"] == round(100.0 * 2503 / 2995, 1)
    stored = conn.execute(
        "SELECT comment_id, declared_items FROM aggregated_records WHERE run_id=?",
        (r["run_id"],),
    ).fetchall()
    assert {(s["comment_id"], s["declared_items"]) for s in stored} == {("a4", 5), ("a5", 2500)}


def test_aggregation_does_not_touch_network_without_enrich(db):
    conn, cfg = db
    pages = _fixture_pages(cfg)
    Collector(conn, cfg, FixtureTransport(pages), progress=False).collect_docket(DOCKET)
    Collector(conn, cfg, FixtureTransport(pages), progress=False).collect_details(DOCKET)
    before = conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
    aggregation(conn, DOCKET)  # no collector, enrich off
    after = conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
    assert after == before, "aggregation must issue no requests unless enrichment is on"


def test_analysis_report_enforces_epistemics_and_guards(db):
    conn, cfg = db
    Collector(conn, cfg, FixtureTransport(_fixture_pages(cfg)), progress=False).collect_docket(DOCKET)
    r = posting_rhythm(conn, DOCKET)
    with pytest.raises(ReportDisciplineError):
        analysis_report(conn, r["run_id"], "t", [("s", ["x"])],
                        _Ep(show=["x"], cannot_show=[]))
    text = analysis_report(
        conn, r["run_id"], "Posting rhythm report",
        [("Rhythm", ["That guard inverts here, posted date is the correct field "
                     "because the agency's workflow is the object of study."])],
        _Ep(show=["s"], cannot_show=["c"]),
    )
    assert "guard inverts" in text
    assert "Guards in force" in text
    assert "What they cannot show." in text
    assert "of 24 posted records stored" in text


def test_boolean_declaration_is_an_absent_declaration(db):
    """
    Regression. Python's bool is a subclass of int, so a detail record carrying
    numItemsReceived true would otherwise count as a declaration of one. A
    boolean is not a count and must fall into the absent declarations.
    """
    conn, cfg = db
    c = Collector(None, cfg, FixtureTransport({}))
    listing = [_list_record("b1")]
    pages = _document_page(cfg)
    pages[c.comment_page_url(DOCKET, 1, 250)] = json.dumps(
        {"data": listing, "meta": {"hasNextPage": False, "totalElements": 1}}
    )
    detail = _detail_record("b1", "A long enough comment to be a real text either way.")
    detail["attributes"]["numItemsReceived"] = True
    pages[c.comment_detail_url("b1")] = json.dumps({"data": detail})
    Collector(conn, cfg, FixtureTransport(pages), progress=False).collect_docket(DOCKET)
    Collector(conn, cfg, FixtureTransport(pages), progress=False).collect_details(DOCKET)
    r = aggregation(conn, DOCKET)
    assert r["no_declaration"] == 1
    assert r["declared_single"] == 0
    assert r["aggregated_records"] == 0


def test_audit_windows_derived_from_logged_cursor_urls(db):
    """
    Regression. The cursors table holds only the final resume point, so window
    counts must come from the request log, where every window after the first
    carried a distinct lastModifiedDate cursor in its listing URL.
    """
    conn, cfg = db
    c = Collector(None, cfg, FixtureTransport({}))
    w1 = [_list_record("w001"), _list_record("w002")]
    w1[-1]["attributes"]["lastModifiedDate"] = "2026-02-10T10:00:00Z"
    cursor = eastern_cursor("2026-02-10T10:00:00Z")
    w2 = [_list_record("w003")]
    doc = {"id": f"{DOCKET}-0001", "type": "documents",
           "attributes": {"objectId": OBJECT_ID, "documentType": "Proposed Rule"}}
    pages = {
        c.document_page_url(DOCKET, 1, 2): json.dumps(
            {"data": [doc], "meta": {"hasNextPage": False}}),
        c.comment_page_url(DOCKET, 1, 2): json.dumps(
            {"data": w1, "meta": {"hasNextPage": True, "totalElements": 3}}),
        c.comment_page_url(DOCKET, 1, 2, cursor=cursor): json.dumps(
            {"data": w2, "meta": {"hasNextPage": False}}),
    }
    Collector(conn, cfg, FixtureTransport(pages), progress=False).collect_docket(
        DOCKET, max_pages=1, page_size=2)
    r = audit_cost(conn, DOCKET)
    assert r["cursor_windows_recorded"] == 2, "one initial window plus one cursor advance"


def test_analysis_commands_reject_a_mistyped_docket(tmp_path):
    """A typo must produce a clear sentence, not a foreign key traceback."""
    import subprocess
    root = Path(__file__).resolve().parents[1]
    for cmd in ("rhythm", "audit", "aggregation"):
        out = subprocess.run(
            [sys.executable, str(root / "run.py"), "--root", str(tmp_path),
             cmd, "WHD-2026-000X"],
            capture_output=True, text=True,
        )
        assert out.returncode != 0
        combined = out.stdout + out.stderr
        assert "not in the database" in combined, (cmd, combined)
        assert "IntegrityError" not in combined


def test_rhythm_survives_malformed_dates_and_counts_them(db):
    """
    Regression. A ten character malformed date passes the length check and then
    fails ISO parsing, and government records do carry occasional dirt. One
    dirty record is counted as unparseable and reported, never fatal.
    """
    conn, cfg = db
    Collector(conn, cfg, FixtureTransport(_fixture_pages(cfg)), progress=False).collect_docket(DOCKET)
    conn.execute("UPDATE comments SET receive_date='0000-00-00' WHERE comment_id='c001'")
    conn.execute("UPDATE comments SET posted_date='2026-99-99T00:00:00Z' WHERE comment_id='c002'")
    conn.commit()
    r = posting_rhythm(conn, DOCKET)
    assert r["unparseable_dates"] == 2
    assert r["dated_pairs"] == 18
    assert r["lag_median_days"] == 12.0


def test_aggregation_survives_corrupted_detail_records(db):
    """
    Regression. A stored detail without attributes or with unparseable JSON is
    counted as unreadable and as an absent declaration, never fatal. The
    integrity check remains the tool that surfaces how it got that way.
    """
    conn, cfg = db
    pages = _fixture_pages(cfg)
    Collector(conn, cfg, FixtureTransport(pages), progress=False).collect_docket(DOCKET)
    Collector(conn, cfg, FixtureTransport(pages), progress=False).collect_details(DOCKET)
    conn.execute("UPDATE comment_details SET raw_json='{\"id\":\"c003\"}' WHERE comment_id='c003'")
    conn.execute("UPDATE comment_details SET raw_json='not json at all' WHERE comment_id='c004'")
    conn.commit()
    r = aggregation(conn, DOCKET)
    assert r["details_available"] == 20
    assert r["unreadable_details"] == 1, "no-attributes parses, raw garbage does not"
    assert r["no_declaration"] == 20
    assert not integrity_check(conn)["ok"], "the tampering still fails integrity"


def test_out_of_order_workflow_recovers_after_details(db):
    """
    Regression for the worst workflow bug found. Running extract before details
    wrote an empty-text generation, and an identity without the content hash
    let that generation permanently block the corrected extraction, so obeying
    the instrument's own warning left the corpus empty forever. The identity
    now includes the content hash, so the refreshed extraction writes and every
    reader picks the newest generation.
    """
    conn, cfg = db
    pages = _fixture_pages(cfg)
    c = Collector(conn, cfg, FixtureTransport(pages), progress=False)
    c.collect_docket(DOCKET)
    early = extract_field_texts(conn, DOCKET)
    assert early["text_missing"]
    c.collect_details(DOCKET)
    late = extract_field_texts(conn, DOCKET)
    assert late["rows_written"] == 20, "the corrected generation must be written"
    assert not late["text_missing"]
    assert late["from_detail_records"] == 20
    d = detect(conn, DOCKET)
    assert d["texts_considered"] == 16, "the detector reads the corrected generation"
    # And a third extraction remains idempotent.
    again = extract_field_texts(conn, DOCKET)
    assert again["rows_written"] == 0
    assert again["texts_in_corpus"] == 20


def test_migration_repairs_a_database_built_with_the_old_index(tmp_path):
    """
    CREATE IF NOT EXISTS never replaces an existing object, so a database built
    before the identity fix keeps the outdated index unless init_db migrates
    it. This builds one with the old definition and asserts the repair.
    """
    import sqlite3 as _sq
    cfg = Config(root=tmp_path)
    conn = connect(cfg)
    conn.executescript(
        """CREATE TABLE texts (text_id INTEGER PRIMARY KEY AUTOINCREMENT,
             comment_id TEXT, run_id INTEGER, source TEXT, attachment_ref TEXT,
             extractor TEXT, extractor_version TEXT, content TEXT,
             content_sha256 TEXT, char_len INTEGER, word_len INTEGER,
             is_placeholder INTEGER DEFAULT 0);
           CREATE UNIQUE INDEX ux_texts_identity
             ON texts (comment_id, source, COALESCE(attachment_ref, ''),
                       extractor_version);"""
    )
    conn.commit()
    init_db(conn)
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='ux_texts_identity'"
    ).fetchone()[0]
    assert "content_sha256" in sql, "the outdated index must be replaced"


def test_html_entities_do_not_leak_into_similarity(db):
    """
    Regression. Live comments carry HTML entities, and without unescaping the
    same letter submitted with entities and with plain punctuation fails to
    match as an exact duplicate, while entity names become tokens shared
    between unrelated texts.
    """
    from observatory.detect import normalise, tokens
    entity = ("I don&rsquo;t support this rule&mdash;it hurts small business "
              "&amp; workers across the country in every single state.")
    plain = ("I don\u2019t support this rule\u2014it hurts small business "
             "& workers across the country in every single state.")
    assert normalise(entity) == normalise(plain), (
        "the same text in two encodings must normalise identically")
    for t in tokens(entity):
        assert t not in {"rsquo", "ldquo", "rdquo", "mdash", "amp", "nbsp", "quot"}


def test_receive_date_is_backfilled_from_the_detail(db):
    """
    Regression from live data. The listing omits receiveDate entirely, only the
    detail carries it, so a collector reading dates from the listing stores
    null everywhere and the rhythm module measures nothing. collect_details
    backfills the date fields from the detail, and posting_rhythm repairs
    databases collected before the fix.
    """
    conn, cfg = db
    c = Collector(None, cfg, FixtureTransport({}))
    rec = _list_record("r1")
    del rec["attributes"]["receiveDate"]          # the live listing shape
    pages = _document_page(cfg)
    pages[c.comment_page_url(DOCKET, 1, 250)] = json.dumps(
        {"data": [rec], "meta": {"hasNextPage": False, "totalElements": 1}}
    )
    pages[c.comment_detail_url("r1")] = json.dumps(
        {"data": _detail_record("r1", "A comment long enough to carry actual content here.")}
    )
    Collector(conn, cfg, FixtureTransport(pages), progress=False).collect_docket(DOCKET)
    assert conn.execute(
        "SELECT receive_date FROM comments WHERE comment_id='r1'"
    ).fetchone()[0] is None, "the listing carried no receive date"
    Collector(conn, cfg, FixtureTransport(pages), progress=False).collect_details(DOCKET)
    assert conn.execute(
        "SELECT receive_date FROM comments WHERE comment_id='r1'"
    ).fetchone()[0] == "2026-02-10", "collect_details must backfill from the detail"
    r = posting_rhythm(conn, DOCKET)
    assert r["dated_pairs"] == 1 and r["lag_median_days"] == 12.0


def test_aggregation_reads_both_declaration_fields(db):
    """
    Regression from live data. Real detail records carry duplicateComments, the
    agency's own consolidation count, not only the documented numItemsReceived.
    Either field above one is a declaration of aggregation, and zero is not.
    """
    conn, cfg = db
    c = Collector(None, cfg, FixtureTransport({}))
    cases = [("d1", {"duplicateComments": 0}), ("d2", {"duplicateComments": 7}),
             ("d3", {"numItemsReceived": 3}), ("d4", {})]
    listing = [_list_record(cid) for cid, _f in cases]
    pages = _document_page(cfg)
    pages[c.comment_page_url(DOCKET, 1, 250)] = json.dumps(
        {"data": listing, "meta": {"hasNextPage": False, "totalElements": 4}}
    )
    for cid, fields in cases:
        detail = _detail_record(cid, "A comment long enough to carry actual content here.")
        detail["attributes"].update(fields)
        pages[c.comment_detail_url(cid)] = json.dumps({"data": detail})
    Collector(conn, cfg, FixtureTransport(pages), progress=False).collect_docket(DOCKET)
    Collector(conn, cfg, FixtureTransport(pages), progress=False).collect_details(DOCKET)
    r = aggregation(conn, DOCKET)
    assert r["aggregated_records"] == 2, "seven duplicates and three items both declare"
    assert r["declared_total"] == 10
    assert r["no_declaration"] == 2, "zero and absent are both not declarations"


def test_details_stops_after_consecutive_network_failures(db, capsys):
    """
    Regression from live use. Every request timing out produced a silent screen
    for tens of minutes, because failures printed nothing and progress counted
    only successes, and the run would have ground on for days. Failures now
    print the moment they happen, and eight straight failures stop the run with
    everything already fetched kept safe and resumable.
    """
    conn, cfg = db
    pages = _fixture_pages(cfg)
    Collector(conn, cfg, FixtureTransport(pages), progress=False).collect_docket(DOCKET)
    r = Collector(conn, cfg, FixtureTransport({})).collect_details(DOCKET)  # every URL fails
    out = capsys.readouterr().out
    assert r["failed"] == 8, "the breaker must trip at eight, not grind through twenty"
    assert "stopped_early" in r
    assert "logged as a failure" in out, "each failure must be visible immediately"
    assert "not responding" in out
    # Resumability, a healthy transport picks up all twenty afterwards.
    r2 = Collector(conn, cfg, FixtureTransport(pages), progress=False).collect_details(DOCKET)
    assert r2["fetched"] == 20
