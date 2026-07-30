-- Rulemaking Observatory, rebuilt schema.
-- Design commitment. Every claim resolves to a run, then to member records,
-- then to raw responses, then to the public address that returned them.
-- Nothing in this schema stores a judgement about an individual person.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Provenance spine
-- ---------------------------------------------------------------------------

-- Every analytical or collection run, with its complete parameter set.
-- parameters_sha256 makes a run's configuration citable.
CREATE TABLE IF NOT EXISTS runs (
    run_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    module             TEXT    NOT NULL,
    module_version     TEXT    NOT NULL,
    docket_id          TEXT,
    started_utc        TEXT    NOT NULL,
    finished_utc       TEXT,
    parameters_json    TEXT    NOT NULL,
    parameters_sha256  TEXT    NOT NULL,
    seed               INTEGER,
    code_sha           TEXT,
    status             TEXT    NOT NULL DEFAULT 'running'
                       CHECK (status IN ('running','complete','failed','aborted')),
    note               TEXT
);

-- Every request issued against the government interface, successful or not,
-- so that gaps in a corpus are visible as gaps rather than as absences.
CREATE TABLE IF NOT EXISTS requests (
    request_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES runs(run_id),
    url             TEXT    NOT NULL,
    method          TEXT    NOT NULL DEFAULT 'GET',
    requested_utc   TEXT    NOT NULL,
    http_status     INTEGER,
    ok              INTEGER NOT NULL CHECK (ok IN (0,1)),
    error           TEXT,
    response_bytes  INTEGER
);
CREATE INDEX IF NOT EXISTS ix_requests_run ON requests(run_id);

-- ---------------------------------------------------------------------------
-- Reference
-- ---------------------------------------------------------------------------

-- era is relative to the public release of ChatGPT on 2022-11-30.
-- 'spanning' marks a docket with comment waves on both sides of that date,
-- which is the within-docket design.
CREATE TABLE IF NOT EXISTS dockets (
    docket_id        TEXT PRIMARY KEY,
    agency           TEXT,
    title            TEXT,
    era              TEXT CHECK (era IN ('pre','post','spanning')),
    design_role      TEXT,
    rulemaking_note  TEXT,
    first_seen_utc   TEXT NOT NULL
);

-- Posted is what the record shows. Received is what the agency says it got.
-- The two differ everywhere, and the difference is a measure, not a nuisance.
CREATE TABLE IF NOT EXISTS retrieval_status (
    docket_id        TEXT PRIMARY KEY REFERENCES dockets(docket_id),
    posted_count     INTEGER,
    received_count   INTEGER,
    received_source  TEXT,
    comments_stored  INTEGER NOT NULL DEFAULT 0,
    texts_extracted  INTEGER NOT NULL DEFAULT 0,
    sampling         TEXT NOT NULL DEFAULT 'identifier_ordered'
                     CHECK (sampling IN ('identifier_ordered','random_stratified','census')),
    as_of_utc        TEXT
);

-- Documents in a docket, the proposed rule, the final rule, and supporting
-- material. Each carries an objectId, which is what the comments endpoint
-- filters on in the documented retrieval path, and which later phases need in
-- order to reach preambles. The first instrument stored 687 of these and the
-- rebuilt schema had nowhere to put them, which this table closes.
CREATE TABLE IF NOT EXISTS documents (
    document_id    TEXT PRIMARY KEY,
    docket_id      TEXT NOT NULL REFERENCES dockets(docket_id),
    object_id      TEXT,
    document_type  TEXT,
    title          TEXT,
    posted_date    TEXT,
    raw_json       TEXT NOT NULL,
    raw_sha256     TEXT NOT NULL,
    source_url     TEXT NOT NULL,
    retrieved_utc  TEXT NOT NULL,
    request_id     INTEGER REFERENCES requests(request_id)
);
CREATE INDEX IF NOT EXISTS ix_documents_docket ON documents(docket_id);

-- ---------------------------------------------------------------------------
-- Raw record
-- ---------------------------------------------------------------------------

-- raw_json is stored exactly as returned. Never normalise in place.
-- receive_date is preferred over posted_date, because posted_date reflects the
-- agency's own batching rhythm and not the public's behaviour.
CREATE TABLE IF NOT EXISTS comments (
    comment_id     TEXT PRIMARY KEY,
    docket_id      TEXT NOT NULL REFERENCES dockets(docket_id),
    raw_json       TEXT NOT NULL,
    raw_sha256     TEXT NOT NULL,
    source_url     TEXT NOT NULL,
    retrieved_utc  TEXT NOT NULL,
    request_id     INTEGER NOT NULL REFERENCES requests(request_id),
    receive_date   TEXT,
    posted_date    TEXT,
    has_attachment INTEGER NOT NULL DEFAULT 0 CHECK (has_attachment IN (0,1)),
    tracking_number TEXT
);
CREATE INDEX IF NOT EXISTS ix_comments_docket ON comments(docket_id);

