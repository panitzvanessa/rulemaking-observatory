"""
Collector.

Retrieves comments from the government interface and stores each record exactly
as returned, together with the coordinated universal time of retrieval and the
exact request that produced it. Every request is logged with its status,
successful or not, so that a gap in a corpus is visible as a gap rather than as
an absence.

The transport is pluggable. Live collection uses the standard library over
HTTPS. The test suite uses a fixture transport, which means the instrument can
be verified end to end without a network connection and without an API key.

Retrieval path, settled empirically rather than from documentation alone. The
first instrument's request log shows 260 successful requests against
/v4/comments with filter[docketId], storing 61,555 records, including the
complete posted record of a docket of 48,977 comments retrieved across ten
five-thousand-record windows. The docket filter therefore works in practice,
and this collector uses it as the primary path. The GSA-documented alternative,
listing a docket's documents and filtering comments by each document's
objectId, remains available through the documents listing that this collector
performs first.

Three constraints of the interface shape the design. A query is capped at
twenty pages of two hundred and fifty records, and going past five thousand
requires a cursor on lastModifiedDate, which this collector persists so that an
interrupted collection resumes where it stopped. The cursor filter is fed a
conservative Eastern conversion, five hours behind the UTC timestamps the
interface returns, which over-fetches slightly at each window boundary, and
over-fetching is harmless because inserts resolve conflicts by doing nothing.
And the comment text lives on the detail record rather than the listing, so a
text corpus costs one request per comment, which at a thousand requests an hour
is the binding constraint on this project.
"""

from __future__ import annotations

import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Optional, Protocol

from .core import Config, Run, refresh_retrieval_status, register_docket, sha256, utc_now

MODULE = "collect"

PAGE_SIZE = 250
MAX_PAGES = 20                    # interface hard cap, 20 x 250 = 5000 per window
EASTERN_OFFSET_HOURS = 5          # conservative, see module docstring
LOW_LIMIT_THRESHOLD = 10          # pause when the hourly allowance runs this low
LOW_LIMIT_PAUSE_S = 600

# Text fields that point at an attachment rather than carrying content.
# Two heuristics are combined. The pattern list catches canonical phrasings,
# and the short-text word test, carried over from the first instrument, catches
# variants such as 'please see my attached letter' that the patterns miss.
PLACEHOLDER_PATTERNS = (
    "see attached",
    "see attachment",
    "please see attached",
    "attached",
    "see uploaded",
    "see letter",
    "comment attached",
    "n/a",
)


@dataclass
class Response:
    url: str
    status: int
    body: str
    error: Optional[str] = None
    headers: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status < 300


class Transport(Protocol):
    def get(self, url: str) -> Response: ...


class HttpTransport:
    """
    Live transport. Honours a minimum interval between requests and retries on
    the hourly limit.

    The key travels in the X-Api-Key header, which is the documented method.
    It must never be placed in the query string, because this instrument writes
    every request URL into the request log and stores it on each comment as
    source_url, so a key in the URL would be persisted into the audit trail and
    into any provenance trace exported from it.
    """

    def __init__(self, api_key: Optional[str] = None, min_interval_s: float = 3.7,
                 timeout: int = 60, max_retries: int = 4):
        self.api_key = api_key
        self.min_interval_s = min_interval_s
        self.timeout = timeout
        self.max_retries = max_retries
        self._last = 0.0

    def get(self, url: str) -> Response:
        attempt = 0
        while True:
            wait = self.min_interval_s - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            headers = {"X-Api-Key": self.api_key} if self.api_key else {}
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    body = r.read().decode("utf-8")
                    hdrs = {k.lower(): v for k, v in r.headers.items()}
                    return Response(url=url, status=r.status, body=body, headers=hdrs)
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < self.max_retries:
                    attempt += 1
                    backoff = 60 * attempt
                    print(f"  rate limited, waiting {backoff}s "
                          f"(attempt {attempt} of {self.max_retries})", flush=True)
                    time.sleep(backoff)
                    continue
                if e.code in (500, 502, 503, 504) and attempt < self.max_retries:
                    attempt += 1
                    print(f"  server error {e.code}, retrying in {10 * attempt}s "
                          f"(attempt {attempt} of {self.max_retries})", flush=True)
                    time.sleep(10 * attempt)
                    continue
                return Response(url=url, status=e.code, body="", error=f"HTTPError {e.code}")
            except KeyboardInterrupt:
                raise
            except Exception as e:  # network, DNS, timeout
                # Timeouts and connection resets are transient far more often
                # than they are permanent, and a sixty second timeout that is
                # not retried silently burns a minute per record. Retry with a
                # short backoff and report each attempt, so a dead network is
                # visible on the screen instead of looking like a frozen
                # program.
                if attempt < self.max_retries:
                    attempt += 1
                    print(f"  network problem ({type(e).__name__}), retrying in "
                          f"{5 * attempt}s (attempt {attempt} of {self.max_retries})",
                          flush=True)
                    time.sleep(5 * attempt)
                    continue
                return Response(url=url, status=0, body="", error=f"{type(e).__name__}: {e}")


