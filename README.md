<p align="center">
  <img src="https://raw.githubusercontent.com/SergeSerb2/agentperf/main/assets/icon.svg" width="72" height="72" alt="">
</p>

<h1 align="center">agentperf</h1>

<p align="center">
  <b>How fast is your computer for AI coding agents?</b><br>
  agentperf benchmarks the local work behind every tool call,<br>
  then tells you what is slowing it down.
</p>

<p align="center">
  <a href="https://github.com/SergeSerb2/agentperf/actions/workflows/ci.yml"><img src="https://github.com/SergeSerb2/agentperf/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/SergeSerb2/agentperf/releases/latest"><img src="https://img.shields.io/github/v/release/SergeSerb2/agentperf?color=ea580c" alt="Latest release"></a>
  <img src="https://img.shields.io/badge/python-3.8%2B-3776ab" alt="Python 3.8+">
  <img src="https://img.shields.io/badge/dependencies-none-2ea44f" alt="No dependencies">
  <a href="https://github.com/SergeSerb2/agentperf/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT license"></a>
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#what-you-get">What you get</a> ·
  <a href="#reading-your-score">Reading your score</a> ·
  <a href="#commands">Commands</a> ·
  <a href="#how-it-works">How it works</a>
</p>

<br>

When Claude Code or Codex works on your code, the model does its thinking in
the cloud. Everything else happens on your computer. A tool call might start
a shell, read or edit files, search with ripgrep, run git, and pipe the
output back. One task can make hundreds of these calls, so a few milliseconds
per call add up quickly.

agentperf times that local work. It runs the same fixed tests on every
machine, compares your results with a reference machine, and explains your
score: which tests cost you points, what is probably causing it, and what to
fix first.

It is one Python package with no dependencies. It runs on macOS, Linux, WSL2,
and Windows with Python 3.8 or newer.

## Quick start

```sh
pipx install agentperf
agentperf run --quick --html report.html
```

To install the latest code from GitHub instead, use
`pipx install git+https://github.com/SergeSerb2/agentperf.git`.

A quick run takes a few minutes. When it finishes, open `report.html` and
read **Biggest wins** first.