-- The detail record for a comment, retrieved one request at a time. This is
-- where the comment text and the agency configured metadata fields live, so a
-- corpus without detail records is a listing and not a text corpus. Stored
-- verbatim with its own provenance, following the first instrument's
-- raw_details practice.
CREATE TABLE IF NOT EXISTS comment_details (
    comment_id     TEXT PRIMARY KEY REFERENCES comments(comment_id),
    raw_json       TEXT    NOT NULL,
    raw_sha256     TEXT    NOT NULL,
    source_url     TEXT    NOT NULL,
    retrieved_utc  TEXT    NOT NULL,
    request_id     INTEGER NOT NULL REFERENCES requests(request_id)
);

-- Resume points for windowed collection past the interface's five thousand
-- record cap. Carried over from the first instrument's cursors table, which
-- collected a complete 48,977 comment docket across ten windows.
CREATE TABLE IF NOT EXISTS collection_cursors (
    task         TEXT PRIMARY KEY,
    cursor       TEXT NOT NULL,
    updated_utc  TEXT NOT NULL,
    complete     INTEGER NOT NULL DEFAULT 0 CHECK (complete IN (0,1))
);

-- ---------------------------------------------------------------------------
-- Extracted text
-- ---------------------------------------------------------------------------

-- The extraction is itself an analytical act, so it carries its own provenance.
-- is_placeholder marks a text field that points at an attachment rather than
-- carrying content. Placeholders are a category, never a short comment.
CREATE TABLE IF NOT EXISTS texts (
    text_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    comment_id         TEXT    NOT NULL REFERENCES comments(comment_id),
    run_id             INTEGER NOT NULL REFERENCES runs(run_id),
    source             TEXT    NOT NULL CHECK (source IN ('field','attachment')),
    attachment_ref     TEXT,
    extractor          TEXT    NOT NULL,
    extractor_version  TEXT    NOT NULL,
    content            TEXT    NOT NULL,
    content_sha256     TEXT    NOT NULL,
    char_len           INTEGER NOT NULL,
    word_len           INTEGER NOT NULL,
    is_placeholder     INTEGER NOT NULL DEFAULT 0 CHECK (is_placeholder IN (0,1))
);

-- Identity of an extracted text. Declared as an expression index rather than an
-- inline UNIQUE because attachment_ref is nullable, and in SQL a NULL never
-- equals a NULL, so an inline UNIQUE spanning a nullable column never fires and
-- every re-extraction would silently duplicate the corpus. COALESCE closes that.
-- The content hash is part of the identity, because extraction can legitimately
-- produce different content for the same comment as sources improve, an early
-- extraction over a listing yields empty text and a later one over the detail
-- record yields the comment, and an identity without content would let the
-- empty generation permanently block the corrected one. Identical re-extraction
-- still conflicts and writes nothing, changed content writes a new generation,
-- and every reader selects the newest generation per comment and source.
CREATE UNIQUE INDEX IF NOT EXISTS ux_texts_identity
    ON texts (comment_id, source, COALESCE(attachment_ref, ''),
              extractor_version, content_sha256);
CREATE INDEX IF NOT EXISTS ix_texts_comment ON texts(comment_id);
CREATE INDEX IF NOT EXISTS ix_texts_run ON texts(run_id);

-- ---------------------------------------------------------------------------
-- Commenter type, machine suggestion and human code kept apart
-- ---------------------------------------------------------------------------

-- pre_ columns hold rule-based suggestions with the named rule that fired.
-- cal_ columns are reserved for human coding and are never written by a model.
-- Agreement between the two is measured before any machine code is scaled.
CREATE TABLE IF NOT EXISTS commenter_codes (
    comment_id     TEXT PRIMARY KEY REFERENCES comments(comment_id),
    pre_type       TEXT,
    pre_subtype    TEXT,
    pre_rationale  TEXT,
    pre_run_id     INTEGER REFERENCES runs(run_id),
    cal_type       TEXT,
    cal_subtype    TEXT,
    cal_coder      TEXT,
    cal_certainty  INTEGER CHECK (cal_certainty IN (1,2,3)),
    cal_coded_utc  TEXT,
    cal_note       TEXT
);

