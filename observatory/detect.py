"""
Campaign detector.

Groups texts into exact duplicate clusters and near duplicate clusters. Near
duplicates are found with five word shingling and locality sensitive hashing
over MinHash signatures, which approximates the pairwise comparison used in
prior work at near linear rather than quadratic cost. That difference is what
makes a minimum cluster size of three tractable, and a minimum of three is what
makes coordination below the conventional hundred comment threshold visible at
all.

MinHash and the banding scheme are implemented here rather than imported, so
that the similarity relation behind every reported cluster is auditable in the
same repository as the claim it supports.

One refinement adopted from Judge-Lord (2021). Phrases appearing in the
proposed rule are removed before clustering, because commenters quote the rule
they are commenting on and shared quotation reads as shared authorship if it is
not stripped first.
"""

from __future__ import annotations

import hashlib
import html as _html
import re
import sqlite3
from dataclasses import dataclass
from typing import Iterable, Optional

from .core import Run, sha256

MODULE = "detect"

SHINGLE_WORDS = 5
MIN_CLUSTER = 3
MIN_CHARS = 200
NUM_PERM = 128
BANDS = 32          # 32 bands of 4 rows, tuned for a similarity threshold near 0.6
ROWS = NUM_PERM // BANDS
SIM_THRESHOLD = 0.6

_MASK = (1 << 61) - 1
_WORD_RE = re.compile(r"[a-z0-9']+")

# Permutation constants for MinHash, derived deterministically from a fixed
# seed so that signatures are reproducible across runs and machines. Each
# shingle is hashed once, and the permuted values come from universal hashing,
# a_i times x plus b_i modulo a Mersenne prime. This replaces hashing every
# shingle once per permutation, which was the honest but slow first version,
# measured at minutes on a fifteen thousand text corpus. The Jaccard estimate
# is the standard one and remains unbiased.
def _perm_constants(num_perm: int) -> tuple[tuple[int, ...], tuple[int, ...]]:
    a: list[int] = []
    b: list[int] = []
    for i in range(num_perm):
        da = hashlib.blake2b(f"perm-a-{i}".encode(), digest_size=8).digest()
        db = hashlib.blake2b(f"perm-b-{i}".encode(), digest_size=8).digest()
        a.append((int.from_bytes(da, "little") | 1) & _MASK)  # odd, nonzero
        b.append(int.from_bytes(db, "little") & _MASK)
    return tuple(a), tuple(b)


_PERM_A, _PERM_B = _perm_constants(NUM_PERM)


def _h(value: str, salt: int) -> int:
    d = hashlib.blake2b(value.encode("utf-8"), digest_size=8, salt=salt.to_bytes(8, "little"))
    return int.from_bytes(d.digest(), "little") & _MASK


def signature(shingle_set: set[str], num_perm: int = NUM_PERM) -> tuple[int, ...]:
    """MinHash signature. Empty input yields a sentinel that matches nothing."""
    if not shingle_set:
        return tuple([_MASK] * num_perm)
    base = [_h(s, 0) for s in shingle_set]
    sig = [_MASK] * num_perm
    pa, pb = _PERM_A, _PERM_B
    for x in base:
        for i in range(num_perm):
            v = (pa[i] * x + pb[i]) % _MASK
            if v < sig[i]:
                sig[i] = v
    return tuple(sig)


def normalise(text: str) -> str:
    """
    Unescape HTML entities, strip tags, lowercase, collapse whitespace.

    Unescaping matters for the similarity relation itself. Live comments carry
    entities, and without unescaping, don&rsquo;t tokenises as three tokens
    including rsquo, so the same form letter submitted once with entities and
    once with plain punctuation fails to match as a duplicate, and entity names
    become tokens shared between unrelated texts. The first instrument
    unescaped for exactly this reason, and that behaviour is restored here.
    """
    text = _html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.lower().split())


def tokens(text: str) -> list[str]:
    return _WORD_RE.findall(normalise(text))


