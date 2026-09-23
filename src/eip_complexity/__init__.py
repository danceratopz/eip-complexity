"""Reproducible EIP testing-complexity assessments with Jev.

The package is deliberately small. Each module does one thing:

- ``config``     read and validate the TOML run configuration;
- ``template``   parse ``templates/EIP-Complexity-Assessment.md`` into anchors and tiers;
- ``eips``       read EIP Markdown from the ``ethereum/EIPs`` clone at an exact commit;
- ``questions``  turn anchors into Jev Choice questions and build the request payload;
- ``jev``        the thin TypeSafe SDK wrapper (the only module that talks to Jev);
- ``scoring``    validate Jev answers and compute scores, the Cross-EIP bonus and the tier;
- ``cache``      semantic caching of completed evaluations;
- ``run``        orchestration: config in, canonical JSON out;
- ``render``     HTML from canonical JSON only, with no Jev, git or template access.
"""

TOOL_NAME = "eip-complexity"
TOOL_VERSION = "0.1.0"

#: Version of the canonical JSON documents (per-EIP evaluation files and ``run.json``).
RESULT_SCHEMA_VERSION = 1

__all__ = ["RESULT_SCHEMA_VERSION", "TOOL_NAME", "TOOL_VERSION"]
