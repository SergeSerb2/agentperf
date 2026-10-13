# Operator guide for coding agents

This guide is for a coding agent (Claude Code, Codex, Cursor, or similar) that runs agentperf for a user. It tells you how to benchmark every machine the user names, in the right order, without harm, and how to explain what you measured.

If you loaded the agentperf skill, it points here for detail. The skill's short procedure and this guide describe the same commands. When they seem to differ, trust `agentperf --help` for syntax and this guide for judgment.

## What agentperf measures

agentperf times the local work behind agent tool calls: shell and interpreter starts, small-file work, search, git, compute, large-file transfer, and pipes. It builds the same 6,000-file test project on every machine and scores the timings against a reference machine.

Three numbers come out:

- **AgentMark**: local-work speed. The reference scores 1000.
- **LW100**: an estimate of local wait seconds per 100 tool calls. Lower is better.
- **Reach**: network speed to model APIs and registries. Scored on its own.

`docs/METHODOLOGY.md` explains the tests and the scoring. `docs/result-schema.md` lists every field you need from result and summary files.

## Install

Check first:

```sh
agentperf --version
```

If the command is missing:

```sh
pipx install agentperf
agentperf --version
```

Without `pipx`, download `agentperf.pyz` from the latest release and run `python3 agentperf.pyz ...` (`py agentperf.pyz ...` on Windows). The `.pyz` carries the full program, including `remote` and `skill`. Python 3.8 or newer is the only need. There are no other dependencies.

To teach your own harness this workflow, install the bundled skill:

```sh
agentperf skill install              # Claude Code, Codex, and ~/.agents folders
agentperf skill install --project    # this repo: .claude/skills and .agents/skills
```

Claude Code users can also install the plugin (see the README section "Use it with your coding agent").

## Discovery: which machines?

Ask the user which machines they mean. "My machines" is not a list. You may propose candidates:

- SSH aliases: read the `Host` lines of `~/.ssh/config`. Show the names. Do not print keys, users, or addresses back unless the user needs them.
- WSL distros: on a Windows host, `wsl -l -v` lists them.
- This machine: always a candidate. Say so.

Confirm the final list with the user before you connect. A benchmark run copies `agentperf.pyz` to `~/.agentperf/` on each host, writes a work folder there (`~/.agentperf/work`), and fetches one result file back. Say that once, plainly, so the footprint is no surprise.

Use generic names in anything you write down for others (`my-mac`, `linux-box`, `my-pc`). Real host names belong in commands, not in reports you paste elsewhere.

## Preflight

Run `doctor` on this machine and on each remote before you benchmark:

```sh
agentperf doctor --json=-
agentperf remote my-mac linux-box my-pc --mode doctor --json=doctor-fleet.json
```

`doctor` checks tools, disk space, load, power, and work-folder traps. It runs no benchmarks and creates nothing. Read its summary:

- `ok`: true only when `checks` is empty. The exit code stays 0 either way. Do not take exit 0 as "healthy".
- `checks`: one line per problem, in plain words. Report each one that matters.
- `missing_tools`: tools whose tests will skip. Missing `git` or `rg` cuts coverage hard.
- Work folder under `/mnt/` in WSL: stop and fix the folder first. The run would measure the wrong disk.
- Busy system (load above half the CPU count) or battery power: warn the user. Offer to wait, or benchmark now and label the numbers as taken under load.
- Under 1.5 GB free on the work disk: a run will stop. Free space first. Do not delete user files to make room.

`remote --mode doctor` runs the same check on each host in order and fetches nothing. Its summary records one `exit_code` per host. Doctor output streams live, so you can watch each host in turn.

## Choosing the run

| Goal | Command |
|---|---|
| First look, this machine | `agentperf run --quick --html report.html --json=summary.json` |
| A number to compare, this machine | `agentperf run --runs 3 --fetch-tools --html report.html --json=summary.json` |
| Several machines, first look | `agentperf remote my-mac linux-box --quick --fetch-tools --json=fleet.json` |
| Several machines, to compare | `agentperf remote my-mac linux-box my-pc --wsl my-pc:Ubuntu --runs 3 --fetch-tools --compare fleet.html --json=fleet.json` |
| Explain a saved result | `agentperf report result.json --json=summary.json` |
| Compare saved results | `agentperf compare a.json b.json --html compare.html --json=compare.json` |

Notes:

