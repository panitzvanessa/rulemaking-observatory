"""
Infrastructure analyses.

Three modules under one frame, what the infrastructure of the public record
does to participation. Posting rhythm measures the agency's conversion of
received participation into a public record. Audit cost measures, from this
instrument's own request log, what it costs the public to read the record back.
Aggregation measures the channel by which one posted record may stand for
thousands of received submissions.

Everything here computes from data the collector already stores. The one
exception is the aggregation module's optional docket detail enrichment, off by
default, which issues a single logged request per docket.

Standing disciplines apply without exception. Every measure is keyed to a
registered run. Absence of a declaration is an absent declaration, never a
declaration of one. And each module's report states what its data cannot show.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from typing import Optional

from .core import Run, utc_now

MODULE_RHYTHM = "rhythm"
MODULE_AUDIT = "audit"
MODULE_AGGREGATION = "aggregation"

# The declaration fields on a comment detail record, by which one posted record
# states how many received submissions it stands for. Two fields appear in the
# wild. numItemsReceived is the bulk-submission declaration in the system's
# documentation. duplicateComments is the agency's own consolidation count,
# observed populated on live detail records. Read defensively, either may be
# absent, null, zero, or one on ordinary records, and only a value above one
# constitutes a declaration of aggregation.
DECLARATION_FIELDS = ("numItemsReceived", "duplicateComments")


def _day(value: Optional[str]) -> Optional[str]:
    if value and len(value) >= 10:
        return value[:10]
    return None


def _days_between(a: str, b: str) -> Optional[int]:
    """
    None on malformed input. Government records carry occasional malformed
    dates, and one dirty record must be counted as unparseable rather than
    allowed to fail the whole docket's analysis.
    """
    try:
        return (date.fromisoformat(b) - date.fromisoformat(a)).days
    except ValueError:
        return None


def _median(values: list) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return float(s[mid]) if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _p90(values: list) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    return float(s[min(int(0.9 * len(s)), len(s) - 1)])


def _elapsed_seconds(started: Optional[str], finished: Optional[str]) -> Optional[float]:
    if not started or not finished:
        return None
    try:
        a = datetime.fromisoformat(started)
        b = datetime.fromisoformat(finished)
        return max((b - a).total_seconds(), 0.0)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Module one, posting rhythm
# ---------------------------------------------------------------------------

def posting_rhythm(conn: sqlite3.Connection, docket_id: str) -> dict:
    """
    Measure the agency's posting rhythm for a docket.

    Posted dates are the object of study here. The artifacts registry records
    that posted dates reconstruct agency posting batches rather than submission
    behaviour, and that guard inverts for this module, posted date is the
    correct field precisely because the agency's workflow is what is being
    measured. Receive dates enter only as the comparison distribution, and
    nothing finer than day level exists in the record.
    """
    params = {"docket_id": docket_id, "batch_definition": "calendar posted date"}
    # Databases collected before the backfill fix hold null receive dates on
    # every comment, because the listing omits receiveDate and only the detail
    # carries it. Repair from stored details before measuring, deterministic
    # derived data, safe to run every time.
    conn.execute(
        """UPDATE comments SET
             receive_date = COALESCE(receive_date,
               (SELECT json_extract(d.raw_json,'$.attributes.receiveDate')
                FROM comment_details d WHERE d.comment_id = comments.comment_id)),
             posted_date = COALESCE(posted_date,
               (SELECT json_extract(d.raw_json,'$.attributes.postedDate')
                FROM comment_details d WHERE d.comment_id = comments.comment_id))
           WHERE docket_id = ?
             AND EXISTS (SELECT 1 FROM comment_details d
                         WHERE d.comment_id = comments.comment_id)""",
        (docket_id,),
    )
    conn.commit()
    with Run(conn, MODULE_RHYTHM, params, docket_id=docket_id) as run:
        rows = conn.execute(
            "SELECT receive_date, posted_date FROM comments WHERE docket_id=?",
            (docket_id,),
        ).fetchall()

        lags: list[int] = []
        posted_days: dict[str, int] = {}
        receive_days: set[str] = set()
        missing_receive = 0
        missing_posted = 0
        unparseable = 0
        for r in rows:
            rd = _day(r["receive_date"])
            pd = _day(r["posted_date"])
            if rd:
                receive_days.add(rd)
            else:
                missing_receive += 1
            if pd:
                posted_days[pd] = posted_days.get(pd, 0) + 1
            else:
                missing_posted += 1
            if rd and pd:
                lag = _days_between(rd, pd)
                if lag is None:
                    unparseable += 1
                else:
                    lags.append(lag)

        total_posted_dated = sum(posted_days.values())
        ordered = sorted(posted_days.items(), key=lambda kv: -kv[1])
        top1 = ordered[0][1] if ordered else 0
        top3 = sum(n for _, n in ordered[:3])

        result = {
            "run_id": run.run_id,
            "docket_id": docket_id,
            "comments": len(rows),
            "dated_pairs": len(lags),
            "missing_receive_date": missing_receive,
            "missing_posted_date": missing_posted,
            "unparseable_dates": unparseable,
            "lag_median_days": _median(lags),
            "lag_p90_days": _p90(lags),
            "receive_days": len(receive_days),
            "posting_days": len(posted_days),
            "top_day_share_pct": round(100.0 * top1 / total_posted_dated, 1)
                                 if total_posted_dated else None,
            "top3_day_share_pct": round(100.0 * top3 / total_posted_dated, 1)
                                  if total_posted_dated else None,
        }

        conn.execute(
            """INSERT INTO posting_rhythm
               (run_id, docket_id, dated_pairs, missing_receive, missing_posted,
                lag_median_days, lag_p90_days, receive_days, posting_days,
                top_day_share, top3_day_share)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (run.run_id, docket_id, len(lags), missing_receive, missing_posted,
             result["lag_median_days"], result["lag_p90_days"],
             len(receive_days), len(posted_days),
             result["top_day_share_pct"], result["top3_day_share_pct"]),
        )
        for pd, n in sorted(posted_days.items()):
            conn.execute(
                """INSERT INTO posting_batches (run_id, docket_id, posted_date, n)
                   VALUES (?,?,?,?)""",
                (run.run_id, docket_id, pd, n),
            )
        conn.commit()
    return result


