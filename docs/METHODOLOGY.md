# agentperf methodology

agentperf measures how fast a computer does the **local** work that an AI coding
agent (Claude Code, Codex CLI, and similar harnesses) waits on. The model runs in
the cloud. Every tool call runs on your machine: a shell starts, files are read
and written, ripgrep and grep search, git runs, tests and builds run, and output
flows back through pipes. agentperf times that work with fixed, repeatable inputs
and compares it with a reference machine.

## What is measured

The suite generates a deterministic corpus: 6,000 source-like files in about
400 folders, 49 MB. SHA-256 of a counter drives every choice, so the bytes are the
same on every machine and every Python version. `tests/test_core.py` pins the
corpus hash.

| Category | Weight | Tests | Agent action it models |
|---|---|---|---|
| Process spawn | 25 | `bash --noprofile --norc -c true` (Git Bash on Windows), native exec (`true`, `cmd.exe`), `python -c pass`, `node -e 0`, 32 parallel shells | Every Bash tool call, hooks, MCP servers, npx |
| Small files | 15 | create 2,000 files, stat-walk 6,000, read 2,000, 300 atomic edits, delete 2,000 | Read/Write/Edit tools, cat, sed, ls, find |
| Search | 15 | `rg` literal, regex, `--files`; `grep -rn` | Grep and Glob tools; grep in Bash |
| Git | 15 | init+add+commit 6,000 files, status (clean and dirty), diff, commit 50 files, log, worktree add+remove | Status checks, reviews, commits, isolated worktrees |
| Compute | 15 | SHA-256, zlib, JSON, pure-Python text work (half weight), 96 parallel jobs (1.5x weight), a Node workload | Tests, builds, linters, scripts |
| File transfer | 10 | 256 MB cold read, 256 MB flushed write, 256 MB copy, copy of the 6,000-file tree, `.tar.gz` extraction | Package installs, artifact copies, clones |
| IPC | 5 | 128 MB pipe, loopback TCP round trip and stream | Reading tool output, local servers |
| Network (Reach) | separate | DNS, TLS handshake and time to first byte for api.anthropic.com and api.openai.com, 25 MB download, 8 MB upload, npm registry | Model API round trips, installs, pushes |
| Diagnostics | not scored | login shell as Codex runs it (`$SHELL -lc`), PowerShell, `claude`/`codex` start, shell spawn timed inside Node, durable 4 KB write, git with fsmonitor, copy-on-write clone, plain-`fsync` write on macOS | Tuning advice |

The weights are a judgment call, guided by one developer's 6,416 real Claude
Code tool calls: 79% were Bash, 9% Read, 3.4% Write or Edit. Most Bash calls
start a shell, so process spawn carries the largest weight. The round numbers
(25/15/15/15/15/10/5) were picked by hand from that picture, not fitted. One
person's history is a small sample, so [Sensitivity](#sensitivity) below
re-scores every bundled profile under different weights;
`agentperf compare --weights equal` reproduces it.

## The reference machine

1000 is one measured machine: a desktop i7-12700K running Ubuntu in WSL2, ext4
on NVMe, measured in October 2026. It is not bare-metal Linux, and it is not an
industry reference. It is the anchor because it was the fastest complete
profile measured when the scale was set, and because WSL2 on a Windows desktop
is a common home for coding agents. No bare-metal Linux profile had been
measured yet; adding one does not move the anchor. Read every score as "speed
relative to that one PC, in WSL2, on these fixed tests": 500 means half as
fast as that setup at this local work, nothing more.

## Fairness rules

- **Same durability request, where a test asks for one.** On macOS, plain
  `fsync()` stops at the drive's cache, while Linux `fsync()` and Windows
  `FlushFileBuffers` ask the drive to flush its volatile cache. The flush on
  macOS therefore uses `F_FULLFSYNC`, which makes the same request. Only two
  scored tests ask for durability: the 256 MB write and the 256 MB copy
  (`transfer.write`, `transfer.copy`), which flush to the device. The
  small-file create, edit, and tree-copy tests do not flush, like editor
  writes. Whether a given drive honours any of these requests depends on its
  firmware; the plain-`fsync` number is shown as a diagnostic on macOS.
- **Cold reads bypass the cache without root:** `F_NOCACHE` on macOS,
  `posix_fadvise(DONTNEED)` on Linux, `FILE_FLAG_NO_BUFFERING` on Windows. The
  Linux call only drops clean pages, which is why the read fixture is flushed
  to the device when created; each sample advises again before reading. The
  method is recorded with the result, and cold-read numbers are only comparable
  between machines that used the same method.