- `--quick` halves the repetitions. Good for a first look and for smoke tests. For a close call between machines, rerun the finalists with `--runs 3`.
- `--fetch-tools` downloads a pinned ripgrep (and Node if missing) so every machine searches with the same binary. Always use it when machines will be compared. It downloads once per machine into `~/.agentperf/tools`.
- `--no-network` skips the Reach tests. Use it offline. Reach never changes AgentMark.
- `--only` and `--exclude` take test ids or categories (`agentperf list` shows them). A partial run has low coverage. Say so when you report it.
- `--clean` deletes the work folder contents agentperf owns after the run. Use it on shared machines and in smoke tests.
- For other machines, always use `agentperf remote`. Do not copy `agentperf.pyz` by hand with `scp` or drive runs with your own `ssh` calls. The command handles PowerShell and POSIX quoting, unique per-run result names, and label checks on fetch. Hand-rolled copies lose those guards.
- Outputs and the work folder are separate. `--out`, `--html`, `--json`, and `--out-dir` go where you say. The work folder stays at its default (`~/.agentperf/work` per machine) unless a specific disk must be measured. On remotes, `~/.agentperf/` on each host is expected, even when local outputs must stay in one folder.
- Give `--json` a file path for long runs (`--json=fleet.json`). Use `--json=-` only when you capture stdout yourself and need the JSON inline. Put `--json` after file arguments, or write `--json=PATH`, so a file name is not taken as the JSON path.

## Order of runs

One machine at a time. Benchmarks notice each other: two runs together read low for both.

`agentperf remote` enforces the order inside one command: positional hosts in the order given, then `--wsl HOST:DISTRO` runs in their order. Respect the same rule across commands:

- Never run native Windows and its WSL2 at the same time, even from two shells. They are one PC.
- Do not benchmark the machine you are running on while a remote run is loading it (the SSH and fetch work is light, but a local `--runs 3` is not).
- If the user works on a machine mid-run, expect noise. Note it next to that machine's score.

Labels: a run's label names it in every report and in the fetched file name. With `remote`, labels come from host names (`my-mac`, `my-pc-wsl-ubuntu`). `--label` sets one label and works for a single run only. If two runs would share a label, the command refuses before it starts (exit 2). Split the command and set distinct labels.

## Windows specifics

- agentperf detects a Windows host by probing `uname`. PowerShell over SSH is the supported shell. Git Bash as the SSH shell also works for the POSIX path, but PowerShell is the tested one.
- OpenSSH kills the session's processes when the session ends. Keep the `remote` command attached until it returns. Do not detach a benchmark and poll for its file; the process dies with the session.
- Scheduled tasks run below normal priority. Never benchmark through one. If a user suggests a scheduled task so they can close the laptop, explain the priority problem and offer a shorter `--quick` run instead.
- Antivirus and the search indexer stay as they are. agentperf measures with them on, because agents work with them on. Report their cost from the causes. Do not offer to disable them. You may explain folder exclusions and Dev Drive as options for the user to weigh.
- Python on Windows: `remote` uses `python` on a Windows host and `python3` elsewhere. Pass `--python` only when the host needs a specific interpreter (for example `py -3.11` will not work; give a single command name or path).
- The remote result file is removed from the host after a good fetch. `~/.agentperf/agentperf.pyz` and the work folder remain for the next run. `--clean` empties the work folder on each host.

## WSL2 specifics

- `--wsl Ubuntu` with one host runs only inside that distro. `--wsl my-pc:Ubuntu` adds a WSL run after the positional hosts. To get native Windows and WSL2 for one PC: `agentperf remote my-pc --wsl my-pc:Ubuntu`.
- The work folder lives inside the distro by default. If the user passes `--workdir`, it must be a Linux path for WSL runs, never under `/mnt/`. `doctor` and the report both flag `/mnt/` work folders. Repos under `/mnt/c` have the same problem for daily agent work. Say so when you see it.
- Do not run `wsl --shutdown`, unregister distros, or edit `.wslconfig`. None of that is needed to benchmark, and all of it disrupts the user.

## macOS specifics

- The git shim: without the Command Line Tools, `/usr/bin/git` can wait on a dialog. agentperf probes `git --version` with a timeout and skips git tests cleanly. If `doctor` reports this, tell the user the fix is `xcode-select --install`. Do not launch it yourself.
- Spotlight indexes the thousands of new corpus files. On a first run this can add background load. A second run is usually calmer. Mention the Spotlight finding when it appears; changing Spotlight privacy is the user's choice.
- Background daemons: one stuck process near 100% CPU drags every process and file test. The findings name the busiest processes as counts and names in the raw result. Suggest restarting the stuck process. Never kill processes you did not start.
- Battery and Low Power Mode both show in `doctor` checks and findings. For a comparable number, ask for wall power.

## Collecting and comparing

A local `run` writes one result JSON (`--out`, or `agentperf-<label>-<time>.json`). A `remote` run fetches one result per host into `--out-dir` (default `results/`), named by label. The remote summary's `results` list is the exact set to compare. Do not glob a folder and hope; an old file from last week will join the ranking uninvited.

Compare with one of:

```sh
agentperf remote my-mac linux-box --fetch-tools --compare fleet.html --json=fleet.json
agentperf compare results/my-mac.json results/linux-box.json --html fleet.html --json=compare.json
```

