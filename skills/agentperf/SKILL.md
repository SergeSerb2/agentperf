---
name: agentperf
description: Benchmark this machine or the user's other machines for AI coding agents, compare where agents run fastest, and explain why agents feel slow. Use when the user asks to benchmark a machine, test this computer for agents, compare machines for agent work, decide where to run agents, or asks why agents are slow. Covers local runs, SSH remote runs, Windows and WSL2, and reading AgentMark results honestly.
---

# Benchmark machines for coding agents with agentperf

agentperf measures how well a computer serves AI coding agents. It times the local work behind tool calls: shell starts, file work, search, git, compute, and pipes. It then scores the machine against a reference (AgentMark; the reference scores 1000) and explains the result.

Use this skill when the user wants a benchmark, a machine comparison, or help choosing where to run agents. Do not guess speeds. Measure them.

## First, know the boundaries

- Confirm which machines the user means before you connect anywhere. You may list `Host` entries from `~/.ssh/config` and WSL distros from `wsl -l -v` as candidates. Do not connect until the user confirms.
- Run one machine at a time. Never run native Windows and its WSL2 at the same time. They share one PC, so they would measure each other.
- Never run `agentperf live` without clear consent. It starts real agent sessions and costs tokens.
- Do not change antivirus, indexing, power, or system settings. Report what you find. Let the user decide.
- Only `agentperf share` output is safe to post in public. Raw results and `--json` summaries are not anonymized.
- Read [references/safety-and-privacy.md](references/safety-and-privacy.md) before your first multi-machine job.

## Procedure

### 1. Check what is installed

Run:

```sh
agentperf --version
agentperf doctor --json=-
```

If `agentperf` is missing, install it and confirm:

```sh
pipx install agentperf
agentperf --version
```

If there is no `pipx`, use the single file instead. Download `agentperf.pyz` from the latest release and run it as `python3 agentperf.pyz` (on Windows, `py agentperf.pyz`). Read the doctor summary. If `ok` is `false`, read `checks` and tell the user what each check means before you benchmark. A busy machine, low disk space, or a work folder under `/mnt/` in WSL will spoil the numbers.

### 2. Pick the run

- First look on one machine: `agentperf run --quick --html report.html --json=summary.json`
- Number the user will compare or share: `agentperf run --runs 3 --fetch-tools --html report.html --json=summary.json`
- Several machines in one command: see [references/multi-machine.md](references/multi-machine.md). In short:

```sh
agentperf remote my-mac linux-box --quick --fetch-tools --json=fleet.json
agentperf remote my-pc --wsl my-pc:Ubuntu --fetch-tools --compare fleet.html --json=fleet.json
```

Use `--fetch-tools` whenever you compare machines. It gives every machine the same pinned ripgrep build, so search results compare fairly.

For other machines, always use `agentperf remote`. Do not copy agentperf to a host by hand with `scp` or run it through your own `ssh` commands. The remote command handles Windows and POSIX quoting, per-run file names, and fetching. Hand-rolled copies get those wrong.

Outputs and the work folder are different things. Put outputs (`--out`, `--html`, `--json`, `--out-dir`) wherever the user wants them. Leave the work folder at its default (`~/.agentperf/work` on each machine) unless the user needs a specific disk measured. The work folder is scratch space agentperf owns and cleans. It does not belong inside a project folder.

### 3. Wait, then read the machine output

Runs take minutes. Do not poll in a tight loop. When a command finishes, read its `--json` summary and its exit code. Do not scrape the human text.

Exit codes are stable:

| Code | Meaning |
|---|---|
| 0 | Success. |
| 1 | The command ran, but the work failed or was refused. Check the summary and the error line. |
| 2 | Bad input or usage: missing file, wrong flags, results that do not compare. Fix the input. |
| 130 | Interrupted. Partial files stay in the work folder. Rerun when ready. |

`doctor` exits 0 when the check itself ran. For machine problems, read `ok` and `checks` in its summary.

### 4. Explain the result

Read [references/reading-results.md](references/reading-results.md). The short rules:

- AgentMark 1000 is the reference machine. 500 is about half as fast at local agent work.
- Differences under about 15% are not meaningful. Single runs vary.
- Gains are upper bounds. They overlap and do not add up.
- If `comparability_warnings` is not empty, say so. Do not rank machines as if the warning were absent.
- Never claim a percentile. There are too few reference machines.

Give the user: the score, the verdict in plain words, the top one to three causes with fixes, and the report file path. For several machines, say where agents will feel fastest, and say what would have to change for the slower machines to catch up.

## References

- [references/multi-machine.md](references/multi-machine.md) — several machines in one command, labels, order, and failure handling.
- [references/windows-and-wsl.md](references/windows-and-wsl.md) — Windows over SSH, WSL2 rules, and macOS notes.
- [references/reading-results.md](references/reading-results.md) — scores, coverage, variance, and honest wording.
- [references/troubleshooting.md](references/troubleshooting.md) — symptom, likely cause, and fix.
- [references/safety-and-privacy.md](references/safety-and-privacy.md) — work folders, consent, cleanup, and what is safe to share.

The full operator guide is `docs/AGENTS-GUIDE.md` in the agentperf repository. The summary and result fields are listed in `docs/result-schema.md`.
