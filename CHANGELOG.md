# Changelog

Scores are comparable only within the same suite version, corpus hash, and
reference file. A suite change that moves scores bumps the minor version and
says what moved.

## 0.2.2 - 2026-10-13

Launch-readiness fixes from a full review. No scored test, weight, or corpus
changed (one new unscored diagnostic), and bundled-profile scores are
unchanged, so 0.2.2 scores compare directly with 0.2.0/0.2.1 scores.

- **`remote` works from `agentperf.pyz`.** It used to build an empty archive
  when run from the single file. Remote commands are now quoted safely,
  labels are validated, and unreachable hosts are reported as unreachable
  instead of as Windows.
- **`share` scrubs harder.** Windows home paths, IP and MAC
  addresses, tailnet names, and bare user/host names in free-text fields are
  now removed, and the printed change list counts them.
- **Versions and intervals are labelled plainly.** Reports show the program
  version and the result's suite version separately; the bootstrap interval
  is now called a "90% within-run interval" everywhere, because between-run
  spread is larger. Reports also warn when two files are not comparable
  (different corpus hash or test definitions).
- **Honester Windows advice.** A launch directory under `/mnt/` no longer
  takes the top finding (only a work directory there compromises the score),
  the run-in-WSL2 cause fires only when the machine shows the slow pattern,
  and the 67-vs-1000 figure is always stated with its conditions (one PC,
  real-time antivirus plus indexing, October 2026).
- **First-run robustness.** Expected errors (missing or malformed files,
  `--runs 0`, unwritable installs) print one clear line instead of a
  traceback. One run per work directory, enforced with a lock; a damaged
  corpus cache is regenerated; an unresponsive git (the macOS shim without
  Command Line Tools) skips git tests instead of hanging. `doctor` now
  checks disk space, load, power, /mnt and tmpfs workdirs, and prints home
  paths as `~`.
- **`live` warns before it spends**, skips a missing harness with a warning
  instead of aborting, and can `--clean` its fixture copies.
- **`compare --weights equal`** re-scores with equal category weights; the
  sensitivity table is in METHODOLOGY (rankings hold under every weighting
  tried; points move up to ~30%). New `docs/FAQ.md`, `diag.node_spawn`
  diagnostic, GitHub issue templates, and CI on Python 3.13.

## 0.2.1 - 2026-10-13

Report fixes only. No test, weight, or corpus changed, so 0.2.1 scores are
comparable with 0.2.0 scores.

- **Gains read as whole numbers** ("up to +94", never "+94.0" or "+0.825"),
  and wins worth under +5 AgentMark move to a folded "Minor wins" list in
  both the HTML report and the terminal summary.
- **Windows reports lead with the WSL2 win**: run agents in WSL2 with repos
  on the Linux filesystem. The bundled same-hardware measurement (native
  Windows 67 vs WSL2 1000) is cited as evidence, with a strong warning
  against `/mnt/c` (2,000 small files: 41.8 s on `/mnt/c` vs 0.024 s on WSL
  ext4 vs 3.0 s natively).
- **`/mnt/*` detection is now the top finding** when the benchmark work
  directory - or the directory the run was launched from - is on a Windows
  drive under WSL. Detection also works from the recorded workdir path, not
  only the probe flag.
- Report layout checked at 400 px width and in light and dark mode; tiles,
  cards, and tables no longer overflow narrow screens.
- Not on PyPI yet: the `agentperf` name is free, but publishing needs a
  maintainer PyPI account. Use
  `pipx install git+https://github.com/SergeSerb2/agentperf.git` (see the
  roadmap in README.md).

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
- **Install**: `pipx install git+https://github.com/SergeSerb2/agentperf.git`, plus a single-file
  `agentperf.pyz` release asset that runs with any Python 3.8+.
- `--quick` alias, `run --html`, `--runs 3` median-of-runs.
- Background-load detection now survives anonymization as counts, so shared
  profiles can still explain a busy-machine score.

## 0.1.0 - 2026-10-12 (pre-release)

- System suite, AgentMark/Reach/LW100 scoring, compare HTML, SSH remote runs,
  live agent mode, reference profiles.
