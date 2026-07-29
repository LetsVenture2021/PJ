# Document evidence governance

PJ keeps evidence links outside prose in adjacent `*.claims.json` sidecars. Prose uses a
stable footnote marker such as `[^CLM-CAPABILITY-1]`; the sidecar maps that ID to one or
more entries in `documents/sources/registry.json`. A sidecar also contains the document's
`review_date`, claim impact, and any referenced manifest record IDs.

The source registry supports repository file/line ranges, operator observations, test and
configuration evidence, external authoritative references, and approved business
assertions. Every source records authority, locator, retrieval/effective/review dates,
digest, and licensing constraints. Historical evidence has a bounded observation date and
is immutable. Other tiers are reviewed at 30, 90, or 180 days as declared in the registry.

Run `python scripts/validate_document_governance.py` for the offline gate. It validates
sources, claim IDs and links, controlled terms, and bidirectional manifest references.
Document freshness ends at the earlier of its own review date and any supporting source's
review date. Expired security, legal, financial, operational, or current-capability claims
block final audience-ready DocOps exports. Draft exports remain possible.

`documents/governance/dependencies.json` maps runtime configuration, Worker routes, tool
policy and schemas, provider adapters, uploads, and artifact formats to documents needing
review. Retired controls, scripts, endpoints, settings, and tools in `records.json` must
name an existing successor. Optional assisted review accepts a `ResponsesProvider`; the
deterministic gate never uses the network or requires an API key.