If you prefer not to install anything, download `agentperf.pyz` from the
[latest release](https://github.com/SergeSerb2/agentperf/releases/latest) and
run it with Python:

```sh
python3 agentperf.pyz run --quick --html report.html
```

On Windows, type `py` instead of `python3`. In WSL, run agentperf inside the
Linux distro, from a folder on the Linux filesystem (not under `/mnt/c`).

## What you get

Every run gives you three numbers:

| Number | What it tells you |
|---|---|
| **AgentMark** | Your score for local agent work. The reference machine scores 1000. A score of 2000 means twice as fast. |
| **LW100** | An estimate of the seconds an agent waits on your machine per 100 tool calls, based on one developer's tool-call mix. Lower is better. |
| **Reach** | How quickly you reach the model APIs and package registries. It is scored separately, because it mostly measures your network and your OS's network stack, not your local hardware. |

After the numbers, agentperf explains the score. This is an excerpt from the
end of a run on a busy laptop (the full output also lists every test and a
findings checklist):

```text
AgentMark 408  (90% bootstrap over runs and samples 401-446, coverage 100%)  vs reference 'WSL2 - i7-12700K (reference, 1000)'
Reach     3,287  (network to model APIs and CDNs)
LW100     3.6 s estimated local wait per 100 tool calls (reference 1.0 s, same parts)

Why this score
  About 2-3x slower than the reference at local agent work. Agents wait on local tools more than they need to; some of the gap may be the OS and its tools. See the biggest wins.
  Biggest drag: process.parallel at x0.05 vs reference (worth up to +73 AgentMark).
  Biggest wins (gains are upper bounds and overlap):
  1. [possible] Process and file overhead (OS or security layer) (up to +335)
     Fix: Rule out security software, indexing, and synced folders on code folders; what remains is the OS's own per-process cost.
  2. [observed] Background load during the run (gain not estimated)
     Fix: Quit or restart the busy processes (a daemon pinned near 100% for hours is a bug, not load).
```

The HTML report goes further. It places you next to the bundled reference
machines, shows the evidence behind each cause, and lists how many points
each test costs you. Here is an
[example report](https://htmlpreview.github.io/?https://github.com/SergeSerb2/agentperf/blob/main/examples/example-report.html)
for a Windows PC with real-time antivirus.

## Reading your score

| AgentMark | What it means |
|---|---|
| 850 or more | A strong host. Tools come back almost at once. |
| 550 to 850 | Good. A few fixes can still help. |
| 300 to 550 | Noticeably slower than the reference. Agents often wait on local tools. |
| Under 300 | Fix the top causes, or run your agents on a different machine. |

Software around the hardware matters a lot. The reference is a desktop PC
running Ubuntu in WSL2. The same PC, running natively on Windows as-is (with
third-party real-time antivirus and search indexing), scored 103. That gap
is one PC with those settings, not a verdict on every Windows install; the
[FAQ](https://github.com/SergeSerb2/agentperf/blob/main/docs/FAQ.md) spells
out the conditions.

A few things to keep in mind:

- Each cause is marked **observed** (a probe saw it), **likely** (a probe
  plus a matching pattern), or **possible** (a pattern only).
- Each fix shows roughly what it is worth if the affected tests reached
  reference speed. Fixes overlap, so the gains do not add up.
- Coverage says how much of the reference's test weight your machine scored.
  A test that could not run lowers coverage instead of being guessed.
- Single runs vary by about 10 to 15%. Use `--runs 3` when you want a number
  to compare.

### What usually slows agents down

agentperf looks for these causes. Most of them were found the hard way, on
real machines that scored worse than they should have.

- Antivirus and search indexing on Windows. Real-time scanning checks every
  process start and every file write.
- Code under `/mnt/c` in WSL. On one measured PC, small-file work there was
  orders of magnitude slower than on the Linux filesystem.
- A slow shell profile. Codex starts a login shell for every command, so your
  login files (`.zprofile` and friends) run every time.
- Background load. One stuck system process held a measured laptop at
  296-430, against about 720 modelled for that hardware when idle.
- Synced folders (iCloud, OneDrive, Dropbox), power-saving plans, and
  spinning disks.
- Large repos with git's fsmonitor turned off.

## Commands

| Command | What it does |
|---|---|
| `agentperf run` | Runs the benchmark. Add `--quick` for a shorter run, `--runs 3` for a steadier number, and `--html report.html` for the report. |
| `agentperf doctor` | Checks tools, disk, and settings. Runs no benchmarks. |
| `agentperf report result.json` | Explains a saved result and writes its HTML report. |
| `agentperf compare a.json b.json --html compare.html` | Puts results from several machines side by side. |
| `agentperf references` | Lists the bundled reference machines. |
| `agentperf share result.json` | Writes an anonymized copy for posting (read it once first). |
| `agentperf remote <host>` | Runs the benchmark on another machine over SSH. |
| `agentperf live` | Runs real Claude Code and Codex sessions. This costs tokens. |

Add `--fetch-tools` when you compare machines. It downloads a pinned ripgrep
build, so every machine searches with the same binary, and a pinned Node
when a machine has none. Downloads are checked by SHA-256 and stored in
`~/.agentperf/tools`; your PATH is not touched.

<details>
<summary><b>Running on other machines over SSH</b></summary>
<br>

agentperf copies itself to the other machine as one file, runs there with
that machine's Python, and brings the result back. It works with Linux and
macOS hosts, Windows hosts that use PowerShell over SSH, and WSL distros on a
Windows host.

```sh
agentperf remote my-mac --fetch-tools --clean
agentperf remote my-pc --fetch-tools
agentperf remote my-pc --wsl Ubuntu --fetch-tools
```

`remote` works from any install, including `agentperf.pyz`.

</details>

<details>
<summary><b>Live agent runs</b></summary>
<br>

`agentperf live` gives real Claude Code and Codex sessions three fixed tasks
in a generated repo: fix a bug, rename a function across several files, and
add a small feature. For each run it reports whether the task passed, the
wall time, the time spent in local tools, the time spent waiting on the
model, and the tokens used.

```sh
agentperf live --dry-run                  # shows what would run and spends nothing
agentperf live --claude-model sonnet
```

Live runs use your own accounts and cost tokens. Read
[SECURITY.md](https://github.com/SergeSerb2/agentperf/blob/main/SECURITY.md)
before you start one.

</details>

## How it works

agentperf builds the same test project on every machine: 6,000 source-like
files in 400 folders, about 49 MB. Then it times seven kinds of work.

| Category | Weight | What it times |
|---|---:|---|
| Process spawn | 25 | Shells, Python, Node, and 32 shells at once |
| Small files | 15 | Creating, reading, editing, listing, and deleting thousands of files |
| Search | 15 | ripgrep and grep across the project |
| Git | 15 | Status, diff, commit, log, and worktrees on a 6,000-file repo |
| Compute | 15 | Hashing, compression, JSON, Python, Node, and jobs on all cores |
| File transfer | 10 | Large reads, writes, and copies, plus unpacking an archive |
| IPC | 5 | Pipes and local network connections |

The weights are a judgment call, guided by one developer's 6,416 real Claude
Code tool calls. Most of them (79%) were shell commands, so process spawn
counts the most.

The tests try to be fair across operating systems. The scored shell is bash
everywhere (Git Bash on Windows), scored copies move real bytes, the two
large-file write tests make the same flush-to-device request on every OS,
cold reads try to skip the file cache, and with `--fetch-tools` every
machine searches with the same ripgrep build. Other tools (grep, git,
Python, Node) are the ones each machine already has, so part of a gap can be
the tools, not the hardware. Every result records the tool versions it used.
[docs/METHODOLOGY.md](https://github.com/SergeSerb2/agentperf/blob/main/docs/METHODOLOGY.md)
explains the rules, the statistics, and the scoring, and
[docs/FAQ.md](https://github.com/SergeSerb2/agentperf/blob/main/docs/FAQ.md)
answers the usual objections.

## Privacy

A raw result file contains your hostname, file paths, and process names, so
do not post it as is. `agentperf share` writes an anonymized copy and lists
everything it changed. See
[SECURITY.md](https://github.com/SergeSerb2/agentperf/blob/main/SECURITY.md).

## Contributing

Contributions are welcome. You can add a benchmark, improve a diagnosis rule,
or send an anonymized result from your machine to grow the reference set.
[CONTRIBUTING.md](https://github.com/SergeSerb2/agentperf/blob/main/CONTRIBUTING.md)
explains how. To run the tests:

```sh
python3 -m unittest discover -s tests -t .
```

## Roadmap

- More reference machines, especially Linux desktops and laptops.
- A result history on each machine, so you can compare before and after a
  change.

## License

MIT. agentperf is not affiliated with other projects that have a similar
name.