Read `comparison.machines` (remote) or `machines` (compare):

- Rank on `agentmark_common`. It scores every machine on the tests they all share.
- `agentmark` and `coverage` show the full-test picture per machine. A machine with low coverage skipped tests. Name the skips.
- `comparability_warnings` per machine: if present, the ranking is shaky. Say why.
- `categories` shows where a machine wins or loses (process, files, search, git, compute, transfer, ipc). Use it to explain, not just to rank.

Then explain each slow machine on its own with `agentperf report <result>.json --json=<label>-summary.json`. The comparison says *where*; the single-machine report says *why* (causes, evidence, fixes).

## Explaining results honestly

Say the score, then the conditions, then the fixes. Example shape:

> Your laptop scored AgentMark 410 (reference 1000), measured on battery with a backup running. Treat it as a low read. The top fixable cause is a slow login shell (observed): each shell start pays for your profile files. The report is in `report.html`.

Rules:

- Within-run intervals (`agentmark_ci`) understate real spread. Between runs, expect about 10 to 15%. Call differences under about 15% a tie.
- Gains ("about +90 at reference speed") are counterfactual estimates: they assume the affected tests reach reference speed. They are not ceilings and not promises, and they overlap. Never add them. When `gain_estimated` is false, say "gain not estimated".
- Confidence words matter: observed, likely, possible. Keep them.
- Coverage under about 90% means the score rests on fewer tests. Say which categories are thin.
- Comparability warnings mean what they say. Different corpus, definitions, methods, or fingerprint: do not compare as equals. Version labels alone never block a comparison; the fingerprint does.
- Never invent a percentile, a population, or a product name no probe observed.
- Reach is the network, not the machine. Do not let a fast network flatter a slow disk, or the reverse.

## Privacy

Raw results hold hostnames, home paths, process names, and security product names. Summaries (`--json`) are derived from raw results and carry labels and paths too. Neither is fit to post.

- For public sharing, run `agentperf share result.json`. Post only that output. It prints every change it made. Skim the file once anyway.
- In chat, quote scores, verdicts, causes, and fixes. Leave hostnames, usernames, full paths, IP addresses, and process lists out unless the fix needs one.
- `SECURITY.md` lists exactly what each command touches. Read it before your first `live` or `remote` job on a new user's machines.

## Safety

- **Live runs cost tokens.** `agentperf live` starts real agent sessions with open permissions in throwaway fixture copies. Show `agentperf live --dry-run` first. Get a clear yes for the session count. Never run it as a surprise extra.
- **Work folders.** agentperf deletes its own `corpus/`, `repo/`, and `scratch/` inside its work folder, and refuses a non-empty folder it did not create. Never point `--workdir` into a user repo to "measure the real disk". The default folder is on the home disk; if the user needs another disk measured, create a new empty folder there together.
- **No system changes.** Do not change antivirus, indexing, power, WSL, SSH, or shell startup settings. Diagnose and recommend. The user applies fixes, or asks you to make one specific change at a time.
- **Cleanup.** Remove scratch files you made (extra summaries, test HTML). Keep results and reports the user asked for, and name where they are. On remotes, `--clean` empties each work folder; the copied `agentperf.pyz` in `~/.agentperf/` is small and useful. Mention it remains.
- **One run per work folder.** The lock file error means a run is active or died hard. Check before you delete a lock.

## Exit codes and machine-readable output

| Code | Meaning | Your move |
|---|---|---|
| 0 | Success. | Read the summary and report. |
| 1 | Ran, but failed or refused: empty run, work folder refused, remote host failed, skill copy conflict, live task failed. | Read the error line and the summary's per-item status. Fix the cause, not the flags. |
| 2 | Bad input: unknown flag, missing or malformed file, incompatible results, bad label, ambiguous `--wsl`. | Fix the command or the file set. Do not retry unchanged. |
| 130 | Interrupted. | Rerun when ready. Partial work files are safe. |

Machine output, so you never scrape text:

- `run`, `report`: summary with `agentmark`, `agentmark_ci`, `coverage`, `reach`, `lw100_seconds`, `verdict`, `top_causes`, `findings`, `result_file`, `html_file`.
- `compare`: summary with `machines[]` (`agentmark`, `agentmark_common`, `coverage`, `categories`, warnings).
- `doctor`: summary with `ok`, `checks`, `tools`, `missing_tools`.
- `remote`: summary with `hosts[]` (`host`, `wsl`, `label`, `exit_code`, `result_file`, `error`), `results`, `compare_html`, and `comparison` when `--compare` ran.
- `skill install` / `uninstall` / `path`: summary with per-target `path` and `status` (or `state` for `path`).

