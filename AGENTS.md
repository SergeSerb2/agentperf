# Agent notes for this repository

This file is for coding agents that work **on** agentperf. If you run agentperf **for** a user (benchmark their machines, explain a score), read [docs/AGENTS-GUIDE.md](docs/AGENTS-GUIDE.md) or install the bundled skill with `agentperf skill install`. Do not confuse the two jobs.

## The project

agentperf measures how well a computer serves AI coding agents. It is one Python package, standard library only, Python 3.8 and newer, on macOS, Linux (WSL2 included), and Windows. The scored suite builds a fixed 6,000-file corpus and times shell starts, file work, search, git, compute, transfer, and pipes. Scores compare against a bundled reference machine (AgentMark 1000).

## Hard rules

- **Standard library only.** No new dependencies. `pyproject.toml` keeps `dependencies = []`. This includes test-only helpers that would leak into the package.
- **Python 3.8 floor.** No 3.9+ syntax in `agentperf/` (no `X | Y` annotations at runtime, no `match`, no `removeprefix` without a fallback). CI runs 3.8 on Linux and Windows. Test it before you ship.
- **Scores must not move by accident.** A change to a scored benchmark, its weight, the corpus, or a measurement method moves every score. Such a change needs a suite minor bump (`SUITE_VERSION` in `agentperf/__init__.py`), a CHANGELOG entry that says scores move, and re-measured bundled profiles. Anything else must leave bundled profiles scoring exactly as before.
- **The comparability contract is one sentence.** Scores compare only when the corpus fixture hash, the scored definitions, the measurement methods, and the measurement and scoring fingerprint all match, against the same reference file. Version strings never decide. Keep every doc, report, and error message consistent with that sentence.
- **Privacy.** Never commit hostnames, usernames, home paths, IP addresses, SSH aliases, process lists, raw result files, or credentials. Bundled profiles are `share` output and nothing else. Placeholder hosts in docs are generic (`my-mac`, `linux-box`, `my-pc`). Before a push, grep the tree for personal strings.
- **Failures must look like failures.** An empty run, a bad selector, a missing reference, and a failed remote run are errors with clear exit codes (see `docs/result-schema.md`). Never let a failed step fall back to stale data or a silent default.
- **Never delete outside owned folders.** A run may delete only its own `corpus/`, `repo/`, and `scratch/` inside a directory marked `.agentperf-owned`. Any new deletion path needs the same ownership check and a test that proves user files survive.
- **Keep the voice.** README, guides, and skill text are plain, warm, and precise. Short sentences. No feature dump. Gains are upper bounds. No percentiles.

## Layout

| Path | What it holds |
|---|---|
| `agentperf/cli.py` | Commands and argument parsing. Exit codes live here. |
| `agentperf/runner.py` | Work-folder safety, locks, and the suite run. |
| `agentperf/benches/` | Benchmark factories. One module per area. |
| `agentperf/registry.py` | Benchmark registration, categories, weights. |
| `agentperf/corpus.py` | The deterministic test project. Its hash is pinned by tests. |
| `agentperf/scoring.py` | AgentMark, Reach, LW100, fingerprint, comparability. |
| `agentperf/diagnose.py`, `agentperf/advice.py` | Causes with confidence, gains, findings. Evidence or it did not happen. |
| `agentperf/report_text.py`, `agentperf/report_html.py` | Terminal and HTML reports. One self-contained HTML file. |
| `agentperf/summary.py` | The `--json` summary builders. Views only; never score differently here. |
| `agentperf/remote.py` | SSH runs, quoting, and the `.pyz` builder. Hosts run in order. |
| `agentperf/skill.py`, `agentperf/skill/` | Skill installer and the packaged skill copy. |
| `agentperf/privacy.py` | The `share` anonymizer. Fail closed: drop, do not pass through. |
| `agentperf/live/` | Real-agent sessions. Costs tokens. Never run by default. |
| `agentperf/references/` | Bundled anonymized profiles, `index.json`, and the `DEFAULT` name. |
| `skills/agentperf/` | The canonical Agent Skill (the plugin loads this copy). |
| `docs/AGENTS-GUIDE.md` | Operator guide for agents that run agentperf for users. |
| `docs/result-schema.md` | Result fields, summary fields, exit codes. Update it with any shape change. |
| `docs/METHODOLOGY.md`, `docs/FAQ.md` | Why the suite measures what it measures. |
| `.claude-plugin/` | Claude Code plugin and marketplace manifests. Plugin root is the repo root. |
| `tests/` | `unittest` suites. `test_core.py` is broad; round files pin review fixes. |
| `scripts/build_pyz.py` | Builds the single-file release asset. |

