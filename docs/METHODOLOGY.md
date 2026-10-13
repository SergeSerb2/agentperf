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
| Process spawn | 25 | `sh -c true` (Git Bash on Windows), native exec (`true`, `cmd.exe`), `python -c pass`, `node -e 0`, 32 parallel shells | Every Bash tool call, hooks, MCP servers, npx |
| Small files | 15 | create 2,000 files, stat-walk 6,000, read 2,000, 300 atomic edits, delete 2,000 | Read/Write/Edit tools, cat, sed, ls, find |
| Search | 15 | `rg` literal, regex, `--files`; `grep -rn` | Grep and Glob tools; grep in Bash |
| Git | 15 | init+add+commit 6,000 files, status (clean and dirty), diff, commit 50 files, log, worktree add+remove | Status checks, reviews, commits, isolated worktrees |
| Compute | 15 | SHA-256, zlib, JSON, pure-Python text work (half weight), 96 parallel jobs (1.5x weight), a Node workload | Tests, builds, linters, scripts |
| File transfer | 10 | 256 MB cold read, 256 MB flushed write, 256 MB copy, copy of the 6,000-file tree, `.tar.gz` extraction | Package installs, artifact copies, clones |
| IPC | 5 | 128 MB pipe, loopback TCP round trip and stream | Reading tool output, local servers |
| Network (Reach) | separate | DNS, TLS handshake and time to first byte for api.anthropic.com and api.openai.com, 25 MB download, 8 MB upload, npm registry | Model API round trips, installs, pushes |
| Diagnostics | not scored | login shell with dotfiles, PowerShell, `claude`/`codex` start, durable 4 KB write, git with fsmonitor, copy-on-write clone, plain-`fsync` write on macOS | Tuning advice |

The weights come from 6,416 real Claude Code tool calls: 79% were Bash, 9% Read,
3.4% Write or Edit. Every Bash call starts a shell, so process spawn carries the
largest weight.

## Fairness rules

- **Same durability for disk writes.** On macOS, plain `fsync()` stops at the
  drive's cache, while Linux `fsync()` and Windows `FlushFileBuffers` flush it.
  Scored writes on macOS therefore use `F_FULLFSYNC`. The plain-`fsync` number is
  shown as a diagnostic.
- **Cold reads bypass the cache without root:** `F_NOCACHE` on macOS,
  `posix_fadvise(DONTNEED)` on Linux, `FILE_FLAG_NO_BUFFERING` on Windows. The
  method is recorded with the result.
- **Copies move real bytes.** APFS and btrfs clones are reported as a diagnostic,
  never as copy throughput. Git worktrees keep any clone benefit, because that
  saving is real for agents.
- **Same search binary.** `--fetch-tools` downloads ripgrep 15.2.0 (SHA-256
  checked) so every machine runs the same build. Node 22.23.3 is fetched only
  where Node is missing.
- **Python version.** Most compute tests call C code (hashlib, zlib, json), so the
  Python version matters little. The one interpreter-bound test has half weight,
  and the Python version is recorded.
- **User config stays out.** Git runs with an empty global config, no hooks, and
  no auto-gc. System config stays, because platform defaults (for example
  `core.fscache` on Git for Windows) are what agents really get.
- **The work directory is in your home folder**, not `/tmp`, which is RAM on many
  Linux systems. A RAM or overlay work directory raises a warning.

## Statistics

Each test runs warmups, then 5 to 9 samples (fewer with `--profile quick`) inside
a time budget. Each sample is a fixed unit of work. The headline value is the
median. A coefficient of variation above 15% marks a test as noisy. If one warmup
takes over 5 seconds, it counts as the first sample, so very slow machines finish.

Whole runs vary more than samples inside one run: three back-to-back runs on the
WSL2 reference scored 1000, 1154, and 1006 against the first run (one run had a
slow phase in disk writes). The bootstrap interval covers only the spread inside a
run. For close comparisons, use `--runs 3` (median of the run medians) and build
references from three runs (`agentperf reference a.json b.json c.json`).

## Scoring

- Per test: ratio = reference / value for times, value / reference for rates.
  2.0 always means twice as fast as the reference.
- Category score = 1000 × weighted geometric mean of the test ratios.
- **AgentMark** = 1000 × weighted geometric mean of the category ratios. Network
  is excluded, because it measures a place and an ISP, not a machine.
- **Reach** = 1000 × geometric mean of the network ratios.
- A missing test is never imputed. It lowers **coverage** instead. `compare`
  also prints AgentMark on the tests that all compared machines share.
- The 90% interval comes from a bootstrap of each test's samples (300 rounds).
  The reference stays fixed.
- **LW100** = modelled seconds of local wait per 100 agent tool calls: 79 shell
  starts, 26 file reads, 9.5 Python starts, 7.5 folder-tree greps (only 17% of
  real grep calls search a tree), 6.3 git commands, 3.4 edits, 1.6 Node starts,
  and 1 commit.

Scores are comparable only for the same suite version, corpus hash, and reference
file. Every result JSON records all three.

## Live agent mode

`agentperf live` runs real Claude Code and Codex sessions on three fixed tasks in
a generated Python repo: fix a planted bug, rename a function across about 8
files, and add a small feature. Hidden verifier tests are copied in only after the
agent exits. Each output line is time-stamped on arrival. Tool time is the union
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
  machines; the fixture is a throwaway copy.

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
  the reference. That is an **upper bound** for a fix, and bounds for
  overlapping causes do not add up.
- **Causes:** each named cause (antivirus/EDR, indexing, workdir location
  such as `/mnt/<drive>` or a synced folder, spinning disk, RAID resync,
  background load, power/thermal limits, power plan or governor, slow login
  shell, PowerShell start cost, missing git fsmonitor, few cores vs slow
  single core, old Python) cites the probe or measurement pattern behind it
  and is labelled **observed** (a probe shows it), **likely** (probe plus
  matching pattern), or **possible** (pattern only). No cause is printed
  without its evidence. Placement against other machines uses only the
  bundled profiles and is reported as a rank, never a percentile.

Lessons baked into the rules, all measured: Windows real-time scanning and
the Search indexer can cost more than the hardware is worth (67 vs 1000 on
one PC); `/mnt/c` from WSL is orders of magnitude slower than ext4 for small
files; a stuck background daemon on macOS moved a laptop from about 720 to
296-430; Windows scheduled tasks default to below-normal priority and skew
timings; Windows OpenSSH kills session processes at logout, so benchmark
output must never abort a run.

## Versioning and comparability

- `suite_version` (semver): any change that can move scores - a new scored
  test, a weight change, a corpus change - bumps the minor version and is
  noted in CHANGELOG.md.
- `schema`: the result JSON shape. Readers must ignore unknown keys.
- Corpus: `CORPUS_FORMAT` plus the recorded `fixture_hash`. The test suite
  pins the hash; changing generation bumps the format.
- Scores compare only when suite version, fixture hash, and reference file
  all match. Reports print all four values.

## Known limits

- Background load on the machine under test changes results. The runner records
  the load average and warns when it is high. Close heavy apps and rerun.
- Security software (antivirus, EDR) can dominate process and file results. This
  is real for agents on that machine, but it is a policy setting, not hardware.
- `transfer.read` without root is best-effort cache bypass. Compare cold-read
  numbers only between machines that used the same method.
