# Changelog

Scores are comparable only when the corpus fixture hash, the scored
benchmark definitions, the measurement methods, and the measurement/scoring
fingerprint all match, against the same reference file. Version strings never
decide comparability; reports warn when two files differ on any part. A suite
change that moves scores bumps the minor version and says what moved.

## 0.3.0 - 2026-10-13

Fixes from two independent reviews of 0.2.2 (a code-and-math review and a
docs-and-claims review), plus measurement changes. **Scores move in this
release** and are not comparable with 0.2.x scores; reports warn when files
from the two suites are mixed. All bundled reference profiles were
re-measured with suite 0.3.0 (three standard runs each, `--fetch-tools`):
the WSL2 anchor is 1000 by definition (its LW100 is now about 1.0 s, because
the scored shell is bash, not dash), native Windows on the same PC scores
103, the Apple M1 Pro 398, and the Apple M5 Pro 408 while measured on battery
under heavy background load. Do not read the old 67 / 380 / 430 as the same
machines changing speed; the suite changed what it measures.

What the suite measures differently now:

- **The scored shell is bash everywhere** (`bash --noprofile --norc`), Git
  Bash on Windows. Scoring `/bin/sh` compared dash on Ubuntu with bash
  elsewhere, on the highest-weighted category.
- **Scored copies move real bytes.** The 256 MB copy and the 6,000-file tree
  copy use explicit buffered read/write; copy helpers could turn them into
  metadata clones (reflinks) on some filesystems. Clones remain a diagnostic.
- **Ripgrep runs with `--no-ignore`** and every search test checks its match
  count against the known corpus. Ignore files on the machine under test can
  no longer change what is searched, and a no-match exit fails the test
  instead of scoring.
- **The login-shell diagnostic times `$SHELL -lc`**, the way Codex runs it
  (login files such as `.zprofile`, not `.zshrc`), instead of `-lic`.

Safety and honesty fixes:

- **The work directory is agentperf-owned or refused.** A run deletes its
  own `corpus/`, `repo/`, and `scratch/` inside the work directory. It now
  claims the directory with a `.agentperf-owned` marker and refuses any other
  non-empty directory before touching it; `--clean` removes only owned
  entries. The run lock is held until cleanup is done.
- **Failures stop looking like success.** Process launches are timed out and
  exit-checked; an empty or all-failed run and an unmatched `--only` selector
  are errors; a failed `remote` run keeps its exit code and can no longer
  fetch a previous result; an explicitly named `--ref` that does not exist is
  an error instead of a silent fallback; tool downloads extract atomically
  and cached tools must run before reuse.
- **The numbers mean what they say.** Combined (`--runs N`) intervals
  bootstrap the same median-of-medians estimator as the headline and never
  exclude it. HTML comparisons rank on the tests every machine ran, with
  coverage and comparability warnings shown. LW100 uses the same parts on
  both sides or says "not available", never 0.0 s. `combine` and `merge`
  refuse incompatible inputs (machine, corpus, definitions, methods). The
  cached corpus is verified by re-hashing its contents, and its archive is
  keyed by fixture hash.
- **One comparability contract.** Scores compare only when the corpus
  fixture hash, the scored definitions, the measurement methods, and the new
  measurement/scoring fingerprint all match, against the same reference
  file. Version strings no longer decide, and every doc and report says the
  same thing.
- **`share` removes more.** Whole paths under a home directory and volume
  names are generalized, IPv6 addresses are replaced whole (prefix and all),
  and run ids are removed at every depth, including inside combined results.
  The bundled profiles were re-anonymized with the new code.
- **`live` is harder to fool and to hang.** A timeout kills the whole process
  tree, unfinished tool calls still count their time, fixture git runs with
  the same isolation as the benchmarks, and hidden verifier tests run from
  outside the agent's repo with their test counts checked.
- **Network tests check their answers.** Each host gets its own TLS context,
  so one blocked API cannot disable the others, and HTTP status and content
  are validated before a sample counts.
- **Cause and verdict text matches the measurements.** The antivirus cause
  no longer blames scanning alone or asserts that compute is fine without
  checking it; the bundled Windows gap is always stated with all of its
  conditions (third-party real-time antivirus, search indexing, Git Bash, a
  different Python) and with the fact that no antivirus-off run exists.
  Verdicts are relative ("about 2-3x slower than the reference"), WSL2 is
  suggested only on Windows, the power-plan cause is "possible" with no
  invented gain, and gains that are not estimated say so.
- **Docs corrected.** The Python spawn-overhead and Python-version answers
  now match the bundled data; Reach explains why most machines beat 1000
  (the anchor pays for WSL2's virtual network); the weights are called a
  judgment call; every remaining cross-OS tool difference is listed; and a
  new FAQ entry answers "does local time even matter next to model latency?"
  with the October 2026 live runs (local tools took 20-26% of each Claude
  Code task on a Windows PC with real-time antivirus, 2-3% on a Mac; 12 runs).
- **Install from PyPI.** `pipx install agentperf` works; the `git+https` form
  remains as an alternative. README links are absolute so the PyPI page
  renders.
- 29 new regression tests (71 total), each tied to a review finding.

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
- **More honest Windows advice.** A launch directory under `/mnt/` no longer
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
- Not on PyPI yet: use
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
