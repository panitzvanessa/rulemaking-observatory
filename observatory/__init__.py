"""
Rulemaking Observatory, rebuilt.

Phase zero. Provenance spine, schema, collector, campaign detector, artifacts
registry, corrections log, and report generation with the evidence discipline
enforced in code rather than in the analyst's memory.

The primary question this instrument is being rebuilt to answer is
distributional and not forensic. Whether the distribution of sophistication
among comments from unaffiliated individuals shifted after generative AI became
widely available, and whether the distance between that distribution and the
distribution for organisational comments narrowed. The synthetic text estimator
retained from the first instrument is a secondary descriptive measure and is
never the primary finding of any report.
"""

from .core import (  # noqa: F401
    AI_DIVIDE,
    MODULE_VERSION,
    Config,
    Run,
    connect,
    init_db,
    refresh_retrieval_status,
    register_docket,
    utc_now,
)
from .collect import (  # noqa: F401
    Collector,
    FixtureTransport,
    HttpTransport,
    budget,
    eastern_cursor,
    extract_field_texts,
    is_placeholder,
)
from .detect import detect, shingles, signature, estimated_jaccard  # noqa: F401
from .infra import aggregation, audit_cost, posting_rhythm  # noqa: F401
from .registry import integrity_check, seed, trace_run  # noqa: F401
from .report import Epistemics, ReportDisciplineError, analysis_report, campaign_report  # noqa: F401

__all__ = [
    "AI_DIVIDE", "MODULE_VERSION", "Config", "Run", "connect", "init_db",
    "register_docket", "refresh_retrieval_status", "utc_now",
    "Collector", "FixtureTransport", "HttpTransport", "budget", "eastern_cursor",
    "extract_field_texts", "is_placeholder", "detect", "shingles", "signature", "estimated_jaccard",
    "seed", "trace_run", "integrity_check",
    "aggregation", "audit_cost", "posting_rhythm",
    "Epistemics", "ReportDisciplineError", "analysis_report", "campaign_report",
]
