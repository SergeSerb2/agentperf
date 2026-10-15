# Changelog

Scores are comparable only when the corpus fixture hash, the scored
benchmark definitions, the measurement methods, and the measurement/scoring
fingerprint all match, against the same reference file. Version strings never
decide comparability; reports warn when two files differ on any part. A suite
change that moves scores bumps the minor version and says what moved.

## 0.4.0 - 2026-10-13

Teach coding agents to run agentperf well. **Scores do not move in this
release**: the suite stays 0.3.0, the measurement fingerprint is unchanged,
and every bundled profile scores exactly as in 0.3.0 (WSL2 anchor 1000,
Windows native 103, M1 Pro 398, M5 Pro 408). One comparability caveat,
stated plainly: scored shell launches are now sanitized (see below), and
that policy is part of method compatibility. Old files with no recorded
shell policy warn against new ones, because their equivalence is unproven
in general; the bundled machines whose settings were checked record it as
established (see the shell note).

- **An Agent Skill and an operator guide.** `skills/agentperf/` holds a
  short procedural skill plus references for multi-machine runs, Windows
  and WSL2, honest result reading, troubleshooting, and safety. The same
  copy ships inside the package, so pip, pipx, and `.pyz` users all get it.
  `docs/AGENTS-GUIDE.md` is the full guide behind the skill.
- **`agentperf skill install` / `path` / `uninstall`.** One command copies
  the skill into the Claude Code (`~/.claude/skills`), Codex
  (`~/.codex/skills`), and shared (`~/.agents/skills`) folders, or into a
  repo with `--project`. Each copy carries a hash manifest; a copy you
  edited is never replaced or removed without `--force`, and your extra
  files survive a forced install and a forced uninstall alike (only the
  known skill files are ever written or removed, never through a symlink
  or junction, and `--force` does not widen that). The repo is also a Claude Code plugin
  marketplace (`/plugin marketplace add SergeSerb2/agentperf`, then
  `/plugin install agentperf`).
- **Several machines in one command.** `remote` takes many hosts and runs
  them strictly one after another, in order. `--wsl HOST:DISTRO` adds a
  WSL run on a Windows host, so `remote my-pc --wsl my-pc:Ubuntu` measures
  native Windows, then WSL2, never both at once. `--compare fleet.html`
  writes one comparison of the fetched results, `--quick`, `--runs`, and
  `--workdir` forward to every host, and `--mode doctor` preflights each
  host without benchmarking. One failed host no longer blocks the rest;
  the summary records each host's outcome. A WSL run's label now names its
  distro (`my-pc-wsl-ubuntu`); two runs that would share a label are
  refused before anything starts.
- **Machine-readable output.** `run`, `report`, `compare`, `doctor`,
  `remote`, and the `skill` commands accept `--json=PATH` (or `--json=-`
  for stdout, with progress on stderr). Summaries carry the score, the
  verdict, top causes, per-host outcomes, and file paths, so agents and
  scripts never scrape human text. Exit codes are now a stable, documented
  contract (0 success, 1 failed or refused, 2 bad input, 130 interrupted;
  `doctor` health lives in its `ok` field). `docs/result-schema.md`
  documents every summary and result field. Summaries are not anonymized;
  only `share` output is fit to post.
- **Repo notes for agents.** Root `AGENTS.md` (rules for working on the
  code), a `CLAUDE.md` pointer, and `llms.txt`. The README gains a short
  "Use it with your coding agent" section.