Two skill copies exist on purpose: `skills/agentperf/` for the plugin and repo readers, `agentperf/skill/` inside the package for pip and `.pyz` users. Edit the canonical copy, sync the package copy, and let the sync test prove they match. The file list lives in `agentperf/skill.py` (`SKILL_FILES`).

## Versions

- `pyproject.toml` holds the package version. `agentperf/__init__.py` holds `SUITE_VERSION` and a package fallback for checkouts and `.pyz` files. Keep the fallback equal to `pyproject.toml`.
- Docs, skill, installer, and new commands that do not move scores: bump the package minor (new commands) or patch (fixes). Leave `SUITE_VERSION` alone.
- Anything that moves scores: bump `SUITE_VERSION` minor and the package minor together, re-measure every bundled profile (three standard runs each, `--fetch-tools`), re-anonymize with current `share`, and say in the CHANGELOG that old scores do not compare.
- The plugin version in `.claude-plugin/plugin.json` tracks the package version. Bump it at release time or plugin users will not see updates.
- Every release gets a CHANGELOG entry. Say plainly whether scores move.

## Tests and smoke runs

Run the full suite on the newest Python and on 3.8 before you call work done:

```sh
python3 -m unittest discover -s tests -t .
uv run --no-project --python 3.8 python -m unittest discover -s tests -t .
```

Useful smoke runs (the same shape as CI):

```sh
python3 -m agentperf run --quick --no-network --clean --label smoke --out smoke.json
python3 -m agentperf report smoke.json --html smoke.html
python3 -m agentperf share smoke.json --out smoke-share.json
python3 scripts/build_pyz.py /tmp/agentperf.pyz
python3 /tmp/agentperf.pyz references
```

Rules for new tests:

- Test behavior at the public surface: `agentperf.cli.main([...])`, exit codes, written files, and summary JSON. Mock SSH; never need a network.
- Skill install tests use a temporary home and temporary project folders. Never install into a developer's real skill folders from a test.
- If you touch scoring, benchmarks, or the corpus, assert the bundled profiles still score the same (unless the change is a deliberate suite bump with re-measured profiles).
- If you touch `summary.py` or add a `--json` shape, update `docs/result-schema.md` in the same change and test that `--json=-` stdout parses as one JSON object.
- If you touch quoting or labels in `remote.py`, test the hostile cases (spaces, quotes, `;`). Labels reach remote shells.

## Add a benchmark

`CONTRIBUTING.md` has the full recipe. The short form: register a factory with `@bench(...)`, model one agent action, keep inputs fixed and OS-fair, skip cleanly with `Skip` when the tool is missing, and import the module from `agentperf/benches/__init__.py`. A new scored test moves every score, so treat it as a suite change (see Versions). A `diag.*` test never scores and is safe to add.

Diagnosis rules (`agentperf/diagnose.py`) must cite evidence: a probe value or a measured pattern, with confidence `observed`, `likely`, or `possible`. Gains come from `gain_if_fixed()` and stay labelled as upper bounds. Never name a product no probe observed.

## Ship checklist

1. Full tests green on newest Python and 3.8. New behavior has new tests.
2. Bundled profiles still score the same (or the suite bump is deliberate and documented).
3. Version bumps in `pyproject.toml`, `agentperf/__init__.py` fallback, `.claude-plugin/plugin.json`, and `CHANGELOG.md` agree.
4. Skill copies in sync; `claude plugin validate .` passes when the plugin files change.
5. Privacy grep over the tree, the built wheel and sdist, and the `.pyz`: no personal strings, no raw results.
6. `twine check` on the built distributions. README links stay absolute so PyPI renders.
7. Push, watch CI go green on every matrix job, then release with `agentperf.pyz` and `example-report.html`. Smoke-test the published `.pyz` after the upload.