- **Copies move real bytes.** Scored copies (`transfer.copy`,
  `transfer.copy_tree`) copy by explicit read/write. High-level copy helpers
  can turn a copy into a metadata clone on some filesystems (a reflink via
  `copy_file_range`, for example), which would score cloning, not moving
  bytes. APFS and btrfs clones are reported as a diagnostic, never as copy
  throughput. Git worktrees keep any clone benefit, because that saving is
  real for agents.
- **Same search binary, same inputs.** `--fetch-tools` downloads ripgrep
  15.2.0 (SHA-256 checked) so every machine runs the same build. Node 22.23.3
  is fetched only where Node is missing. Both land in `~/.agentperf/tools`
  (PATH is never touched). Pinned ripgrep exists for macOS (ARM64, x86-64),
  Linux x86-64 (musl, runs anywhere), Linux ARM64 (glibc), and Windows
  x86-64; pinned Node needs glibc on Linux. Elsewhere the flag says so and
  system tools are used. Ripgrep also runs with `--no-ignore`, so ignore
  files on the machine under test (including in parent folders) cannot change
  what is searched, and each search test checks its match count against the
  known corpus. Without `--fetch-tools`, the machine's own ripgrep build is
  part of the measurement.
- **The scored shell is the same family everywhere.** `process.shell` and
  `process.parallel` run bash with no profile files, and with inherited
  shell-startup variables (`BASH_ENV`, `ENV`, `SHELLOPTS`, `BASHOPTS`)
  removed from the launch environment, because `--noprofile --norc` alone
  does not stop bash from reading `BASH_ENV`: the system bash on
  macOS and Linux, Git Bash on Windows, which is also the shell Claude Code
  uses there. Scoring `/bin/sh` would score dash on Ubuntu against bash
  elsewhere. Builds still differ (macOS ships bash 3.2; current Linux ships
  5.x), and the version is recorded. Where no bash exists, the tests fall
  back to `sh` and record that, so reports can warn.
- **Tools that still differ.** Part of any gap can be the tools, not the
  hardware: `search.grep` uses each OS's own grep (GNU grep on Linux and in
  Git for Windows, BSD grep on macOS); `process.exec` runs `true` on Unix and
  `cmd.exe` on Windows; Python, Node, and git are whatever each machine has.
  Every result records the exact tool versions, and the Python build matters
  beyond the one interpreter test (see below). Cold-read methods also differ
  by OS and are recorded per test.
- **Spawn harness overhead.** Scored process tests spawn from Python's
  `subprocess`, while Claude Code spawns from Node and Codex from Rust. The
  Python side is not free, and its cost is not the same on every machine. In
  the bundled profiles, a bash start from Python costs 3.7 ms on the WSL2
  reference and 5.9 ms on the Apple M1 Pro; spawned and timed inside Node
  (`diag.node_spawn`) the same starts cost 2.8 ms and 1.8 ms. So process
  ratios can be off in either direction, by roughly the size of those
  differences. `diag.node_spawn` reports the Node-side number on each
  machine, which is probably closer to what Claude Code pays.
- **Python version.** The Python build matters. Most compute tests call C code
  (hashlib, zlib, json), but the interpreter-bound test (half weight) is very
  sensitive to the version, and the harness itself spawns every timed process
  from Python, so an old or slow build taxes process tests too. The Python
  version and implementation are recorded in every result.
- **User config stays out.** Git runs with an empty global config, no hooks, and
  no auto-gc. System config stays, because platform defaults (for example
  `core.fscache` on Git for Windows) are what agents really get.
- **The work directory is in your home folder**, not `/tmp`, which is RAM on many
  Linux systems. A RAM or overlay work directory raises a warning.

## Statistics

Each test runs warmups, then 3 to 9 samples (fewer with `--profile quick`) inside
a time budget. Each sample is a fixed unit of work. The headline value is the
median. A coefficient of variation above 15% marks a test as noisy. If one warmup
takes over 5 seconds, it counts as the first sample, so very slow machines finish.

