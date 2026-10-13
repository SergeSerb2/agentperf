# Changelog

Scores are comparable only within the same suite version, corpus hash, and
reference file. A suite change that moves scores bumps the minor version and
says what moved.

## 0.2.0 - 2026-10-13

First public release.

- **Explanatory report** (`agentperf report result.json`): a single
  self-contained HTML file with a verdict, placement against bundled
  reference profiles, ranked likely causes with cited evidence and concrete
  fixes (labelled observed / likely / possible), exact per-test attribution
  of the score, and counterfactual upper-bound gains. The terminal summary
  prints the same top wins.
- **Bundled anonymized reference profiles** (`agentperf references`): WSL2 on
  an i7-12700K (the 1000 anchor), Windows 11 on the same hardware (67),
  macOS on Apple M1 Pro (380) and M5 Pro under load (430).
- **`agentperf share`** writes an anonymized result safe to attach to an
  issue; **`agentperf doctor`** is a fast pre-flight check.
- **Install**: `pipx install agentperf` (or from git), plus a single-file
  `agentperf.pyz` release asset that runs with any Python 3.8+.
- `--quick` alias, `run --html`, `--runs 3` median-of-runs.
- Background-load detection now survives anonymization as counts, so shared
  profiles can still explain a busy-machine score.

## 0.1.0 - 2026-10-12 (pre-release)

- System suite, AgentMark/Reach/LW100 scoring, compare HTML, SSH remote runs,
  live agent mode, reference profiles.
