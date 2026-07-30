# Reproducibility

This note is addressed to someone who has no reason to trust the researcher. It
sets out what can be verified without a credential, what requires one, and what
cannot be verified at all, which is the category that matters most.

## Verifiable with nothing but this repository

Run the test suite and the demonstration. Both are offline, both build a
temporary database, and neither requires an API key. Twenty six tests cover the
provenance spine, the request log, placeholder separation, the similarity
primitives, cluster recovery including a coordinated batch of three, the removal
of quoted rule text before clustering, the integrity check under deliberate
tampering, and the report generator's refusal to emit a report that omits what
the data cannot show.

Two properties are worth verifying directly because they are the ones the project
rests on. First, that a deliberately tampered record is caught, which the
integrity test exercises by rewriting a stored payload and asserting the hash
mismatch surfaces. Second, that a run's parameter hash is independent of the
order in which parameters were supplied, which is what makes a configuration
citable across runs.

## Verifiable with a Regulations.gov API key

Rebuild any corpus from the public interface and compare. The collector records
the exact address of every request, so the addresses in a run's trace can be
requested independently. Where the government record has changed since retrieval,
which happens because agencies post in batches and revise, the stored payload and
the current payload will differ, and that difference is a finding about the record
rather than an error in the instrument. The stored retrieval timestamp is what
makes the comparison interpretable.

## Reproducing the figures published in the writing sample

The figures in the writing sample come from the first instrument, from runs dated
24 July 2026. The archive database from that instrument is retained read only and
is addressable through `OBSERVATORY_ARCHIVE_DB`. Any figure that this rebuilt
instrument recomputes must either match the archived figure or be recorded in the
corrections log with its cause. Divergence is expected in at least two places.
Detection now excludes attachment placeholders more strictly and strips quoted
rule text where a proposed rule is available, both of which will reduce cluster
membership relative to earlier runs, and randomised retrieval will change every
share once Phase two lands. Neither is a defect, and neither may be applied
silently.

One figure from the first instrument is deliberately not reproducible and is
recorded as such. The synthetic text comparison for the labor pair has no test
statistic in the verified run and no recorded split of its texts between
calibration and target, so the comparison is reported descriptively and the gap
is logged rather than filled by reconstruction.

## Not verifiable, by anyone

The received record exceeds the posted record on every docket examined, and on
the 2025 endangerment finding docket the agency reports having received on the
order of five hundred and seventy two thousand comments against roughly six
thousand posted. No instrument operating on the public record can recover the
difference. The agency's own count of mass campaigns on that docket, one hundred
and sixty nine, is not recoverable from the posted individual texts either, where
detection finds three small clusters.

The public interface exposes no sub day timestamp, no submission method, no
network identifier, no user agent, and no contact field. No method applied to
this record can establish how a submission arrived. This is a property of the
record's construction rather than a limitation of any detector, and the
instrument now treats it as a finding rather than as a gap to be closed.

## What a reader should conclude from a number in a report

That it came from a named run with a hashed parameter set and identifiable code,
that it carries the denominator and the retrieval share of the corpus it
describes, that any known failure mode affecting it has been restated alongside
it, and that a human being read the report before it was released. Nothing more
than that, and nothing less.