- **Fixes from an independent verification of 0.3.0.** Point scores do not
  move; combined-run intervals do (see the bootstrap note), because the old
  ones were too narrow.
  - Work-folder safety, tightened again: an unmarked non-empty work
    folder is never adopted, even one holding a valid corpus (nothing in
    it proves a `repo/` beside the corpus is agentperf's); delete an old
    pre-marker work folder or use a fresh path. `--clean` deletes corpus
    archives only when agentperf's generation ledger recorded making
    them (a manifest hash alone never owns an archive), and the run lock
    is released last, after all cleanup, with entries removed in a fixed
    order.
  - Scored shell starts and the Node spawn diagnostic now run with
    inherited shell-startup variables (`BASH_ENV`, `ENV`, `SHELLOPTS`,
    `BASHOPTS`, and `NODE_OPTIONS` for Node) removed. `--noprofile --norc`
    alone never stopped bash from reading `BASH_ENV`. The policy is part
    of method compatibility now, recorded per benchmark and as
    `measurement_env`: differing policies warn, and old files with no
    recorded policy warn against new ones. Three bundled machines were
    checked and had none of these set (the WSL2 anchor, the Windows PC,
    and the M5 Pro Mac); their profiles record `sanitized-equivalent`,
    so new results compare to them cleanly. The M1 Pro's machine could
    not be re-checked, so its profile carries no such claim and warns as
    unknown. Suite and scores are unchanged either way.
  - The combined-run bootstrap now draws run indices once per round
    across all tests, keeping whole-run correlation (a busy run is slow
    at everything at once). Bundled 90% intervals become: anchor
    976-1006, Windows native 98-105, M1 Pro 387-436, M5 Pro 394-467.
    Every point estimate is unchanged.
  - Search tests now check every repetition against counts derived from
    the corpus files themselves (62 literal files/lines, 3,571 regex
    lines), never against the tool under test, and the tree copy verifies
    the files and bytes it moved, outside timing.
  - Comparability got stricter and more truthful: results record their
    category weights (`scoring_config`), stored fingerprints are
    validated against file contents and regenerated by `merge`, `share`,
    `combine`, and `reference`, and `combine`/`merge` refuse files whose
    Python or shell versions differ. Placement bars pair each score with
    the interval of that same score; partial LW100 estimates state their
    coverage in comparisons too; "scored common tests" no longer counts
    diagnostics.
  - `share` now scrubs paths whose segments contain spaces (a volume
    named "Client Merger Documents", a `C:\Users\Alice Smith` home, a
    spaced `/Users/...` home), UNC paths, `~/` and `~user` paths, email
    addresses, and case variants of known user/host names in free-text
    fields; paths are scrubbed wherever they appear, including in keys,
    in any letter case and with apostrophes in folder names. The scrub
    is schema-aware: benchmark ids, categories, units, method strings,
    and scoring config are never rewritten, so a shared file scores
    exactly like the original even when a host or user name is a common
    word ("work", "git"). A free-text string that still looks
    identifying afterwards is withheld whole instead of passing through.
  - Live mode: the harness's whole process group is cleaned up on every
    exit path (normal, error, timeout, Ctrl-C), before verification or
    fixture deletion, so a tool subprocess that ignores SIGTERM is
    killed even after its parent exits (on Windows, a descendant that
    outlives an exited harness can still survive; there is no process
    group to signal). Hidden verification runs the agent's code in a
    separate process and decides pass/fail in the parent: the probe
    receives fresh inputs only (never expected outputs), returns raw
    outputs over a channel captured before the agent's code is
    imported, a zero verifier exit status is required, and stopping a
    verifier with Ctrl-C still cleans up its process group. It is
    built to catch wrong code, not to resist a deliberately
    adversarial agent.
  - Remote and JSON hardening: one host's operational failure (missing
    `ssh`, a failed copy) no longer aborts the fleet; fetched results
    are validated structurally per mode (an empty `benchmarks` is not
    success) and refused files are deleted locally; `remote --mode
    doctor` now fetches each host's doctor summary (`hosts[].doctor`),
    keeping machine health separate from execution success; labels must
    be portable file names (full-string check, no Windows reserved
    names or trailing dots) and case-only label collisions are refused
    before any host runs; every `--json` failure path emits a documented
    error summary instead of empty output, summaries are strict JSON
    (no NaN/Infinity), malformed result files are clean exit-2 errors,
    and bad selectors exit 2 as documented; `combine` validates each
    input's stored fingerprint before touching it and preserves the
    recorded scoring weights; a missing default reference fails clearly
    (self-reference and reference-free runs are explicit opt-ins,
    `--self-reference` / `--no-reference`); JSON summaries state LW100
    component coverage; failed skill removals report `failed` and exit
    nonzero; `doctor` now checks macOS Low Power Mode.
  - Comparability and validation, tightened once more: shell-startup
    policies are compared per scored shell benchmark (`process.shell`
    and `process.parallel` independently, from
    `measurement_env.shell_launch` specifically), and `combine` refuses
    the same mismatches, so an unrelated diagnostic field can no longer
    mask one. Malformed result files (a list where `meta` belongs, a
    null `weight`, and the like) are rejected up front as clean exit-2
    errors on every command, and fetched remote results must pass the
    same full validation (live fetches must carry interpretable runs).
    A result must also carry every field reports print (`label`,
    `profile`, `system`, `skipped`, and each test's `title` and `unit`),
    so a trimmed file is a clean exit-2 error instead of a crash in
    `report`. `combine` and `merge` refuse inputs whose shell benchmarks
    would change policy in the output (the output keeps the first file's
    `measurement_env`, so a benchmark from another file with a different
    or unrecorded policy would otherwise be relabelled). `share` now
    keeps only the fields agentperf writes and drops the rest (a note typed
    into the file, a field from another tool), at every level. In what it
    keeps, schema text (benchmark ids, kept fields, keys, tool file names)
    gets the full path and address scrub and loses any user or host name
    that is not a word of agentperf's own vocabulary; that vocabulary
    comes from the package (registry and bundled profiles), never from the
    shared file. Text a person or vendor can set (power plan names, OS
    version, CPU model, tool version lines) is scrubbed as free text.
    Timestamps must look like timestamps and hashes like hashes, or they
    are withheld. Home-path, name, and address detection is wider: any
    `~user` form, legacy `Documents and Settings` and domain-qualified
    (`bob.DOMAIN`, `_bob.DOMAIN`) Windows homes, home folders inside UNC
    and WSL paths (`\\wsl.localhost\Ubuntu\home\bob`, `/mnt/c/Users/bob`),
    quoted, mixed-separator, and doubled-separator home paths (a quote
    inside a home folder name fails closed), two-letter names, names in
    any Unicode form or case folding, nested host names, IPv6 behind a
    label (`IPv6:2001:db8::4`), addresses wrapped in underscores, dotted
    MAC addresses, quoted email local parts, and `user@host` addresses. Scores do not move: every bundled profile scores exactly
    as before, and its `share` output is byte-identical.
  - Smaller correctness fixes: a failed launch inside the 32-way
    parallel spawn reaps the shells that did start; a cached tool must
    exit 0 to count as working; every network test records its own TLS
    verification outcome even when the TLS context is cached; `remote`
    treats a fetched non-object JSON as a clean invalid result.
  - Wording: reference-speed gains are now called what they are -
    counterfactual estimates ("about +90 at reference speed"), not upper
    bounds - in reports, the README, the methodology, the skill, and the
    guides. The combined headline is described accurately everywhere:
    per test, the median of the run medians, then scored.

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