class FixtureTransport:
    """
    Offline transport backed by a mapping of url to body. Unknown URLs return a
    logged failure rather than raising, which is what the live interface does
    and therefore what the request log must be able to represent.
    """

    def __init__(self, pages: dict[str, str]):
        self.pages = pages
        self.calls: list[str] = []

    def get(self, url: str) -> Response:
        self.calls.append(url)
        if url in self.pages:
            return Response(url=url, status=200, body=self.pages[url])
        return Response(url=url, status=404, body="", error="fixture miss")


def eastern_cursor(iso_utc: str) -> str:
    """
    Convert a UTC timestamp from the interface into the string the
    lastModifiedDate filter expects, conservatively five hours behind.

    The first instrument passed the UTC digits through unconverted and its
    largest corpus still landed on the exact posted count across ten window
    boundaries, so no loss was observed in practice. The conversion is kept
    conservative regardless, because an over-fetched boundary costs a few
    duplicate listings that conflict resolution discards, while an
    under-fetched boundary silently loses records.
    """
    cleaned = iso_utc.replace("Z", "").replace("T", " ").strip()
    dt = datetime.fromisoformat(cleaned)
    return (dt - timedelta(hours=EASTERN_OFFSET_HOURS)).strftime("%Y-%m-%d %H:%M:%S")


def is_placeholder(text: str) -> bool:
    """
    True when a text field points at an attachment instead of carrying content.
    Deliberately conservative. A long text that happens to mention an
    attachment is content, not a placeholder.
    """
    stripped = (text or "").strip().lower().rstrip(".;: ")
    if not stripped:
        return True
    if len(stripped) > 120:
        return False
    compact = " ".join(stripped.split())
    if any(compact == p or compact.startswith(p) for p in PLACEHOLDER_PATTERNS):
        return True
    words = compact.split()
    return len(words) <= 15 and any(w.startswith("attach") for w in words)