Whole runs vary more than samples inside one run. In the October 2026
re-measurement, the three runs behind the WSL2 reference profile scored 1006,
982, and 984 against their own median; the three behind the Apple M1 Pro
profile scored 436, 387, and 407. For close comparisons, use `--runs 3`
(the headline then scores, per test, the median of that test's run
medians; it is not the median of the whole-run scores) and build references
from three runs (`agentperf reference a.json b.json c.json`). The interval
printed for a combined result bootstraps runs and samples together, so it
describes the same estimator as the headline; for a single run it covers
spread inside that run only. Either way the reference stays fixed.

## Scoring

- Per test: ratio = reference / value for times, value / reference for rates.
  2.0 always means twice as fast as the reference.
- Category score = 1000 × weighted geometric mean of the test ratios.
- **AgentMark** = 1000 × weighted geometric mean of the category ratios. Network
  is excluded, because it mostly measures a place, an ISP, and the OS network
  stack, not the local hardware.
- **Reach** = 1000 × geometric mean of the network ratios.
- A missing test is never imputed. It lowers **coverage** instead. `compare`
  also prints AgentMark on the tests that all compared machines share.
- The 90% interval comes from a bootstrap (300 rounds) with the reference
  fixed. For a single run it resamples each test's samples and covers spread
  inside that run only; between-run spread is larger (see Statistics). For a
  combined (`--runs N`) result it draws run indices once per round, applies
  them to every test (a slow run is slow at everything at once, and the
  interval keeps that correlation), then resamples samples within each drawn
  run, matching the per-test median-of-run-medians headline. An interval never
  excludes its own point estimate. Reports name which kind they printed.
- **LW100** = modelled seconds of local wait per 100 agent tool calls, from the
  same one-developer history as the weights: 79 shell starts, 26 file reads,
  9.5 Python starts, 7.5 folder-tree greps (only 17% of real grep calls search
  a tree), 6.3 git commands, 3.4 edits, 1.6 Node starts, and 1 commit. The
  lines overlap (one Bash call can be a shell start plus a Python start plus a
  grep), which is why they sum to more than 100 operations.