-- ---------------------------------------------------------------------------
-- Coordination
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS clusters (
    cluster_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           INTEGER NOT NULL REFERENCES runs(run_id),
    docket_id        TEXT    NOT NULL REFERENCES dockets(docket_id),
    local_index      INTEGER NOT NULL,
    kind             TEXT    NOT NULL CHECK (kind IN ('exact','near')),
    size             INTEGER NOT NULL,
    exemplar_text_id INTEGER REFERENCES texts(text_id),
    core_excerpt     TEXT,
    UNIQUE (run_id, local_index)
);

CREATE TABLE IF NOT EXISTS cluster_members (
    run_id     INTEGER NOT NULL REFERENCES runs(run_id),
    cluster_id INTEGER NOT NULL REFERENCES clusters(cluster_id),
    text_id    INTEGER NOT NULL REFERENCES texts(text_id),
    similarity REAL,
    PRIMARY KEY (run_id, cluster_id, text_id)
);

-- A sponsor's technical comment, its exact-core form letter, and its
-- personalised satellites are one lobbying effort. Coalition is the unit of
-- analysis for anything about organised participation.
CREATE TABLE IF NOT EXISTS coalitions (
    coalition_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES runs(run_id),
    docket_id       TEXT    NOT NULL REFERENCES dockets(docket_id),
    label           TEXT    NOT NULL,
    lead_org        TEXT,
    interest_type   TEXT CHECK (interest_type IN ('public','private','unclassified')),
    campaign_format TEXT,
    coder           TEXT,
    note            TEXT
);

CREATE TABLE IF NOT EXISTS coalition_members (
    coalition_id INTEGER NOT NULL REFERENCES coalitions(coalition_id),
    member_kind  TEXT    NOT NULL CHECK (member_kind IN ('cluster','comment')),
    member_ref   TEXT    NOT NULL,
    PRIMARY KEY (coalition_id, member_kind, member_ref)
);

-- ---------------------------------------------------------------------------
-- Rule text and agency-side triage. Defined now, populated in Phase six.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS rule_texts (
    rule_text_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    docket_id     TEXT NOT NULL REFERENCES dockets(docket_id),
    stage         TEXT NOT NULL CHECK (stage IN ('proposed','final')),
    fr_document   TEXT,
    content       TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    source_url    TEXT,
    retrieved_utc TEXT NOT NULL
);

-- Same nullable-column reasoning as ux_texts_identity above.
CREATE UNIQUE INDEX IF NOT EXISTS ux_rule_texts_identity
    ON rule_texts (docket_id, stage, COALESCE(fr_document, ''));

CREATE TABLE IF NOT EXISTS citations (
    citation_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       INTEGER NOT NULL REFERENCES runs(run_id),
    rule_text_id INTEGER NOT NULL REFERENCES rule_texts(rule_text_id),
    target_kind  TEXT NOT NULL CHECK (target_kind IN ('comment','coalition')),
    target_ref   TEXT NOT NULL,
    quote        TEXT,
    disposition  TEXT CHECK (disposition IN ('accept','compromise','concession','reject'))
);