def shingles(text: str, k: int = SHINGLE_WORDS) -> set[str]:
    tok = tokens(text)
    if len(tok) < k:
        return {" ".join(tok)} if tok else set()
    return {" ".join(tok[i : i + k]) for i in range(len(tok) - k + 1)}


def estimated_jaccard(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    if not a or not b:
        return 0.0
    return sum(1 for x, y in zip(a, b) if x == y) / len(a)


def band_keys(sig: tuple[int, ...], bands: int = BANDS, rows: int = ROWS) -> list[str]:
    keys = []
    for band in range(bands):
        chunk = sig[band * rows : (band + 1) * rows]
        keys.append(f"{band}:" + hashlib.blake2b(
            repr(chunk).encode("utf-8"), digest_size=8
        ).hexdigest())
    return keys


class _Union:
    """Union find over text ids, used to merge LSH candidate pairs."""

    def __init__(self):
        self.parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


@dataclass
class TextRow:
    text_id: int
    comment_id: str
    content: str


def load_texts(conn: sqlite3.Connection, docket_id: str, min_chars: int = MIN_CHARS) -> list[TextRow]:
    """
    Load candidate texts.

    Placeholders are excluded because they are a category of record rather than a
    short comment, and including them would create spurious clusters of
    identical pointers to different attachments.

    Only the newest extraction of each distinct text is loaded, one per comment,
    source, and attachment reference. Re-extracting a docket under a new
    extractor version leaves the older generation in place by design, since
    extraction carries its own provenance and history is not destroyed, so the
    detector must select rather than assume. Loading two generations would
    double every count and cluster each text with its own predecessor, which is
    the most dangerous failure available to this module.
    """
    rows = conn.execute(
        """SELECT t.text_id, t.comment_id, t.content
           FROM texts t
           JOIN comments c ON c.comment_id = t.comment_id
           JOIN (SELECT comment_id, source, COALESCE(attachment_ref,'') AS aref,
                        MAX(text_id) AS newest
                 FROM texts GROUP BY comment_id, source, aref) latest
             ON latest.newest = t.text_id
           WHERE c.docket_id = ? AND t.is_placeholder = 0 AND t.char_len >= ?
           ORDER BY t.text_id""",
        (docket_id, min_chars),
    ).fetchall()
    return [TextRow(r["text_id"], r["comment_id"], r["content"]) for r in rows]


def rule_shingles(conn: sqlite3.Connection, docket_id: str) -> set[str]:
    """Shingles from the proposed rule, to be stripped before clustering."""
    rows = conn.execute(
        "SELECT content FROM rule_texts WHERE docket_id=? AND stage='proposed'",
        (docket_id,),
    ).fetchall()
    out: set[str] = set()
    for r in rows:
        out |= shingles(r["content"])
    return out


def detect(
    conn: sqlite3.Connection,
    docket_id: str,
    min_cluster: int = MIN_CLUSTER,
    min_chars: int = MIN_CHARS,
    sim_threshold: float = SIM_THRESHOLD,
    strip_rule_text: bool = True,
) -> dict:
    """
    Run campaign detection over a docket and persist clusters against the run.

    Returns a summary. Every proportion in the summary is accompanied by its
    denominator, because a share without a denominator is not a finding.
    """
    params = {
        "docket_id": docket_id,
        "shingle_words": SHINGLE_WORDS,
        "min_cluster": min_cluster,
        "min_chars": min_chars,
        "num_perm": NUM_PERM,
        "bands": BANDS,
        "rows": ROWS,
        "sim_threshold": sim_threshold,
        "strip_rule_text": strip_rule_text,
    }

    with Run(conn, MODULE, params, docket_id=docket_id) as run:
        rows = load_texts(conn, docket_id, min_chars)
        stripped = rule_shingles(conn, docket_id) if strip_rule_text else set()

        # Exact duplicate grouping on normalised content.
        exact: dict[str, list[TextRow]] = {}
        for r in rows:
            exact.setdefault(sha256(normalise(r.content)), []).append(r)

        # One representative per exact group enters near duplicate detection, so
        # that a widely circulated form letter cannot dominate the banding.
        reps: list[TextRow] = [g[0] for g in exact.values()]
        sigs: dict[int, tuple[int, ...]] = {}
        for r in reps:
            sh = shingles(r.content) - stripped
            sigs[r.text_id] = signature(sh)

        buckets: dict[str, list[int]] = {}
        for tid, sig in sigs.items():
            for key in band_keys(sig):
                buckets.setdefault(key, []).append(tid)

        uf = _Union()
        pair_sim: dict[tuple[int, int], float] = {}
        for members in buckets.values():
            if len(members) < 2:
                continue
            # Anchor linking rather than all pairs. A bucket holding a few
            # thousand satellites of one campaign would otherwise force
            # millions of pairwise comparisons, which was measured as minutes
            # on a fifteen thousand text corpus. Each member is compared to the
            # bucket's first member only, and union-find transitivity plus the
            # thirty-two independent bands recover the links an anchor misses.
            anchor = members[0]
            for other in members[1:]:
                a, b = anchor, other
                key = (a, b) if a < b else (b, a)
                if key in pair_sim:
                    continue
                s = estimated_jaccard(sigs[a], sigs[b])
                pair_sim[key] = s
                if s >= sim_threshold:
                    uf.union(a, b)

        rep_by_id = {r.text_id: r for r in reps}
        groups: dict[int, list[TextRow]] = {}
        for tid in sigs:
            root = uf.find(tid)
            groups.setdefault(root, []).append(rep_by_id[tid])

        # Expand each near duplicate group back to full membership by pulling in
        # the exact duplicates of every representative it contains.
        exact_by_rep: dict[int, list[TextRow]] = {g[0].text_id: g for g in exact.values()}

        clusters_written = 0
        members_written = 0
        local_index = 0
        for root, members in sorted(groups.items(), key=lambda kv: -sum(
            len(exact_by_rep[m.text_id]) for m in kv[1]
        )):
            full: list[TextRow] = []
            for m in members:
                full.extend(exact_by_rep[m.text_id])
            if len(full) < min_cluster:
                continue
            local_index += 1
            kind = "exact" if len(members) == 1 else "near"
            excerpt = " ".join(normalise(full[0].content).split()[:25])
            cur = conn.execute(
                """INSERT INTO clusters
                   (run_id, docket_id, local_index, kind, size, exemplar_text_id, core_excerpt)
                   VALUES (?,?,?,?,?,?,?)""",
                (run.run_id, docket_id, local_index, kind, len(full), full[0].text_id, excerpt),
            )
            cluster_id = cur.lastrowid
            for t in full:
                sim = 1.0 if t.text_id == full[0].text_id else pair_sim.get(
                    (min(t.text_id, full[0].text_id), max(t.text_id, full[0].text_id))
                )
                conn.execute(
                    """INSERT INTO cluster_members (run_id, cluster_id, text_id, similarity)
                       VALUES (?,?,?,?)""",
                    (run.run_id, cluster_id, t.text_id, sim),
                )
                members_written += 1
            clusters_written += 1
        conn.commit()

        total = len(rows)
        summary = {
            "run_id": run.run_id,
            "docket_id": docket_id,
            "texts_considered": total,
            "clusters": clusters_written,
            "texts_in_clusters": members_written,
            "clustered_share_pct": round(100.0 * members_written / total, 1) if total else None,
            "rule_shingles_stripped": len(stripped),
            "min_cluster": min_cluster,
        }
    return summary


def cluster_table(conn: sqlite3.Connection, run_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT local_index, kind, size, core_excerpt
           FROM clusters WHERE run_id=? ORDER BY size DESC, local_index""",
        (run_id,),
    ).fetchall()