Scores are comparable only when the corpus fixture hash, the scored
benchmark definitions, the measurement methods, and the measurement/scoring
fingerprint all match, against the same reference file. Reports check this
and print a comparability warning when they do not (see
[Versioning and comparability](#versioning-and-comparability)).

## Sensitivity

The weights are the softest part of the method, so here is what changing them
does. Every bundled profile, re-scored against the default reference under
four weightings (AgentMark; "compute/git-heavy" = process 15, files 15,
search 10, git 20, compute 25, transfer 10, IPC 5, a stand-in for a
build-heavy Codex-style mix; "flat" = every scored test equal, categories
ignored):

| Profile | Default | Equal categories | Compute/git-heavy | Flat per-test |
|---|---:|---:|---:|---:|
| WSL2 - i7-12700K (reference) | 1000 | 1000 | 1000 | 1000 |
| Windows 11 - i7-12700K | 103 | 131 | 122 | 124 |
| macOS - Apple M1 Pro | 398 | 516 | 437 | 471 |
| macOS - Apple M5 Pro (busy) | 408 | 572 | 487 | 509 |

"Flat" is the unweighted geometric mean of the scored test ratios, network
excluded as in AgentMark. The rank order does not move, and neither does the
native-Windows-vs-WSL2 conclusion (about an 8x gap instead of 10x at the
extreme). Absolute points do move, by up to about 40%: treat AgentMark as a
ratio with a soft second digit, not as a precise constant.
`agentperf compare --weights equal` reproduces the equal column on your own
results.

## Live agent mode

`agentperf live` runs real Claude Code and Codex sessions on three fixed tasks in
a generated Python repo: fix a planted bug, rename a function across about 8
files, and add a small feature. Hidden verifier tests run from outside the
agent's repo, so nothing the agent writes can change which tests execute, and
a verifier only passes when the expected tests actually ran. Each output line is time-stamped on arrival. Tool time is the union
of the intervals from a tool call event to its result event, so parallel calls
are not counted twice. Model time is the rest of the wall time after startup.
Live runs cost tokens; the default is one run per task per harness.

Three measured details matter when reading live results:

- Codex emits its `item.started` event about 0.1 s after a command really
  starts. Commands shorter than that read as about 0 s, so Codex tool time is a
  lower bound. Claude Code's tool times include the harness's own overhead.
- Codex runs every command through a login shell (`zsh -lc` on macOS), so the
  user's shell profile start-up time (the `diag.login_shell` test) is paid on
  every Codex command.
- On Windows, Codex 0.152 in `workspace-write` mode rejects every command in
  non-interactive runs unless its elevated sandbox has been set up (an admin
  step). The October 2026 runs used `--codex-sandbox danger-full-access` on both
  machines; the fixture is a throwaway copy. Those runs are not published as
  files; the FAQ's "local time vs model latency" entry quotes their summary
  numbers with the sample size.

## Estimates

Machines that were not measured can be modelled by scaling a measured Linux
result by published CPU ratios and known penalties, with absolute models for
spinning disks and remote networks. Each factor has a middle, pessimistic,
and optimistic value. Factors multiply, and their log-uncertainties add in
quadrature. The compare report accepts an estimates file (`--estimates`) and
draws estimates hatched and labelled. They are not measurements.

## Explaining a score (attribution and causes)

AgentMark is a weighted geometric mean, so the log score splits **exactly**
into per-category and per-test parts. The report uses that split two ways:

- **Attribution:** for each test, the report shows your value, the reference
  value, the speed ratio, and the AgentMark points the test costs. For any
  set of tests it computes the counterfactual score if those tests matched
  the reference. That is a **counterfactual estimate** for a fix - what
  matching the reference would be worth, not a ceiling on it - and
  estimates for overlapping causes do not add up.
- **Causes:** each named cause cites the probe or measurement pattern behind
  it and is labelled **observed** (a probe shows it), **likely** (probe plus
  matching pattern), or **possible** (pattern only). No cause is printed
  without its evidence. The current causes are:
  - antivirus / endpoint security scanning
  - run agents in WSL2 (slow native-Windows pattern)
  - repository or workdir under `/mnt/<drive>`, or in a synced folder
  - RAM-disk (tmpfs) workdir, spinning disk, RAID resync in progress
  - search indexing of code folders
  - background load during the run
  - battery power, low-power mode, thermal limits
  - Windows power plan or Linux CPU governor
  - slow login shell, slow PowerShell start
  - git fsmonitor off on a large repo
  - few cores, or one slow core
  - older Python interpreter
  Placement against other machines uses only the bundled profiles, scored on
  the tests every profile shares, and is reported as a rank, never a
  percentile.

Lessons baked into the rules, from measuring real machines:

- Windows real-time scanning and the Search indexer can cost more than the
  hardware is worth. The bundled same-PC comparison is under
  [the FAQ's Windows question](FAQ.md), with its conditions.
- `/mnt/c` from WSL is orders of magnitude slower than ext4 for small files.
- A stuck background daemon on macOS held a laptop at 296-430, against about
  720 modelled for that hardware idle.
- Windows scheduled tasks default to below-normal priority and skew timings.
- Windows OpenSSH kills session processes at logout, so benchmark output must
  never abort a run.

## Versioning and comparability

- `package_version`: the installed program version (`agentperf --version`
  prints it first). `suite_version` in a result names the benchmark suite that
  produced that result. Reports label the two separately, so an old result
  never looks like the running program.
- A change that can move scores (a new scored test, a weight change, a corpus
  change) bumps the suite minor version and is noted in CHANGELOG.md.
- `schema`: the result JSON shape. Readers must ignore unknown keys.
- Corpus: `CORPUS_FORMAT` plus the recorded `fixture_hash`. The test suite
  pins the hash; changing generation bumps the format.
- Comparability is checked on what was measured, not on version strings.
  Scores compare only when the corpus fixture hash, the scored benchmark
  definitions (category, weight, unit, direction, title), the measurement
  methods recorded per test, and the measurement/scoring fingerprint (a short
  hash of all of that plus the category weights) all match, against the same
  reference file. Reports warn when any part differs. The bundled reference
  profiles were re-measured with suite 0.3.0, the current suite. Older
  results still report their raw values; they just carry the warning when
  scored against 0.3.0 profiles.

## Known limits

- Background load on the machine under test changes results. The runner records
  the load average and warns when it is high. Close heavy apps and rerun.
- Security software (antivirus, EDR) can dominate process and file results. This
  is real for agents on that machine, but it is a policy setting, not hardware.
- `transfer.read` without root is best-effort cache bypass. Compare cold-read
  numbers only between machines that used the same method.
- Reach is a snapshot of one network at one moment. DNS timings mostly measure
  the OS resolver cache after the first lookup, and time-to-first-byte starts
  after the TLS handshake (the handshake is its own test). Compare Reach
  between places, not between afternoons.
