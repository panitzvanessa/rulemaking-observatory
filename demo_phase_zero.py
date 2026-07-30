"""
Phase zero demonstration.

Runs the whole chain offline against a fixture corpus, then prints a generated
report and a provenance trace. This is the acceptance test for Phase zero, and
it is meant to be run by a human being who then reads the output.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "tests"))

from observatory import (
    Collector, Config, Epistemics, FixtureTransport, campaign_report, connect,
    detect, extract_field_texts, init_db, integrity_check, seed, trace_run,
)
from observatory.core import register_docket
from test_phase_zero import DOCKET, PROPOSED_RULE, _fixture_pages


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="observatory_demo_"))
    cfg = Config(root=tmp)
    conn = connect(cfg)
    init_db(conn)
    print(f"Database initialised at {cfg.db_path}")
    print("Registry seeded:", seed(conn))

    register_docket(
        conn, DOCKET, agency="TEST", title="Fixture docket, phase zero demonstration",
        era="post", design_role="acceptance test",
    )
    conn.execute(
        """UPDATE retrieval_status SET posted_count=24, received_count=400,
           received_source='fixture final rule preamble' WHERE docket_id=?""",
        (DOCKET,),
    )
    conn.execute(
        """INSERT INTO rule_texts (docket_id, stage, fr_document, content,
           content_sha256, source_url, retrieved_utc)
           VALUES (?,?,?,?,?,?,datetime('now'))""",
        (DOCKET, "proposed", "FR-DEMO-1", PROPOSED_RULE, "demo", "http://example.test"),
    )
    conn.commit()

    pages = _fixture_pages(cfg)
    collected = Collector(conn, cfg, FixtureTransport(pages)).collect_docket(DOCKET)
    print("Collection:", json.dumps(collected))

    details = Collector(conn, cfg, FixtureTransport(pages)).collect_details(DOCKET)
    print("Details:", json.dumps(details))

    extracted = extract_field_texts(conn, DOCKET)
    print("Extraction:", json.dumps(extracted))

    found = detect(conn, DOCKET)
    print("Detection:", json.dumps(found))

    print("Integrity:", json.dumps(integrity_check(conn)))

    print()
    print("=" * 78)
    print(campaign_report(
        conn,
        found["run_id"],
        Epistemics(
            show=[
                "Ten of the sixteen texts considered fall into a single cluster built on one "
                "exact core with four personalised variants, and a further three form a "
                "separate coordinated batch.",
                "Four records carry a placeholder text field rather than content.",
            ],
            consistent_with=[
                "A sponsor circulating a template with a personalisation slot, and a small "
                "separate group coordinating without organisational infrastructure.",
            ],
            cannot_show=[
                "Who drafted any individual comment, or whether any drafting tool was used.",
                "The composition of the received record, which on this docket is roughly six "
                "times the posted record.",
                "Anything about submission timing finer than the day, since the interface "
                "exposes no sub day timestamp.",
            ],
        ),
        title="Campaign detection report, phase zero demonstration",
    ))
    print("=" * 78)
    print()
    print("Provenance trace for the detection run:")
    print(json.dumps(trace_run(conn, found["run_id"]), indent=2)[:1400])
    print()
    print(f"Artifacts on file: "
          f"{conn.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0]}. "
          f"Corrections logged: "
          f"{conn.execute('SELECT COUNT(*) FROM corrections').fetchone()[0]}.")
    conn.close()


if __name__ == "__main__":
    main()