Every summary carries `command` and `summary_version`. Full field lists live in `docs/result-schema.md`. With `--json=-`, stdout holds only the JSON; progress moves to stderr.

## Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| Exit 2, "no such file" | Wrong result path. | Use `result_file` from the run summary, or `results` from the remote summary. |
| Exit 2, "not an agentperf result file" | You passed an HTML report or a summary JSON. | Pass the result JSON from `run` or `remote`. |
| Exit 2, "no such reference" | `--ref` names nothing bundled and no file. | Run `agentperf references`, or pass a result file path. An unknown `--ref` is an error on purpose; it never falls back silently. |
| Exit 2, "different corpus fixture hash" (combine/merge) | Files from different suites. | Do not combine them. Rerun with one agentperf version. |
| Exit 2, "--wsl DISTRO needs exactly one host" | Bare distro with several hosts. | Use `--wsl HOST:DISTRO` per WSL run. |
| Exit 2, "both write result label" | Two runs, one label. | Split the command; set distinct `--label` values. |
| Exit 2, "matches no benchmark" | Typo in `--only` / `--exclude`. | Copy an id from `agentperf list`. |
| Exit 2, "--runs" or other count "must be at least 1" | A zero or negative count. | Use 1 or more. |
| Exit 1, workdir refused as non-empty | The folder holds other files. | Use the default, or a new empty folder. Never a repo. |
| Exit 1, lock file exists | A run holds the folder, or one died. | Wait, or confirm no run is active and delete `.agentperf-run.lock`. |
| Exit 1, "Only X GB free" | Work disk nearly full. | Free space with the user. Do not delete their files. |
| Exit 1, "No benchmark produced a result" | No tools, or everything skipped. | `agentperf doctor --json=-`; fix `checks`; try `--fetch-tools`. |
| Exit 1, "cannot reach host over SSH" | Host off, bad alias, or no batch-mode keys. | `ssh -o BatchMode=yes host "echo ok"`. Ask the user to fix access. Do not edit keys. |
| Exit 1, "--wsl needs a Windows host" | Host is POSIX, not Windows. | Drop `--wsl`, or check the alias. |
| Exit 1, "could not copy agentperf.pyz" | No write access to the remote home, or `scp` blocked. | Check `ssh host "mkdir -p .agentperf"` by hand with the user. |
| Remote host has `result_file: null` | Run failed, or fetch or label check failed. | Read `exit_code` and `error`. Rerun that host alone and watch the stream. A failed run never fetches an older result; the remote file name is unique per run. |
| Fetched result "label does not match; refusing it" | The file on the host was not this run's output. | Rerun. Do not rename files to force a match. |
| `skill install` refused | Edited copy, or no manifest. | Show the path and changed files. `--force` only with the user's agreement. |
| Score dropped since last time | Load, battery, thermal, new scanner, disk filling. | Read `findings`. Rerun idle, plugged in, `--runs 3`. |
| Git tests skipped (macOS) | Git shim, no Command Line Tools. | Report it. Fix is `xcode-select --install`, by the user. |
| Search tests skipped | No ripgrep. | Rerun with `--fetch-tools`, or install ripgrep. |
| Reach tests fail | Offline, proxy, blocked host. | Use `--no-network` for the local score. Say Reach is missing. |
| WSL run is 10x too slow | Work under `/mnt/`. | Move to the Linux file system. Keep the default work folder. |
| Windows run looks frozen | Session output stalled, or a dialog waits on the desktop. | Check the host console. Keep the SSH session alive; closing it kills the run. |

## A complete example

The user says: "Benchmark my Mac, my Linux box, and my Windows PC (native and WSL2) and tell me where to run my agents."

1. Confirm aliases. For example: "I see `my-mac`, `linux-box`, and `my-pc` in your SSH config. Shall I benchmark those three, with `my-pc` twice (native Windows, then WSL2 Ubuntu)?" Adjust to what they confirm.
2. Preflight: `agentperf remote my-mac linux-box my-pc --mode doctor --json=doctor.json`. Read each host's output. Fix or report blockers.
3. Measure: `agentperf remote my-mac linux-box my-pc --wsl my-pc:Ubuntu --runs 3 --fetch-tools --compare fleet.html --json=fleet.json`. This runs four benchmarks in a row. It takes a while. Tell the user it is running, then wait.
4. Read `fleet.json`: check every `hosts[].exit_code` is 0 and every `result_file` is set. Check `comparison.machines` for warnings.
5. Rank on `agentmark_common`. Call machines within about 15% a tie.
6. For each slow machine, run `agentperf report <result>.json --json=<label>.json` and pull the top causes.
7. Answer: where agents run fastest, what holds each slower machine back (with confidence words), the one fix worth trying first per machine, and the paths to `fleet.html` and the result files.
8. Offer the next step once: rerun one machine after a fix, or a `live --dry-run` plan. Do not start either without a yes.