-- ---------------------------------------------------------------------------
-- Sampling, reproducible by construction
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS samples (
    sample_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES runs(run_id),
    docket_id       TEXT REFERENCES dockets(docket_id),
    strategy        TEXT NOT NULL,
    seed            INTEGER NOT NULL,
    strata_json     TEXT,
    n               INTEGER NOT NULL,
    manifest_sha256 TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sample_members (
    sample_id  INTEGER NOT NULL REFERENCES samples(sample_id),
    comment_id TEXT    NOT NULL REFERENCES comments(comment_id),
    stratum    TEXT,
    PRIMARY KEY (sample_id, comment_id)
);

-- ---------------------------------------------------------------------------
-- Infrastructure analyses. All run keyed, nothing here mutates the record.
-- ---------------------------------------------------------------------------

-- Module, audit cost. Computed entirely from this instrument's own request log,
-- which is the measurement of what auditing the public record costs.
CREATE TABLE IF NOT EXISTS audit_costs (
    run_id                      INTEGER NOT NULL REFERENCES runs(run_id),
    docket_id                   TEXT    NOT NULL REFERENCES dockets(docket_id),
    listing_requests            INTEGER NOT NULL,
    detail_requests             INTEGER NOT NULL,
    other_requests              INTEGER NOT NULL,
    failed_requests             INTEGER NOT NULL,
    rate_limit_hits             INTEGER NOT NULL,
    comments_stored             INTEGER NOT NULL,
    requests_per_comment        REAL,
    elapsed_seconds             REAL,
    measured_seconds_per_detail REAL,
    cursor_windows_recorded     INTEGER NOT NULL DEFAULT 0,
    posted_count                INTEGER,
    full_audit_hours_measured   REAL,
    PRIMARY KEY (run_id, docket_id)
);

-- Module, posting rhythm. Posted dates are the object of study here, which
-- inverts the standing guard on posted_date, and every report of this module
-- must say so.
CREATE TABLE IF NOT EXISTS posting_rhythm (
    run_id          INTEGER NOT NULL REFERENCES runs(run_id),
    docket_id       TEXT    NOT NULL REFERENCES dockets(docket_id),
    dated_pairs     INTEGER NOT NULL,
    missing_receive INTEGER NOT NULL,
    missing_posted  INTEGER NOT NULL,
    lag_median_days REAL,
    lag_p90_days    REAL,
    receive_days    INTEGER,
    posting_days    INTEGER,
    top_day_share   REAL,
    top3_day_share  REAL,
    PRIMARY KEY (run_id, docket_id)
);

CREATE TABLE IF NOT EXISTS posting_batches (
    run_id      INTEGER NOT NULL REFERENCES runs(run_id),
    docket_id   TEXT    NOT NULL REFERENCES dockets(docket_id),
    posted_date TEXT    NOT NULL,
    n           INTEGER NOT NULL,
    PRIMARY KEY (run_id, docket_id, posted_date)
);

-- Module, aggregation channel. A posted record may declare that it represents
-- many received submissions. Absence of the declaration is an absent
-- declaration and never a declaration of one.
CREATE TABLE IF NOT EXISTS aggregation_profiles (
    run_id                    INTEGER NOT NULL REFERENCES runs(run_id),
    docket_id                 TEXT    NOT NULL REFERENCES dockets(docket_id),
    details_available         INTEGER NOT NULL,
    no_declaration            INTEGER NOT NULL,
    declared_single           INTEGER NOT NULL,
    aggregated_records        INTEGER NOT NULL,
    declared_total            INTEGER NOT NULL,
    multiplier_median         REAL,
    multiplier_max            INTEGER,
    posted_count              INTEGER,
    received_count            INTEGER,
    received_minus_posted     INTEGER,
    explained_by_aggregation  INTEGER,
    PRIMARY KEY (run_id, docket_id)
);

CREATE TABLE IF NOT EXISTS aggregated_records (
    run_id         INTEGER NOT NULL REFERENCES runs(run_id),
    comment_id     TEXT    NOT NULL REFERENCES comments(comment_id),
    declared_items INTEGER NOT NULL,
    PRIMARY KEY (run_id, comment_id)
);

-- ---------------------------------------------------------------------------
-- Instrument self-knowledge
-- ---------------------------------------------------------------------------

-- Known ways this record manufactures false findings. Every new measure is
-- checked against this table before it ships.
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    slug              TEXT UNIQUE NOT NULL,
    title             TEXT NOT NULL,
    description       TEXT NOT NULL,
    affected_measure  TEXT NOT NULL,
    guard             TEXT NOT NULL,
    detected_utc      TEXT NOT NULL,
    status            TEXT NOT NULL
                      CHECK (status IN ('excluded_from_evidence','mitigated','open'))
);

-- Corrections are logged, never silently overwritten.
CREATE TABLE IF NOT EXISTS corrections (
    correction_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    slug              TEXT UNIQUE NOT NULL,
    logged_utc        TEXT NOT NULL,
    subject           TEXT NOT NULL,
    superseded_claim  TEXT NOT NULL,
    corrected_claim   TEXT NOT NULL,
    cause             TEXT NOT NULL,
    source_run        TEXT
);

-- ---------------------------------------------------------------------------
-- Convenience views
-- ---------------------------------------------------------------------------

-- Any proportion must be reported with the share it describes. This view
-- exists so that no report can quietly omit the denominator.
CREATE VIEW IF NOT EXISTS v_retrieval_share AS
SELECT d.docket_id,
       d.agency,
       d.era,
       rs.posted_count,
       rs.received_count,
       rs.comments_stored,
       rs.texts_extracted,
       rs.sampling,
       CASE WHEN rs.posted_count > 0
            THEN ROUND(100.0 * rs.comments_stored / rs.posted_count, 1) END
         AS pct_of_posted,
       CASE WHEN rs.received_count > 0
            THEN ROUND(100.0 * rs.posted_count / rs.received_count, 2) END
         AS pct_posted_of_received
FROM dockets d
LEFT JOIN retrieval_status rs ON rs.docket_id = d.docket_id;
