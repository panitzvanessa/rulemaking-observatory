#!/usr/bin/env python3
"""
Rulemaking Observatory, command line entry point.

Everything the instrument does, reachable from a normal terminal, so that no
step requires the interactive Python prompt.

    python run.py check
    python run.py collect WHD-2026-0001
    python run.py details WHD-2026-0001 --limit 50
    python run.py extract WHD-2026-0001
    python run.py detect  WHD-2026-0001
    python run.py status
    python run.py all     WHD-2026-0001 --limit 50

The key is read from the REGS_API_KEY environment variable, or passed with
--key. The working directory is read from OBSERVATORY_ROOT, or passed with
--root, and defaults to a data folder beside this file.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Comment text carries characters outside the Windows legacy code page, the
# subscript two of CO2 and the Greek mu of microgram among them, and this module
# prints excerpts of it. On Windows the console itself handles those, but a
# redirected or piped stream falls back to the locale encoding and would raise
# UnicodeEncodeError, so the streams are pinned to UTF-8 before anything prints.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # already wrapped, or not reconfigurable
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

from observatory import (  # noqa: E402
    Collector,
    Config,
    HttpTransport,
    connect,
    detect,
    extract_field_texts,
    init_db,
    integrity_check,
    seed,
)
from observatory.core import DESIGN_DOCKETS, register_docket  # noqa: E402

# Declared once, in observatory/core.py, so agency and era cannot drift between
# the library and this entry point.
KNOWN_DOCKETS = DESIGN_DOCKETS


def show(label: str, payload) -> None:
    print(f"\n{label}")
    if isinstance(payload, dict):
        width = max(len(k) for k in payload) if payload else 0
        for k, v in payload.items():
            print(f"  {k.ljust(width)}  {v}")
    else:
        print(f"  {payload}")


def build_config(args) -> Config:
    root = args.root or os.environ.get("OBSERVATORY_ROOT")
    if not root:
        root = str(Path(__file__).resolve().parent / "data")
    cfg = Config(root=Path(root).expanduser())
    cfg.api_key = args.key or os.environ.get("REGS_API_KEY")
    archive = os.environ.get("OBSERVATORY_ARCHIVE_DB")
    if archive:
        cfg.archive_db = Path(archive).expanduser()
    return cfg.ensure()


def open_db(cfg: Config):
    conn = connect(cfg)
    init_db(conn)
    seed(conn)
    return conn


def with_db(fn):
    """
    Open one connection, hand it to the command, and always close it.

    Closing matters on Windows, where an open handle keeps the database and its
    write ahead log files locked, so a chain of commands that each left a
    connection open could fail with a lock error or leave a folder that cannot
    be deleted. One invocation, one connection, closed even when the command
    raises.
    """
    def wrapper(args):
        cfg = build_config(args)
        conn = open_db(cfg)
        try:
            return fn(args, conn, cfg)
        finally:
            conn.close()
    return wrapper


def ensure_docket(conn, docket_id: str) -> None:
    agency, era, note = KNOWN_DOCKETS.get(docket_id, (None, None, None))
    register_docket(conn, docket_id, agency=agency, era=era, rulemaking_note=note)


def require_collected_docket(conn, docket_id: str) -> None:
    """
    The analysis commands read what collection stored, so a docket that was
    never registered, a typo among them, must produce a clear sentence rather
    than a foreign key traceback from inside a run registration.
    """
    row = conn.execute(
        "SELECT 1 FROM dockets WHERE docket_id=?", (docket_id,)
    ).fetchone()
    if row is None:
        known = ", ".join(sorted(KNOWN_DOCKETS))
        sys.exit(
            f"Docket {docket_id} is not in the database. Check the spelling, "
            f"or collect it first with python run.py collect {docket_id}. "
            f"Known design dockets, {known}."
        )


def collector_for(conn, cfg: Config) -> Collector:
    if not cfg.api_key:
        sys.exit(
            "No API key. Set REGS_API_KEY in the environment or pass --key.\n"
            "Request a free key at https://api.data.gov/signup/"
        )
    return Collector(conn, cfg, HttpTransport(api_key=cfg.api_key))


# ---------------------------------------------------------------- commands

def _check(args, conn, cfg) -> None:
    show("Environment", {
        "database": cfg.db_path,
        "api key loaded": "yes" if cfg.api_key else "NO, collection will not run",
        "archive database": cfg.archive_db or "not set",
    })
    counts = {
        "dockets": conn.execute("SELECT COUNT(*) FROM dockets").fetchone()[0],
        "comments": conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0],
        "documents": conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
        "detail records": conn.execute("SELECT COUNT(*) FROM comment_details").fetchone()[0],
        "texts": conn.execute("SELECT COUNT(*) FROM texts").fetchone()[0],
        "requests logged": conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0],
        "artifacts on file": conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0],
        "corrections logged": conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0],
    }
    show("Database", counts)
    show("Integrity", integrity_check(conn))
    print("\nAll good. Next step, python run.py collect WHD-2026-0001")


def _collect(args, conn, cfg) -> None:
    ensure_docket(conn, args.docket)
    print(f"Listing comments for {args.docket}. This is the cheap stage, "
          f"a few requests.")
    show("Collection", collector_for(conn, cfg).collect_docket(args.docket))
    print(f"\nNext step, python run.py details {args.docket} --limit 50")


def _details(args, conn, cfg) -> None:
    ensure_docket(conn, args.docket)
    pending = conn.execute(
        """SELECT COUNT(*) FROM comments c
           LEFT JOIN comment_details d ON d.comment_id = c.comment_id
           WHERE c.docket_id=? AND d.comment_id IS NULL""",
        (args.docket,),
    ).fetchone()[0]
    if not pending:
        print("Nothing pending. Every stored comment already has its detail record.")
        return
    n = min(pending, args.limit) if args.limit else pending
    print(f"{pending} comments still need their detail record, requesting {n}.")
    est = n * 3.7
    human = (f"about {est / 60:.0f} minutes" if est < 5400
             else f"about {est / 3600:.1f} hours")
    print(f"This is the expensive stage, one request per comment, {human}. "
          f"Interrupting is safe, it resumes.")
    show("Details", collector_for(conn, cfg).collect_details(
        args.docket, limit=args.limit, attachments=args.attachments))
    print(f"\nNext step, python run.py extract {args.docket}")


def _extract(args, conn, cfg) -> None:
    result = extract_field_texts(conn, args.docket)
    show("Extraction", result)
    if not result["texts_in_corpus"]:
        print(f"\nNothing to extract, no comments stored for this docket. "
              f"Run python run.py collect {args.docket} first.")
    elif result.get("text_missing"):
        print(f"\nThis corpus is a listing and not a text corpus. "
              f"Run python run.py details {args.docket} first.")
    else:
        print(f"\nNext step, python run.py detect {args.docket}")


def _detect(args, conn, cfg) -> None:
    result = detect(conn, args.docket, min_cluster=args.min_cluster)
    show("Detection", result)
    rows = conn.execute(
        """SELECT local_index, kind, size, core_excerpt FROM clusters
           WHERE run_id=? ORDER BY size DESC LIMIT 10""",
        (result["run_id"],),
    ).fetchall()
    if rows:
        print("\nLargest clusters")
        for r in rows:
            print(f"  cluster {r['local_index']:>3}  {r['size']:>6} texts  "
                  f"{r['kind']:<5}  {(r['core_excerpt'] or '')[:70]}")


def _status(args, conn, cfg) -> None:
    rows = conn.execute("SELECT * FROM v_retrieval_share ORDER BY docket_id").fetchall()
    if not rows:
        print("No dockets collected yet.")
        return
    print(f"{'docket':<24}{'era':<10}{'stored':>8}{'posted':>9}{'texts':>8}"
          f"{'details':>9}  sampling")
    for r in rows:
        det = conn.execute(
            """SELECT COUNT(*) FROM comment_details d JOIN comments c
               ON c.comment_id=d.comment_id WHERE c.docket_id=?""",
            (r["docket_id"],),
        ).fetchone()[0]
        print(f"{r['docket_id']:<24}{(r['era'] or '?'):<10}"
              f"{r['comments_stored']:>8}{(r['posted_count'] or 0):>9}"
              f"{r['texts_extracted']:>8}{det:>9}  {r['sampling']}")
    print("\nStored counts describe the retrieved portion, not the corpus.")


def _all(args, conn, cfg) -> None:
    _collect(args, conn, cfg)
    _details(args, conn, cfg)
    _extract(args, conn, cfg)
    _detect(args, conn, cfg)


def _rhythm(args, conn, cfg) -> None:
    require_collected_docket(conn, args.docket)
    from observatory import analysis_report, posting_rhythm
    from observatory.report import Epistemics
    r = posting_rhythm(conn, args.docket)
    show("Posting rhythm", r)
    batches = conn.execute(
        """SELECT posted_date, n FROM posting_batches WHERE run_id=?
           ORDER BY n DESC LIMIT 10""", (r["run_id"],),
    ).fetchall()
    body = [
        "The artifacts registry records that posted dates reconstruct agency "
        "posting batches rather than submission behaviour. That guard inverts "
        "here, posted date is the correct field because the agency's workflow "
        "is the object of study.",
        f"{r['dated_pairs']} comments carry both dates, {r['missing_receive_date']} "
        f"lack a receive date, {r['missing_posted_date']} lack a posted date"
        + (f", and {r['unparseable_dates']} carry dates that do not parse and are "
           f"counted here rather than dropped silently." if r["unparseable_dates"]
           else "."),
        f"Median receive to post lag {r['lag_median_days']} days, ninetieth "
        f"percentile {r['lag_p90_days']} days.",
        f"Received across {r['receive_days']} days, posted across "
        f"{r['posting_days']} days, largest posting day carries "
        f"{r['top_day_share_pct']} percent and the top three carry "
        f"{r['top3_day_share_pct']} percent of dated records.",
    ]
    for b in batches:
        body.append(f"  {b['posted_date']}  {b['n']}")
    print()
    print(analysis_report(
        conn, r["run_id"], "Posting rhythm report",
        [("Rhythm", body)],
        Epistemics(
            show=[
                f"The agency posted this docket across {r['posting_days']} days "
                f"with a median lag of {r['lag_median_days']} days after receipt."
            ],
            consistent_with=[
                "Batched internal processing, whether by staff, contractor, or "
                "automated pipeline, which day level data cannot decompose.",
            ],
            cannot_show=[
                "Anything finer than day level, since the record exposes no "
                "sub day timestamp.",
                "The cause of any rhythm shift, which is consistent with "
                "automation but also with staffing, volume, and policy.",
                "Submitter behaviour, which posted dates do not describe.",
            ],
        ),
    ))


def _audit(args, conn, cfg) -> None:
    require_collected_docket(conn, args.docket)
    from observatory import analysis_report, audit_cost
    from observatory.report import Epistemics
    r = audit_cost(conn, args.docket)
    show("Audit cost", r)
    body = [
        f"{r['listing_requests']} listing requests, {r['detail_requests']} detail "
        f"requests, {r['failed_requests']} failed, {r['rate_limit_hits']} rate "
        f"limited, for {r['comments_stored']} stored comments, "
        f"{r['requests_per_comment']} requests per comment.",
        f"Elapsed collection time {r['elapsed_seconds']} seconds.",
    ]
    if r["full_audit_hours_measured"] is not None:
        body.append(
            f"At the measured rate of {r['measured_seconds_per_detail']} seconds "
            f"per detail record, a full text audit of the {r['posted_count']} "
            f"posted comments costs about {r['full_audit_hours_measured']} hours."
        )
    else:
        body.append(r.get("timing_note", "No measured rate available."))
    body.append(
        "One aggregated submission can enter this record in a single request. "
        "Reading the record back costs the public the request counts above, "
        "under a cap of five thousand records per query and a shared limit of "
        "one thousand requests an hour."
    )
    print()
    print(analysis_report(
        conn, r["run_id"], "Audit cost report",
        [("Cost of reading the record", body)],
        Epistemics(
            show=[
                f"Assembling this corpus took {r['listing_requests'] + r['detail_requests'] + r['other_requests']} "
                f"logged requests under the published limits."
            ],
            cannot_show=[
                "The cost to any auditor other than this instrument under one "
                "key, and agencies or contractors with internal access face "
                "none of these constraints, which is the boundary and not a "
                "finding about their capabilities.",
                "Costs the log does not contain, a gap is a gap.",
            ],
        ),
    ))


def _aggregation(args, conn, cfg) -> None:
    require_collected_docket(conn, args.docket)
    from observatory import aggregation, analysis_report
    from observatory.report import Epistemics
    collector = None
    if getattr(args, "enrich", False):
        collector = collector_for(conn, cfg)
    r = aggregation(conn, args.docket, collector=collector,
                    enrich=getattr(args, "enrich", False))
    show("Aggregation", r)
    if r["details_available"]:
        body = [
            f"Of {r['details_available']} posted records with a detail on file, "
            f"{r['aggregated_records']} declare representing more than one "
            f"submission ({r['aggregation_rate_pct']} percent), "
            f"{r['declared_single']} declare one, and {r['no_declaration']} carry "
            f"no declaration, which is an absent declaration and not a one.",
        ]
    else:
        body = [
            "No detail records are on file for this docket yet, so the "
            "aggregation channel cannot be measured. Run the details stage first.",
        ]
    if r["aggregated_records"]:
        body.append(
            f"Aggregated records declare {r['declared_total']} submissions in "
            f"total, median multiplier {r['multiplier_median']}, maximum "
            f"{r['multiplier_max']}."
        )
    if r["received_minus_posted"] is not None:
        body.append(
            f"The agency reports {r['received_count']} received against "
            f"{r['posted_count']} posted, a gap of {r['received_minus_posted']}, "
            f"of which declared aggregation accounts for "
            f"{r['explained_by_aggregation']}"
            + (f" ({r['explained_share_pct']} percent)." if r["explained_share_pct"] is not None else ".")
        )
    if r["received_disagreement"]:
        body.append(
            f"Two received totals disagree, {r['received_disagreement']['recorded']} "
            f"on file against {r['received_disagreement']['docket_endpoint']} from "
            f"the docket endpoint. Both are kept with their sources."
        )
    print()
    print(analysis_report(
        conn, r["run_id"], "Aggregation channel report",
        [("The channel", body)],
        Epistemics(
            show=[
                f"{r['aggregation_rate_pct']} percent of posted records with "
                f"details declare standing for multiple submissions."
                if r["aggregation_rate_pct"] is not None else
                "No detail records are available yet for this docket."
            ],
            cannot_show=[
                "Whether declared multipliers are accurate, they are self "
                "reports the record offers no way to audit.",
                "Aggregation inside the never posted remainder of the record.",
                "How any submission arrived, since the record exposes no "
                "submission method field.",
            ],
        ),
    ))


# Public entry points, each wrapped so the connection is opened once and closed.
cmd_check = with_db(_check)
cmd_collect = with_db(_collect)
cmd_details = with_db(_details)
cmd_extract = with_db(_extract)
cmd_detect = with_db(_detect)
cmd_status = with_db(_status)
cmd_all = with_db(_all)
cmd_rhythm = with_db(_rhythm)
cmd_audit = with_db(_audit)
cmd_aggregation = with_db(_aggregation)


# ---------------------------------------------------------------- main

def main() -> None:
    p = argparse.ArgumentParser(
        prog="run.py",
        description="Rulemaking Observatory, phase zero.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--root", help="working directory for the database")
    p.add_argument("--key", help="Regulations.gov API key, overrides REGS_API_KEY")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="verify the environment and the database").set_defaults(
        func=cmd_check)
    sub.add_parser("status", help="what has been collected so far").set_defaults(
        func=cmd_status)

    c = sub.add_parser("collect", help="retrieve comment listings for a docket")
    c.add_argument("docket")
    c.set_defaults(func=cmd_collect)

    d = sub.add_parser("details", help="retrieve the detail record of each comment")
    d.add_argument("docket")
    d.add_argument("--limit", type=int, default=None,
                   help="stop after this many requests, use a small number first")
    d.add_argument("--attachments", action="store_true",
                   help="include attachment metadata in the detail request")
    d.set_defaults(func=cmd_details)

    e = sub.add_parser("extract", help="turn stored records into analysable texts")
    e.add_argument("docket")
    e.set_defaults(func=cmd_extract)

    t = sub.add_parser("detect", help="find coordinated commenting campaigns")
    t.add_argument("docket")
    t.add_argument("--min-cluster", type=int, default=3)
    t.set_defaults(func=cmd_detect)

    r = sub.add_parser("rhythm", help="measure the agency's posting rhythm")
    r.add_argument("docket")
    r.set_defaults(func=cmd_rhythm)

    u = sub.add_parser("audit", help="measure what auditing this docket cost, from the request log")
    u.add_argument("docket")
    u.set_defaults(func=cmd_audit)

    g = sub.add_parser("aggregation", help="measure submissions compressed into single posted records")
    g.add_argument("docket")
    g.add_argument("--enrich", action="store_true",
                   help="one logged request for the docket detail record, off by default")
    g.set_defaults(func=cmd_aggregation)

    a = sub.add_parser("all", help="collect, details, extract, detect in sequence")
    a.add_argument("docket")
    a.add_argument("--limit", type=int, default=None)
    a.add_argument("--attachments", action="store_true")
    a.add_argument("--min-cluster", type=int, default=3)
    a.set_defaults(func=cmd_all)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
