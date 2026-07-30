"""
Rulemaking Observatory, core layer.

Configuration, database access, and run registration. Paths are configurable
and never hard coded, so that the same code runs on the researcher's machine,
on a hosted runner, and in the test suite against a temporary database.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

MODULE_VERSION = "2.0.0"

# The dividing date for era comparison. Public release of ChatGPT.
AI_DIVIDE = "2022-11-30"

# The four dockets of the current design, so that agency and era are declared in
# one place, cannot be mistyped, and are available to every module including the
# archive importer. Era is relative to AI_DIVIDE, and 'spanning' marks a docket
# with comment waves on both sides of it, which is the within-docket design.
DESIGN_DOCKETS = {
    "EPA-HQ-OAR-2021-0317": ("EPA", "spanning", "methane, comment waves on both sides"),
    "WHD-2022-0003": ("DOL", "pre", "independent contractor rule"),
    "EPA-HQ-OAR-2025-0194": ("EPA", "post", "endangerment finding rescission"),
    "WHD-2026-0001": ("DOL", "post", "contractor rescission"),
}

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def utc_now() -> str:
    """ISO 8601 in UTC, second precision, stable across platforms."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_json(obj: Any) -> str:
    """Deterministic serialisation, so parameter hashes are comparable."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass
class Config:
    """
    Runtime configuration.

    root is the working directory holding the database and any downloaded
    material. archive_db points at the read only database from the first
    instrument, retained so that published figures remain reproducible.
    """

    root: Path
    db_name: str = "observatory.sqlite3"
    archive_db: Optional[Path] = None
    api_key: Optional[str] = None
    api_base: str = "https://api.regulations.gov/v4"
    rate_limit_per_hour: int = 1000
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls, root: Optional[os.PathLike] = None) -> "Config":
        root_path = Path(root or os.environ.get("OBSERVATORY_ROOT", "./data")).expanduser()
        archive = os.environ.get("OBSERVATORY_ARCHIVE_DB")
        return cls(
            root=root_path,
            archive_db=Path(archive).expanduser() if archive else None,
            api_key=os.environ.get("REGS_API_KEY"),
        )

    @property
    def db_path(self) -> Path:
        return self.root / self.db_name

    def ensure(self) -> "Config":
        self.root.mkdir(parents=True, exist_ok=True)
        return self


def connect(cfg: Config) -> sqlite3.Connection:
    cfg.ensure()
    conn = sqlite3.connect(cfg.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    _migrate(conn)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """
    In-place migrations for databases created by earlier versions of this
    schema. CREATE IF NOT EXISTS never replaces an existing object, so a
    definition change must drop the outdated object first or existing
    databases keep the old behaviour silently.

    Migration one. The texts identity index originally lacked the content
    hash, which let an early extraction over a listing permanently block the
    corrected extraction over detail records. If the installed index predates
    the fix, drop it so the schema script recreates it with the hash.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='ux_texts_identity'"
    ).fetchone()
    if row and row[0] and "content_sha256" not in row[0]:
        conn.execute("DROP INDEX ux_texts_identity")
        conn.commit()


def code_sha(default: str = "unversioned") -> str:
    """
    Best effort identification of the code that produced a run. Falls back to a
    hash of the package source when the tree is not a git repository, so that a
    run is always attributable to a specific state of the code.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    parts = sorted(Path(__file__).parent.glob("*.py")) + [SCHEMA_PATH]
    digest = hashlib.sha256()
    for p in parts:
        digest.update(p.read_bytes())
    return f"src:{digest.hexdigest()[:16]}" if parts else default


class Run:
    """
    A registered analytical or collection run.

    Use as a context manager so that a run is always closed with a status,
    including when it fails. An unclosed run left as 'running' in the database
    is itself informative and should not be tidied away.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        module: str,
        parameters: dict,
        docket_id: Optional[str] = None,
        seed: Optional[int] = None,
        note: Optional[str] = None,
    ):
        self.conn = conn
        self.module = module
        self.parameters = parameters
        params = canonical_json(parameters)
        cur = conn.execute(
            """INSERT INTO runs
               (module, module_version, docket_id, started_utc, parameters_json,
                parameters_sha256, seed, code_sha, status, note)
               VALUES (?,?,?,?,?,?,?,?,'running',?)""",
            (
                module,
                MODULE_VERSION,
                docket_id,
                utc_now(),
                params,
                sha256(params),
                seed,
                code_sha(),
                note,
            ),
        )
        conn.commit()
        self.run_id: int = cur.lastrowid

    def __enter__(self) -> "Run":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        status = "complete" if exc_type is None else "failed"
        note = None if exc_type is None else f"{exc_type.__name__}: {exc}"
        self.conn.execute(
            "UPDATE runs SET finished_utc=?, status=?, note=COALESCE(?, note) WHERE run_id=?",
            (utc_now(), status, note, self.run_id),
        )
        self.conn.commit()
        return False  # never swallow the exception


def register_docket(
    conn: sqlite3.Connection,
    docket_id: str,
    agency: Optional[str] = None,
    title: Optional[str] = None,
    era: Optional[str] = None,
    design_role: Optional[str] = None,
    rulemaking_note: Optional[str] = None,
) -> None:
    conn.execute(
        """INSERT INTO dockets
           (docket_id, agency, title, era, design_role, rulemaking_note, first_seen_utc)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(docket_id) DO UPDATE SET
             agency=COALESCE(excluded.agency, dockets.agency),
             title=COALESCE(excluded.title, dockets.title),
             era=COALESCE(excluded.era, dockets.era),
             design_role=COALESCE(excluded.design_role, dockets.design_role),
             rulemaking_note=COALESCE(excluded.rulemaking_note, dockets.rulemaking_note)""",
        (docket_id, agency, title, era, design_role, rulemaking_note, utc_now()),
    )
    conn.execute(
        "INSERT OR IGNORE INTO retrieval_status (docket_id, as_of_utc) VALUES (?,?)",
        (docket_id, utc_now()),
    )
    conn.commit()


def refresh_retrieval_status(conn: sqlite3.Connection, docket_id: str) -> None:
    """Recompute stored counts. Called after collection and after extraction."""
    stored = conn.execute(
        "SELECT COUNT(*) FROM comments WHERE docket_id=?", (docket_id,)
    ).fetchone()[0]
    extracted = conn.execute(
        """SELECT COUNT(*) FROM texts t JOIN comments c ON c.comment_id=t.comment_id
           WHERE c.docket_id=? AND t.is_placeholder=0""",
        (docket_id,),
    ).fetchone()[0]
    conn.execute(
        """UPDATE retrieval_status
           SET comments_stored=?, texts_extracted=?, as_of_utc=? WHERE docket_id=?""",
        (stored, extracted, utc_now(), docket_id),
    )
    conn.commit()
