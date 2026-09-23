# eip-complexity

Reproducible testing-complexity assessments of Ethereum EIPs, scored by
[Jev](https://docs.typesafe.ai) (TypeSafe's System One model) against every criterion of
the [EIP Complexity Assessment checklist](templates/EIP-Complexity-Assessment.md).

**Results:** the Hegotá run (SFI, CFI and PFI EIPs of
[EIP-8081](https://eips.ethereum.org/EIPS/eip-8081)) is published by GitHub Pages at
`/hegota/`. The canonical data is [`docs/hegota/run.json`](docs/hegota/run.json) and the
per-EIP files under [`docs/hegota/evaluations/`](docs/hegota/evaluations); the HTML overview
is rendered from that JSON on every push to `main` and is not committed.

## How it works

One TOML config names a fork, an explicit EIP list, an `ethereum/EIPs` ref, the template and
a Jev model. For every EIP the tool makes **exactly one Jev request** containing one Choice
question per checklist criterion (options are the template's discrete score definitions,
kept sparse, plus the template's exceptional score 4) and one yes/no Choice per candidate
interacting EIP. Scores are the chosen options; the uncapped Cross-EIP bonus, total and tier
are computed in code from rules parsed out of the template. Jev writes no prose, so the
template's Rationale, Special Considerations and Notes are recorded as `null`.

## Setup

Requires [uv](https://docs.astral.sh/uv/) and a sibling clone of
[ethereum/EIPs](https://github.com/ethereum/EIPs) at `../EIPs`.

```sh
uv sync                       # creates .venv with typesafe-sdk and pytest
export TYPESAFE_API_KEY=...   # the only way to supply the key; never put it in a config
```

## Config

Everything a run needs is in one file (see [`configs/hegota.toml`](configs/hegota.toml)):

```toml
fork = "Hegota"
meta_eip = 8081            # optional, display-only: the hardfork meta EIP the list came from

eips_repo = "../EIPs"
eips_ref = ""              # optional: branch, tag or commit; empty = remote default branch

complexity_template = "templates/EIP-Complexity-Assessment.md"

jev_model = "jev-latest"   # optional; pin e.g. "jev-1.13.0" for fully cacheable reruns

output_dir = "docs/hegota"
cache_dir = ".cache/eip-complexity"
render_html = true
force = false              # true bypasses the evaluation cache

[human_assessments]        # optional, display-only: where human checklists live on GitHub
repo = "ethspecs/pm"
path = "complexity_assessments/EIPs"
pr_query = "is:pr complexity"

[[eips]]
number = 8141
stage = "SFI"              # display-only; never sent to Jev
layer = "execution"        # optional; "consensus"/"informational" => not applicable, not scored
```

EIPs may carry a `layer` (`execution`, `consensus`, `cross-layer`, `networking`,
`informational`). Consensus and informational EIPs are recorded as **not applicable** to this
execution-layer checklist (`evaluations/eip-NNNN.not-applicable.json`) and are not sent to Jev.
The layer is configuration, not a model judgment.

Relative paths resolve against the directory the command is run from (normally the repo root).

## Run

```sh
uv run eip-complexity run configs/hegota.toml
```

The command resolves the EIPs ref against `origin` with `git ls-remote` (fetching only if the
commit is missing locally and never touching the checkout), reads each EIP with
`git show <sha>:EIPS/eip-N.md`, parses the template (revision 2 must yield 28 criteria) and
evaluates each EIP with one Jev request. Each result is written as soon as its request
succeeds, so an interrupted run loses nothing that completed.

## Output

```
docs/hegota/
  run.json                  # manifest: config, provenance, model, list of evaluations
  evaluations/eip-NNNN.json # one canonical evaluation per EIP, incl. exact request and raw response
  index.html                # overview rendered from the JSON above; gitignored, built by CI
.cache/eip-complexity/      # gitignored semantic cache
```

Each evaluation file carries every criterion's final score, Choice probabilities, confidence
and the criteria text used; the Cross-EIP helper judgments, qualifying count, base score,
bonus and final score; total and tier; token usage; the normalized request payload with its
SHA-256; and the verbatim Jev response.

## Re-render HTML without Jev

```sh
uv run eip-complexity render docs/hegota
```

The renderer reads `run.json` and the evaluation files it lists (verifying their hashes) and
needs neither Jev, nor `../EIPs`, nor the template.

## Publishing

[`.github/workflows/pages.yml`](.github/workflows/pages.yml) runs the tests, renders every
`docs/*/run.json` and deploys `docs/` to GitHub Pages on each push to `main` (pull requests
run the same steps without deploying). It needs no secrets. In the repository settings, set
Pages -> Source to "GitHub Actions". `docs/index.html` is the hand-written landing page that
lists the runs.

## Reproducibility

Every result is identified by four hashes recorded in every file: the SHA-256 of the EIP
Markdown actually sent (plus the resolved `ethereum/EIPs` commit), the SHA-256 of the
template (plus its checklist revision and git blob/commit), the SHA-256 of the generated
question set, and the concrete Jev model (`jev-latest` is pinned to the version the first
response reports; all EIPs in a run must use the same one). The cache is keyed on exactly
these four inputs, so changing any of them re-evaluates the affected EIPs, while editing
HTML/CSS never does. Scores from different checklist revisions are not comparable.

## Limitations

Only the target EIP's own Markdown is supplied to Jev. Dependency EIPs are not bundled, and
implementation status, devnet history and discussion threads are neither fetched nor
assumed. Cross-EIP candidates come solely from the EIP's frontmatter `requires` field and
explicit `EIP-NNNN` references in its text. Consensus-layer EIPs are scored against the same
execution-layer checklist as everything else.

## Tests

```sh
uv run pytest                                                  # offline; Jev is mocked
EIP_COMPLEXITY_LIVE=1 uv run pytest tests/test_live_smoke.py   # one real Jev request
```

## Template provenance

`templates/EIP-Complexity-Assessment.md` is a byte-identical copy of
`Templates/EIP-Complexity-Assessment.md` from [ethereum/pm](https://github.com/ethereum/pm)
at commit `3d8c0128c5543dd3146341ef395aa344e4abea30` (checklist revision 2, git blob
`b0d2258a8fdd1a514b61ef42ab0fd5a6d7fb5046`, SHA-256
`16da70eafc61ca7ff9be62b4a89017f4a881a6ff3a27bb02040334d3100dba2a`). The tool was first
developed on that repository's `complexity-with-jev` branch.
