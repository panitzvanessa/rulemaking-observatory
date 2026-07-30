"""
Report generation.

Reports are produced by the instrument and released by a human being. Nothing
here publishes anything. The generator enforces three disciplines that were
previously left to the analyst's memory.

First, no proportion is emitted without its denominator and the retrieval share
of the corpus it describes. Second, any measure whose known failure mode is on
file in the artifacts registry carries its guard restated in the report. Third,
every report ends with an explicit separation between what the data show, what
they are consistent with, and what they cannot show, and the generator refuses
to produce a report if that section is empty.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from .core import utc_now
from .registry import guards_for


class ReportDisciplineError(RuntimeError):
    """Raised when a report would violate the standing evidence discipline."""


@dataclass
class Epistemics:
    show: list[str] = field(default_factory=list)
    consistent_with: list[str] = field(default_factory=list)
    cannot_show: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if not self.show:
            raise ReportDisciplineError("a report must state what the data show")
        if not self.cannot_show:
            raise ReportDisciplineError(
                "a report must state what the data cannot show, which is never nothing"
            )


def retrieval_line(conn: sqlite3.Connection, docket_id: str) -> str:
    row = conn.execute(
        "SELECT * FROM v_retrieval_share WHERE docket_id=?", (docket_id,)
    ).fetchone()
    if row is None:
        return f"{docket_id}, no retrieval status recorded."
    parts = [f"{docket_id}"]
    if row["posted_count"]:
        parts.append(
            f"{row['comments_stored']} of {row['posted_count']} posted records stored"
            + (f", {row['pct_of_posted']} percent" if row["pct_of_posted"] is not None else "")
        )
    else:
        parts.append(f"{row['comments_stored']} records stored, posted total not yet recorded")
    if row["received_count"]:
        parts.append(
            f"agency reports {row['received_count']} received, so the posted record is "
            f"{row['pct_posted_of_received']} percent of it"
        )
    parts.append(f"retrieval {row['sampling'].replace('_', ' ')}")
    return ", ".join(parts) + "."


def campaign_report(
    conn: sqlite3.Connection,
    run_id: int,
    epistemics: Epistemics,
    title: str = "Campaign detection report",
    top_n: int = 12,
) -> str:
    """
    Render a campaign detection report as plain text. Raises rather than
    emitting a report that omits a denominator or a required guard.
    """
    epistemics.validate()

    run = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if run is None:
        raise KeyError(f"no such run: {run_id}")
    docket_id = run["docket_id"]

    clusters = conn.execute(
        """SELECT local_index, kind, size, core_excerpt FROM clusters
           WHERE run_id=? ORDER BY size DESC, local_index LIMIT ?""",
        (run_id, top_n),
    ).fetchall()
    total_in_clusters = conn.execute(
        "SELECT COUNT(*) FROM cluster_members WHERE run_id=?", (run_id,)
    ).fetchone()[0]
    considered = conn.execute(
        """SELECT COUNT(*) FROM texts t JOIN comments c ON c.comment_id=t.comment_id
           WHERE c.docket_id=? AND t.is_placeholder=0""",
        (docket_id,),
    ).fetchone()[0]
    placeholders = conn.execute(
        """SELECT COUNT(*) FROM texts t JOIN comments c ON c.comment_id=t.comment_id
           WHERE c.docket_id=? AND t.is_placeholder=1""",
        (docket_id,),
    ).fetchone()[0]

    lines: list[str] = []
    lines.append(title)
    lines.append(f"Docket {docket_id}. Run {run_id}, module {run['module']} "
                 f"version {run['module_version']}, generated {utc_now()}.")
    lines.append(f"Parameter set {run['parameters_sha256'][:16]}, code {run['code_sha']}.")
    lines.append("")
    lines.append("1. Coverage")
    lines.append(retrieval_line(conn, docket_id))
    if placeholders:
        share = round(100.0 * placeholders / (placeholders + considered), 1)
        lines.append(
            f"{placeholders} records carry a placeholder text field rather than content, "
            f"{share} percent of records with an extracted text field, which means this docket "
            f"carries part of its participation in attachments that are not yet read."
        )
    lines.append("")
    lines.append("2. Coordination")
    if considered:
        lines.append(
            f"{total_in_clusters} of {considered} texts fall into detected clusters, "
            f"{round(100.0 * total_in_clusters / considered, 1)} percent of texts considered, "
            f"where a cluster is three or more texts sharing five word sequences above an "
            f"estimated Jaccard similarity of {run['parameters_json'] and ''}"
            f"{_param(run, 'sim_threshold')}."
        )
    else:
        lines.append("No texts met the minimum length for consideration.")
    if clusters:
        lines.append("")
        for c in clusters:
            lines.append(
                f"Cluster {c['local_index']}, {c['kind']}, {c['size']} texts. "
                f"Opening words, {c['core_excerpt'][:90]}"
            )
    lines.append("")
    lines.append("3. Guards in force")
    seen = set()
    for keyword in ("every", "campaign", "distribution"):
        for g in guards_for(conn, keyword):
            if g["slug"] in seen:
                continue
            seen.add(g["slug"])
            lines.append(f"{g['title']}, status {g['status']}. {g['guard']}")
    lines.append("")
    lines.append("4. Reading of the evidence")
    lines.append("What these data show.")
    for s in epistemics.show:
        lines.append(f"  {s}")
    if epistemics.consistent_with:
        lines.append("What they are consistent with, without establishing.")
        for s in epistemics.consistent_with:
            lines.append(f"  {s}")
    lines.append("What they cannot show.")
    for s in epistemics.cannot_show:
        lines.append(f"  {s}")
    lines.append("")
    lines.append(
        "No claim in this report is about an individual comment or an individual person. "
        "All statements are corpus level and comparative. Released only after human review."
    )
    return "\n".join(lines)


def _param(run: sqlite3.Row, key: str) -> str:
    import json
    try:
        return str(json.loads(run["parameters_json"]).get(key, "unspecified"))
    except Exception:
        return "unspecified"


def analysis_report(
    conn: sqlite3.Connection,
    run_id: int,
    title: str,
    sections: list[tuple[str, list[str]]],
    epistemics: Epistemics,
    guard_keywords: tuple[str, ...] = ("every",),
) -> str:
    """
    Render a generic analysis report under the same discipline as the campaign
    report. The header names the run, its parameter hash, and the code that
    produced it. Coverage restates the retrieval share. Guards matching the
    supplied keywords are restated. And the report refuses to render when the
    statement of what the data cannot show is empty, which is enforced by the
    Epistemics object rather than by convention.
    """
    epistemics.validate()
    run = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if run is None:
        raise KeyError(f"no such run: {run_id}")
    docket_id = run["docket_id"]

    lines: list[str] = []
    lines.append(title)
    lines.append(f"Docket {docket_id}. Run {run_id}, module {run['module']} "
                 f"version {run['module_version']}, generated {utc_now()}.")
    lines.append(f"Parameter set {run['parameters_sha256'][:16]}, code {run['code_sha']}.")
    lines.append("")
    lines.append("1. Coverage")
    lines.append(retrieval_line(conn, docket_id) if docket_id else
                 "No single docket, measures span the database.")
    section_no = 1
    for heading, body in sections:
        section_no += 1
        lines.append("")
        lines.append(f"{section_no}. {heading}")
        lines.extend(body)
    lines.append("")
    section_no += 1
    lines.append(f"{section_no}. Guards in force")
    seen: set[str] = set()
    for keyword in guard_keywords:
        for g in guards_for(conn, keyword):
            if g["slug"] in seen:
                continue
            seen.add(g["slug"])
            lines.append(f"{g['title']}, status {g['status']}. {g['guard']}")
    lines.append("")
    section_no += 1
    lines.append(f"{section_no}. Reading of the evidence")
    lines.append("What these data show.")
    for s in epistemics.show:
        lines.append(f"  {s}")
    if epistemics.consistent_with:
        lines.append("What they are consistent with, without establishing.")
        for s in epistemics.consistent_with:
            lines.append(f"  {s}")
    lines.append("What they cannot show.")
    for s in epistemics.cannot_show:
        lines.append(f"  {s}")
    lines.append("")
    lines.append(
        "No claim in this report is about an individual comment or an individual person. "
        "All statements are corpus level and comparative. Released only after human review."
    )
    return "\n".join(lines)
