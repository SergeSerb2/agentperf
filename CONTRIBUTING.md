# Contributing to agentperf

The core stays **Python standard library only** and supports **Python 3.8+**
on macOS, Linux, WSL2, and Windows. Run the tests before you send a change:

```sh
python3 -m unittest discover -s tests -t .
uv run --no-project --python 3.8 python -m unittest discover -s tests -t .  # 3.8 check
python3 -m agentperf run --quick --no-network --clean                       # smoke run
```

## Add a benchmark (one decorated factory)

A benchmark is a factory. It receives the shared `Context`, does untimed
setup, and returns a `sample()` callable. Each call does one fixed unit of
work and returns its measurement in the declared unit.

```python
from ..registry import bench

@bench("files.my_test", title="What it times (shown in reports)", unit="ms",
       lower_is_better=True, models="The agent action it models",
       reps=5, warmup=1, budget_s=20.0, ops=1)
def _my_test(ctx):
    scratch = ctx.scratch("my_test")        # fresh dir under the work root
    def sample():
        # one fixed unit of work; return milliseconds
        ...
    return sample                            # or (sample, cleanup)
```

Rules that keep scores comparable:

- The id prefix is the category (`process`, `files`, `search`, `git`,
  `compute`, `transfer`, `ipc`, `network`, `diag`). `diag.*` is never scored.
- Raise `agentperf.registry.Skip("reason")` when the test cannot run here.
- Need a tool? Use `ctx.need("git")` - it skips cleanly when missing.
- Fixed inputs only. Derive content from the corpus or from a seeded,
  version-independent source. Never from wall time or the network (except
  `network.*`).
- Equal semantics on every OS: if one OS needs a stronger flush or a cache
  bypass to mean the same thing, do it there and note it in
  [docs/METHODOLOGY.md](docs/METHODOLOGY.md).
- A new **scored** test changes every score. Bump the suite minor version,
  note it in CHANGELOG.md, and say which categories move.

Import your module from `agentperf/benches/__init__.py` and check it lists:

```sh
python3 -m agentperf list | grep my_test
```

## Add or change a diagnosis rule

Diagnosis lives in `agentperf/diagnose.py`. A cause must cite its evidence
(a probe value or a measured pattern) and carry a confidence: `observed`
(probe), `likely` (probe + pattern), `possible` (pattern only). Never state a
brand, a product, or a setting that no probe observed. Estimated gains come
from `gain_if_fixed()` and stay labelled as upper bounds.

## Contribute a reference result

```sh
agentperf run --runs 3 --fetch-tools --clean   # on an idle, plugged-in machine
agentperf share <result>.json                  # anonymized export
```

Attach the `-share.json` to an issue titled "Reference profile: <machine>".
Say what the machine is (CPU, OS, disk) in the issue, not in the file. A
maintainer checks the file for private fields before it is bundled. Do not
attach raw results.

## Result schema and comparability

Every result records `schema`, `suite_version`, the corpus `fixture_hash`,
and a measurement/scoring `fingerprint`. Scores compare only when the corpus
fixture hash, the scored benchmark definitions, the measurement methods, and
the fingerprint all match, and the same reference file is used. Version
strings never decide comparability. Changing corpus generation must bump
`CORPUS_FORMAT` (the test suite pins the hash).

## Style

- Stdlib only; no new dependencies.
- Code works on Python 3.8 (no match statements, no builtin generics in
  annotations that run at import time).
- Keep comments factual and short. Reports and docs use plain sentences.