# ---------------------------------------------------------------------------
# Module two, the cost of auditing the record
# ---------------------------------------------------------------------------

def audit_cost(conn: sqlite3.Connection, docket_id: str) -> dict:
    """
    Measure what auditing this docket cost, from the instrument's own request
    log. Computes from the log as it stands, never backfills, and reports a gap
    as a gap. The measured rate describes this instrument under one key and the
    published limits, not every possible auditor, and agencies or their
    contractors with internal access face none of these constraints.
    """
    params = {"docket_id": docket_id, "extrapolation": "measured rate"}
    with Run(conn, MODULE_AUDIT, params, docket_id=docket_id) as run:
        reqs = conn.execute(
            """SELECT r.url, r.ok, r.http_status
               FROM requests r JOIN runs u ON u.run_id = r.run_id
               WHERE u.docket_id = ? AND u.module IN ('collect','collect_details')""",
            (docket_id,),
        ).fetchall()

        listing = detail = other = failed = limited = 0
        for r in reqs:
            url = r["url"]
            if "/comments/" in url:
                detail += 1
            elif "/comments?" in url or "/documents?" in url:
                listing += 1
            else:
                other += 1
            if not r["ok"]:
                failed += 1
            if r["http_status"] == 429:
                limited += 1

        stored = conn.execute(
            "SELECT COUNT(*) FROM comments WHERE docket_id=?", (docket_id,)
        ).fetchone()[0]

        elapsed_total = 0.0
        detail_elapsed = 0.0
        detail_reqs_in_detail_runs = 0
        for u in conn.execute(
            """SELECT run_id, module, started_utc, finished_utc FROM runs
               WHERE docket_id=? AND module IN ('collect','collect_details')
               AND finished_utc IS NOT NULL""",
            (docket_id,),
        ).fetchall():
            e = _elapsed_seconds(u["started_utc"], u["finished_utc"]) or 0.0
            elapsed_total += e
            if u["module"] == "collect_details":
                detail_elapsed += e
                detail_reqs_in_detail_runs += conn.execute(
                    "SELECT COUNT(*) FROM requests WHERE run_id=?", (u["run_id"],)
                ).fetchone()[0]

        measured = (detail_elapsed / detail_reqs_in_detail_runs
                    if detail_reqs_in_detail_runs and detail_elapsed > 0 else None)

        posted = conn.execute(
            "SELECT posted_count FROM retrieval_status WHERE docket_id=?", (docket_id,)
        ).fetchone()
        posted_count = posted["posted_count"] if posted else None

        full_hours = (round(posted_count * measured / 3600, 1)
                      if posted_count and measured else None)

        # Windows are derived from the log rather than from the cursors table,
        # which holds only the final resume point. Every window after the first
        # issued listing requests carrying a lastModifiedDate cursor in the URL,
        # so the count of distinct cursor values among logged listing URLs is
        # the count of window advances, and windows consumed is that plus one
        # when any listing ran at all.
        cursor_values = set()
        listing_ran = False
        for row in conn.execute(
            """SELECT r.url FROM requests r JOIN runs u ON u.run_id = r.run_id
               WHERE u.docket_id = ? AND u.module = 'collect'
               AND r.url LIKE '%comments%'""",
            (docket_id,),
        ):
            listing_ran = True
            url = row["url"]
            marker = "lastModifiedDate%5D%5Bge%5D="
            if marker in url:
                cursor_values.add(url.split(marker, 1)[1].split("&", 1)[0])
        windows = (len(cursor_values) + 1) if listing_ran else 0

        result = {
            "run_id": run.run_id,
            "docket_id": docket_id,
            "listing_requests": listing,
            "detail_requests": detail,
            "other_requests": other,
            "failed_requests": failed,
            "rate_limit_hits": limited,
            "comments_stored": stored,
            "requests_per_comment": round((listing + detail + other) / stored, 2)
                                    if stored else None,
            "elapsed_seconds": round(elapsed_total, 1),
            "measured_seconds_per_detail": round(measured, 2) if measured else None,
            "cursor_windows_recorded": windows,
            "posted_count": posted_count,
            "full_audit_hours_measured": full_hours,
            "timing_gap": measured is None,
        }
        if measured is None:
            result["timing_note"] = (
                "The log does not carry enough elapsed detail time to establish a "
                "measured rate, so no extrapolation is reported. A gap is a gap."
            )

        conn.execute(
            """INSERT INTO audit_costs
               (run_id, docket_id, listing_requests, detail_requests, other_requests,
                failed_requests, rate_limit_hits, comments_stored, requests_per_comment,
                elapsed_seconds, measured_seconds_per_detail, cursor_windows_recorded,
                posted_count, full_audit_hours_measured)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run.run_id, docket_id, listing, detail, other, failed, limited, stored,
             result["requests_per_comment"], result["elapsed_seconds"],
             result["measured_seconds_per_detail"], windows, posted_count, full_hours),
        )
        conn.commit()
    return result


# ---------------------------------------------------------------------------
# Module three, the aggregation channel
# ---------------------------------------------------------------------------

def aggregation(
    conn: sqlite3.Connection,
    docket_id: str,
    collector=None,
    enrich: bool = False,
) -> dict:
    """
    Measure how much participation arrives compressed into single posted
    records, from the declaration field on detail records already collected.

    Absence of the declaration is an absent declaration and never a declaration
    of one. Declared multipliers are self reports the record offers no way to
    audit. The optional enrichment, off by default, issues one logged request
    for the docket detail record, stores it verbatim, and fills the agency
    received count in retrieval_status only where none is recorded, reporting a
    disagreement rather than resolving one where a count already exists.
    """
    params = {
        "docket_id": docket_id,
        "declaration_fields": list(DECLARATION_FIELDS),
        "enrich": bool(enrich),
    }
    with Run(conn, MODULE_AGGREGATION, params, docket_id=docket_id) as run:
        docket_detail_received = None
        received_disagreement = None
        if enrich and collector is not None:
            url = f"{collector.cfg.api_base}/dockets/{docket_id}"
            resp = collector._fetch(run.run_id, url)
            if resp.ok:
                try:
                    attrs = (json.loads(resp.body).get("data", {}) or {}).get(
                        "attributes", {}) or {}
                except json.JSONDecodeError:
                    attrs = {}
                for key, value in attrs.items():
                    if "comment" in key.lower() and isinstance(value, int):
                        docket_detail_received = value
                        break
                if docket_detail_received is not None:
                    row = conn.execute(
                        "SELECT received_count FROM retrieval_status WHERE docket_id=?",
                        (docket_id,),
                    ).fetchone()
                    existing = row["received_count"] if row else None
                    if existing is None:
                        conn.execute(
                            """UPDATE retrieval_status SET received_count=?,
                               received_source='docket detail endpoint', as_of_utc=?
                               WHERE docket_id=?""",
                            (docket_detail_received, utc_now(), docket_id),
                        )
                    elif existing != docket_detail_received:
                        received_disagreement = {
                            "recorded": existing,
                            "docket_endpoint": docket_detail_received,
                        }

        rows = conn.execute(
            """SELECT d.comment_id, d.raw_json FROM comment_details d
               JOIN comments c ON c.comment_id = d.comment_id
               WHERE c.docket_id = ?""",
            (docket_id,),
        ).fetchall()

        no_declaration = 0
        declared_single = 0
        unreadable = 0
        multipliers: list[int] = []
        for r in rows:
            try:
                attrs = (json.loads(r["raw_json"]) or {}).get("attributes", {}) or {}
            except (json.JSONDecodeError, AttributeError):
                # A stored detail that cannot be parsed is an unreadable record,
                # counted and reported, and it also fails the integrity check,
                # which is the tool for investigating how it got that way.
                unreadable += 1
                no_declaration += 1
                continue
            value = None
            for field_name in DECLARATION_FIELDS:
                candidate = attrs.get(field_name)
                if isinstance(candidate, bool) or not isinstance(candidate, int):
                    continue
                if value is None or candidate > value:
                    value = candidate
            if value is None or value < 1:
                no_declaration += 1
            elif value == 1:
                declared_single += 1
            else:
                multipliers.append(value)
                conn.execute(
                    """INSERT INTO aggregated_records (run_id, comment_id, declared_items)
                       VALUES (?,?,?)""",
                    (run.run_id, r["comment_id"], value),
                )

        status = conn.execute(
            "SELECT posted_count, received_count FROM retrieval_status WHERE docket_id=?",
            (docket_id,),
        ).fetchone()
        posted_count = status["posted_count"] if status else None
        received_count = status["received_count"] if status else None
        gap = (received_count - posted_count
               if received_count is not None and posted_count is not None else None)
        # Each aggregated record already counts once among the posted, so the
        # submissions it adds to the received side beyond itself are value - 1.
        explained = sum(m - 1 for m in multipliers) if multipliers else 0

        result = {
            "run_id": run.run_id,
            "docket_id": docket_id,
            "details_available": len(rows),
            "no_declaration": no_declaration,
            "unreadable_details": unreadable,
            "declared_single": declared_single,
            "aggregated_records": len(multipliers),
            "aggregation_rate_pct": round(100.0 * len(multipliers) / len(rows), 2)
                                    if rows else None,
            "declared_total": sum(multipliers),
            "multiplier_median": _median(multipliers),
            "multiplier_max": max(multipliers) if multipliers else None,
            "posted_count": posted_count,
            "received_count": received_count,
            "received_minus_posted": gap,
            "explained_by_aggregation": explained if gap is not None else None,
            "explained_share_pct": round(100.0 * explained / gap, 1)
                                   if gap else None,
            "docket_detail_received": docket_detail_received,
            "received_disagreement": received_disagreement,
        }

        conn.execute(
            """INSERT INTO aggregation_profiles
               (run_id, docket_id, details_available, no_declaration, declared_single,
                aggregated_records, declared_total, multiplier_median, multiplier_max,
                posted_count, received_count, received_minus_posted,
                explained_by_aggregation)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run.run_id, docket_id, len(rows), no_declaration, declared_single,
             len(multipliers), sum(multipliers), result["multiplier_median"],
             result["multiplier_max"], posted_count, received_count, gap,
             result["explained_by_aggregation"]),
        )
        conn.commit()
    return result