class Collector:
    def __init__(self, conn: sqlite3.Connection, cfg: Config, transport: Transport,
                 progress: bool = True):
        self.conn = conn
        self.cfg = cfg
        self.transport = transport
        self.progress = progress

    def _say(self, message: str) -> None:
        """
        Report progress as it happens. A collection that prints nothing for
        hours cannot be distinguished from one that has hung, so every stage
        reports, and the details stage reports often enough to be watched.
        """
        if self.progress:
            print(message, flush=True)

    # -- request logging -----------------------------------------------------

    def _fetch(self, run_id: int, url: str) -> Response:
        resp = self.transport.get(url)
        self.conn.execute(
            """INSERT INTO requests
               (run_id, url, method, requested_utc, http_status, ok, error, response_bytes)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                run_id,
                url,
                "GET",
                utc_now(),
                resp.status,
                1 if resp.ok else 0,
                resp.error,
                len(resp.body.encode("utf-8")) if resp.body else 0,
            ),
        )
        self.conn.commit()
        remaining = resp.headers.get("x-ratelimit-remaining")
        if remaining is not None:
            try:
                if int(remaining) < LOW_LIMIT_THRESHOLD:
                    print(f"  hourly allowance nearly spent ({remaining} left), "
                          f"pausing {LOW_LIMIT_PAUSE_S}s")
                    time.sleep(LOW_LIMIT_PAUSE_S)
            except ValueError:
                pass
        return resp

    def _last_request_id(self) -> int:
        return self.conn.execute("SELECT MAX(request_id) FROM requests").fetchone()[0]

    # -- url construction ----------------------------------------------------

    def document_page_url(self, docket_id: str, page: int, page_size: int = PAGE_SIZE) -> str:
        return f"{self.cfg.api_base}/documents?" + urllib.parse.urlencode({
            "filter[docketId]": docket_id,
            "page[size]": str(page_size),
            "page[number]": str(page),
            "sort": "postedDate",
        })

    def comment_page_url(self, docket_id: str, page: int, page_size: int = PAGE_SIZE,
                         cursor: Optional[str] = None) -> str:
        params = {
            "filter[docketId]": docket_id,
            "page[size]": str(page_size),
            "page[number]": str(page),
            "sort": "lastModifiedDate,documentId",
        }
        if cursor:
            params["filter[lastModifiedDate][ge]"] = cursor
        return f"{self.cfg.api_base}/comments?" + urllib.parse.urlencode(params)

    def comment_detail_url(self, comment_id: str, attachments: bool = False) -> str:
        base = f"{self.cfg.api_base}/comments/{comment_id}"
        return base + ("?include=attachments" if attachments else "")

    # -- cursor persistence --------------------------------------------------

    def _load_cursor(self, task: str) -> tuple[Optional[str], bool]:
        row = self.conn.execute(
            "SELECT cursor, complete FROM collection_cursors WHERE task=?", (task,)
        ).fetchone()
        if row is None:
            return None, False
        return row["cursor"] or None, bool(row["complete"])

    def _save_cursor(self, task: str, cursor: Optional[str], complete: bool) -> None:
        self.conn.execute(
            """INSERT INTO collection_cursors (task, cursor, updated_utc, complete)
               VALUES (?,?,?,?)
               ON CONFLICT(task) DO UPDATE SET
                 cursor=excluded.cursor,
                 updated_utc=excluded.updated_utc,
                 complete=excluded.complete""",
            (task, cursor or "", utc_now(), 1 if complete else 0),
        )
        self.conn.commit()

    # -- collection, stage one, listings -------------------------------------

    def collect_docket(
        self,
        docket_id: str,
        max_pages: int = MAX_PAGES,
        page_size: int = PAGE_SIZE,
        max_windows: int = 200,
        agency: Optional[str] = None,
        era: Optional[str] = None,
    ) -> dict:
        """
        Collect comment list records for a docket.

        Lists the docket's documents first, which yields each document's
        objectId for the alternative retrieval path and an inventory for later
        phases, then lists comments filtered by docket, walking past the five
        thousand record cap with a persisted lastModifiedDate cursor so that an
        interrupted collection resumes rather than restarts. A collection that
        previously finished restarts from the beginning without the cursor, to
        catch late postings, and conflict resolution discards the repeats.

        List records do not carry the comment text. Text requires the detail
        stage, collect_details, at one request per comment.
        """
        register_docket(self.conn, docket_id, agency=agency, era=era)
        task = f"comments:{docket_id}"
        cursor, was_complete = self._load_cursor(task)
        if was_complete:
            cursor = None
        params = {
            "docket_id": docket_id,
            "max_pages": max_pages,
            "page_size": page_size,
            "path": "comments.docketId, cursor on lastModifiedDate",
            "resumed_from_cursor": cursor,
        }
        stored = 0
        pages_ok = 0
        pages_failed = 0
        object_ids: list[str] = []
        total_elements: Optional[int] = None
        complete = False

        with Run(self.conn, MODULE, params, docket_id=docket_id) as run:
            # Documents, for objectIds and the docket inventory.
            for page in range(1, max_pages + 1):
                url = self.document_page_url(docket_id, page, page_size)
                resp = self._fetch(run.run_id, url)
                if not resp.ok:
                    pages_failed += 1
                    break
                try:
                    payload = json.loads(resp.body)
                except json.JSONDecodeError:
                    pages_failed += 1
                    break
                pages_ok += 1
                request_id = self._last_request_id()
                docs = payload.get("data", []) or []
                for d in docs:
                    oid = (d.get("attributes", {}) or {}).get("objectId")
                    if oid:
                        object_ids.append(oid)
                self._store_documents(docket_id, docs, request_id, url)
                if not (payload.get("meta", {}) or {}).get("hasNextPage"):
                    break

            # Comments, windowed by the persisted cursor.
            for _window in range(max_windows):
                window_last: Optional[str] = None
                has_next = False
                failed = False
                for page in range(1, max_pages + 1):
                    url = self.comment_page_url(docket_id, page, page_size, cursor)
                    resp = self._fetch(run.run_id, url)
                    if not resp.ok:
                        pages_failed += 1
                        failed = True
                        break
                    request_id = self._last_request_id()
                    try:
                        payload = json.loads(resp.body)
                    except json.JSONDecodeError:
                        pages_failed += 1
                        failed = True
                        break
                    pages_ok += 1
                    meta = payload.get("meta", {}) or {}
                    if total_elements is None and meta.get("totalElements") is not None:
                        total_elements = meta.get("totalElements")
                    records = payload.get("data", []) or []
                    if records:
                        new = self._store(docket_id, records, request_id, url)
                        stored += new
                        total_note = f" of {total_elements}" if total_elements else ""
                        self._say(f"  page {page}, received {len(records)}, new {new}, "
                                  f"stored so far {stored}{total_note}")
                        window_last = (records[-1].get("attributes", {}) or {}).get(
                            "lastModifiedDate"
                        ) or window_last
                    has_next = bool(meta.get("hasNextPage"))
                    if not has_next:
                        break
                if failed:
                    break
                if not has_next:
                    complete = True
                    break
                # Window exhausted with more to come. Advance the cursor.
                if not window_last:
                    break
                next_cursor = eastern_cursor(window_last)
                if next_cursor == cursor:
                    # The cursor failed to advance, which would loop forever.
                    # Stop and leave the state resumable rather than spinning.
                    break
                cursor = next_cursor
                self._save_cursor(task, cursor, complete=False)
                self._say(f"  five thousand record window exhausted, "
                          f"continuing from {cursor}")

            self._save_cursor(task, cursor, complete=complete)
            if total_elements is not None:
                self.conn.execute(
                    """UPDATE retrieval_status SET posted_count=?, as_of_utc=?
                       WHERE docket_id=?""",
                    (total_elements, utc_now(), docket_id),
                )
                self.conn.commit()
            refresh_retrieval_status(self.conn, docket_id)
            summary = {
                "run_id": run.run_id,
                "docket_id": docket_id,
                "documents_found": len(object_ids),
                "pages_ok": pages_ok,
                "pages_failed": pages_failed,
                "records_stored": stored,
                "posted_total_from_api": total_elements,
                "complete": complete and pages_failed == 0,
                "resumable_cursor": None if complete else cursor,
            }
        return summary

    def _store_documents(self, docket_id: str, records: Iterable[dict],
                         request_id: int, url: str) -> int:
        """
        Store the docket's documents. The objectId on each is what the comments
        endpoint filters on in the documented retrieval path, and later phases
        need these records to reach proposed and final rule preambles.
        """
        n = 0
        for rec in records:
            document_id = rec.get("id")
            if not document_id:
                continue
            raw = json.dumps(rec, sort_keys=True, ensure_ascii=False)
            attrs = rec.get("attributes", {}) or {}
            cur = self.conn.execute(
                """INSERT INTO documents
                   (document_id, docket_id, object_id, document_type, title,
                    posted_date, raw_json, raw_sha256, source_url,
                    retrieved_utc, request_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(document_id) DO NOTHING""",
                (
                    document_id,
                    docket_id,
                    attrs.get("objectId"),
                    attrs.get("documentType"),
                    attrs.get("title"),
                    attrs.get("postedDate"),
                    raw,
                    sha256(raw),
                    url,
                    utc_now(),
                    request_id,
                ),
            )
            n += max(cur.rowcount, 0)
        self.conn.commit()
        return n

    def _store(self, docket_id: str, records: Iterable[dict], request_id: int, url: str) -> int:
        """
        Insert records, returning the count actually written. Re-collection of a
        docket must not inflate the count, so the return value comes from the
        cursor's own rowcount rather than from the connection's cumulative
        change counter, which would report a write for a skipped conflict.
        """
        n = 0
        for rec in records:
            comment_id = rec.get("id")
            if not comment_id:
                continue
            raw = json.dumps(rec, sort_keys=True, ensure_ascii=False)
            attrs = rec.get("attributes", {}) or {}
            cur = self.conn.execute(
                """INSERT INTO comments
                   (comment_id, docket_id, raw_json, raw_sha256, source_url,
                    retrieved_utc, request_id, receive_date, posted_date,
                    has_attachment, tracking_number)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(comment_id) DO NOTHING""",
                (
                    comment_id,
                    docket_id,
                    raw,
                    sha256(raw),
                    url,
                    utc_now(),
                    request_id,
                    attrs.get("receiveDate"),
                    attrs.get("postedDate"),
                    1 if attrs.get("hasAttachments") else 0,
                    attrs.get("trackingNbr"),
                ),
            )
            n += max(cur.rowcount, 0)
        self.conn.commit()
        return n

    # -- collection, stage two, details --------------------------------------

    def collect_details(
        self,
        docket_id: str,
        limit: Optional[int] = None,
        attachments: bool = False,
        commit_every: int = 25,
    ) -> dict:
        """
        Retrieve the detail record for each stored comment that lacks one.

        The detail record is where the comment text and the agency-configured
        metadata fields live, so this stage is what turns a listing into a
        corpus. It costs one request per comment, which is the binding
        constraint, and it is resumable, since only comments without a stored
        detail are requested. The detail response is stored verbatim with its
        own provenance, following the first instrument's raw_details practice.
        """
        pending = self.conn.execute(
            """SELECT c.comment_id FROM comments c
               LEFT JOIN comment_details d ON d.comment_id = c.comment_id
               WHERE c.docket_id = ? AND d.comment_id IS NULL
               ORDER BY c.comment_id""",
            (docket_id,),
        ).fetchall()
        todo = [r["comment_id"] for r in pending]
        if limit is not None:
            todo = todo[:limit]
        params = {
            "docket_id": docket_id,
            "pending": len(pending),
            "requested": len(todo),
            "attachments": attachments,
        }
        fetched = 0
        failed = 0
        consecutive_failures = 0
        aborted_for_network = False
        started = time.monotonic()
        with Run(self.conn, "collect_details", params, docket_id=docket_id) as run:
            for i, comment_id in enumerate(todo, start=1):
                url = self.comment_detail_url(comment_id, attachments)
                resp = self._fetch(run.run_id, url)
                if not resp.ok:
                    failed += 1
                    consecutive_failures += 1
                    self._say(f"  {comment_id}: {resp.error or resp.status}, "
                              f"logged as a failure, continuing")
                    if consecutive_failures >= 8:
                        # Eight straight failures after per-request retries is a
                        # dead network or a down interface, not eight unlucky
                        # records. Stop with everything fetched so far safe,
                        # rather than spending days failing in a loop. Rerunning
                        # the same command resumes exactly here.
                        aborted_for_network = True
                        self._say("  eight consecutive failures, the interface is "
                                  "not responding from this connection. Stopping. "
                                  "Nothing is lost, rerun the same command to "
                                  "resume, or try again later.")
                        break
                    continue
                consecutive_failures = 0
                request_id = self._last_request_id()
                try:
                    data = json.loads(resp.body).get("data")
                except json.JSONDecodeError:
                    failed += 1
                    continue
                if not data:
                    failed += 1
                    continue
                raw = json.dumps(data, sort_keys=True, ensure_ascii=False)
                self.conn.execute(
                    """INSERT INTO comment_details
                       (comment_id, raw_json, raw_sha256, source_url,
                        retrieved_utc, request_id)
                       VALUES (?,?,?,?,?,?)
                       ON CONFLICT(comment_id) DO NOTHING""",
                    (comment_id, raw, sha256(raw), url, utc_now(), request_id),
                )
                # The listing does not carry receiveDate, so the comments row was
                # stored with a null there. The detail does carry it, and the
                # date fields are backfilled from it, detail as the source of
                # truth for anything the listing omits.
                d_attrs = (data.get("attributes", {}) or {})
                self.conn.execute(
                    """UPDATE comments SET
                         receive_date = COALESCE(receive_date, ?),
                         posted_date  = COALESCE(posted_date, ?)
                       WHERE comment_id = ?""",
                    (d_attrs.get("receiveDate"), d_attrs.get("postedDate"), comment_id),
                )
                fetched += 1
                if i % commit_every == 0:
                    self.conn.commit()
                    elapsed = time.monotonic() - started
                    rate = elapsed / i
                    left = (len(todo) - i) * rate
                    self._say(f"  {i} of {len(todo)} details, {failed} failed, "
                              f"about {left / 3600:.1f} hours remaining"
                              if left > 3600 else
                              f"  {i} of {len(todo)} details, {failed} failed, "
                              f"about {left / 60:.0f} minutes remaining")
            self.conn.commit()
            summary = {
                "run_id": run.run_id,
                "docket_id": docket_id,
                "pending_before": len(pending),
                "fetched": fetched,
                "failed": failed,
                "remaining": len(pending) - fetched,
                "budget_note": budget(len(pending) - fetched),
            }
            if aborted_for_network:
                summary["stopped_early"] = (
                    "stopped after eight consecutive network failures, resume "
                    "with the same command"
                )
        return summary


def budget(n_requests: int, interval_s: float = 3.7) -> str:
    """State the cost of remaining detail retrieval plainly."""
    if n_requests <= 0:
        return "nothing remaining"
    hours = n_requests * interval_s / 3600
    if hours < 1:
        return f"{n_requests} detail requests, about {hours * 60:.0f} minutes"
    if hours < 48:
        return f"{n_requests} detail requests, about {hours:.1f} hours"
    return f"{n_requests} detail requests, about {hours / 24:.1f} days of continuous running"


def extract_field_texts(conn: sqlite3.Connection, docket_id: str) -> dict:
    """
    Extract the typed text field from stored records, preferring the detail
    record where one exists, since the listing does not carry the comment text.

    A corpus with no detail records yields no text at all, and that failure is
    silent once stored, so the result flags the corpus as text_missing when no
    record carries a comment field. A corpus in that state is a listing and not
    a text corpus, and no measure may be run over it.

    Attachment extraction is Phase one and is deliberately not implemented here.
    """
    params = {"docket_id": docket_id, "source": "field", "extractor": "field_v2"}
    written = 0
    attempted = 0
    from_detail = 0
    with Run(conn, "extract_field", params, docket_id=docket_id) as run:
        rows = conn.execute(
            """SELECT c.comment_id, c.raw_json AS list_json, d.raw_json AS detail_json
               FROM comments c
               LEFT JOIN comment_details d ON d.comment_id = c.comment_id
               WHERE c.docket_id=?""",
            (docket_id,),
        ).fetchall()
        for row in rows:
            attrs = {}
            source_had_field = False
            if row["detail_json"]:
                attrs = json.loads(row["detail_json"]).get("attributes", {}) or {}
                if "comment" in attrs:
                    source_had_field = True
                    from_detail += 1
            if not source_had_field:
                list_attrs = json.loads(row["list_json"]).get("attributes", {}) or {}
                if "comment" in list_attrs:
                    attrs = list_attrs
                    source_had_field = True
            content = attrs.get("comment") or ""
            ph = is_placeholder(content)
            cur = conn.execute(
                """INSERT INTO texts
                   (comment_id, run_id, source, attachment_ref, extractor,
                    extractor_version, content, content_sha256, char_len,
                    word_len, is_placeholder)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT DO NOTHING""",
                (
                    row["comment_id"],
                    run.run_id,
                    "field",
                    None,
                    "field_v2",
                    "2.0.0",
                    content,
                    sha256(content),
                    len(content),
                    len(content.split()),
                    1 if ph else 0,
                ),
            )
            attempted += 1
            written += max(cur.rowcount, 0)
        conn.commit()
        refresh_retrieval_status(conn, docket_id)

        # Corpus figures are read back from the table rather than accumulated in
        # the loop, so that a re-extraction reports the state of the corpus and
        # not the number of rows it tried to write. The counters in this loop
        # describe the write, the query below describes the corpus.
        state = conn.execute(
            """SELECT COUNT(*) AS n,
                      SUM(t.is_placeholder) AS ph,
                      SUM(CASE WHEN t.char_len = 0 THEN 1 ELSE 0 END) AS empty
               FROM texts t
               JOIN comments c ON c.comment_id = t.comment_id
               JOIN (SELECT comment_id, source, COALESCE(attachment_ref,'') AS aref,
                            MAX(text_id) AS newest
                     FROM texts GROUP BY comment_id, source, aref) latest
                 ON latest.newest = t.text_id
               WHERE c.docket_id = ? AND t.source = 'field'""",
            (docket_id,),
        ).fetchone()
        n = state["n"] or 0
        placeholders = state["ph"] or 0
        empty = state["empty"] or 0
        text_missing = n > 0 and empty == n
        result = {
            "run_id": run.run_id,
            "rows_written": written,
            "rows_attempted": attempted,
            "texts_in_corpus": n,
            "from_detail_records": from_detail,
            "placeholders": placeholders,
            "records_without_a_comment_field": empty,
            "placeholder_share_pct": round(100.0 * placeholders / n, 1) if n else None,
            "text_missing": text_missing,
        }
        if text_missing:
            result["warning"] = (
                "No stored record carries a comment field. This corpus is a listing, not a "
                "text corpus. Run collect_details first, at one request per comment."
            )
    return result
