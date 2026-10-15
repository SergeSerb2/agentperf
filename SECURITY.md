# Security and privacy

## What agentperf touches

- **Local files only (system suite).** It creates a work directory
  (`~/.agentperf/work`), generates a fixed 6,000-file corpus there, runs timed
  file, git, search, and process operations inside it, and writes one result
  JSON. A run deletes its own `corpus/`, `repo/`, and `scratch/` folders
  inside the work directory, so the directory must be agentperf's own: it is
  marked with a `.agentperf-owned` file, and a non-empty directory without
  that marker is refused before anything is touched. agentperf never
  adopts an unmarked non-empty directory, even one that looks like an old
  work folder. `--clean` removes only the entries agentperf owns,
  including corpus archives only when its generation ledger recorded
  making them, never anything else you put there. It never modifies your
  repos.
- **System probes are read-only.** CPU, OS, filesystem type, power state,
  running security agents (process names), the busiest processes, load
  average, and similar facts. Nothing needs administrator rights.
- **Network tests (Reach)** contact public endpoints only: the model API
  hosts (`api.anthropic.com`, `api.openai.com`) for DNS/TLS timing,
  `speed.cloudflare.com` for a 25 MB download and an 8 MB upload of zero
  bytes, and the npm registry for one metadata request. They send **no
  credentials and no API keys**. Skip them with `--no-network`.
- **`--fetch-tools`** downloads a pinned ripgrep (SHA-256 checked), and Node
  only if Node is missing. Both are stored in `~/.agentperf/tools`; PATH is
  never modified. Without the flag, nothing is downloaded.
- **`remote`** copies agentperf itself as one `.pyz` to `~/.agentperf` on a
  host you name over SSH and runs it with that host's Python. Several hosts
  run one after another. Result files come back over SSH; the host copy is
  deleted after a good fetch. The `.pyz` stays in `~/.agentperf` for later
  runs.
- **`skill install`** copies the bundled Agent Skill text files into agent
  skill folders (`~/.claude/skills`, `~/.codex/skills`, `~/.agents/skills`,
  or the project forms with `--project`). It writes a small manifest with
  file hashes and refuses to replace a copy you edited unless you pass
  `--force`. Install and uninstall touch only the known skill files:
  manifest entries outside that list are ignored, nothing is written or
  deleted through a symlink or junction, `--force` never widens that
  confinement, and a forced uninstall of a copy agentperf did not install
  removes only the recognized skill files and keeps your other files. One
  limit, stated plainly: the link checks are check-then-write, so a
  same-user process that swaps a checked file or folder for a link in
  the moment between the check and the write can win that race. Closing
  it would need no-follow file APIs Python does not portably expose. The
  target folders are your own, so treat this confinement as defense
  against accidents and crafted manifests, not against a live local
  adversary. It installs no code that runs on its own; the files are
  instructions your agent reads.
- **`--json` summaries** are plain JSON views of a result. They are **not**
  anonymized: they carry labels, file paths, and findings from the raw
  result. Keep them as private as the result itself.
- **`live` spends tokens.** It starts real Claude Code / Codex sessions with
  your existing login on a throwaway generated repo. It is never run by
  default, and it prints the session count and sandbox modes before it
  starts: Claude Code runs with `--permission-mode bypassPermissions`, Codex
  with the `--codex-sandbox` mode (default `workspace-write`). Both are
  confined by the prompt to the throwaway fixture copy, but treat
  `bypassPermissions` with the care the name suggests. Use `live --dry-run`
  first. Run copies stay under `~/.agentperf/live` unless you pass `--clean`.
  When a session ends - normally, on error, on timeout, or on Ctrl-C -
  agentperf kills the harness's process group before it verifies or
  deletes anything, so tool subprocesses do not outlive the session. One
  limit, stated plainly: on Windows there is no process group to signal
  once the harness itself has exited, so a descendant that outlives an
  exited Windows harness can survive. Verification runs the agent's
  code in a separate process and decides pass/fail in the parent; it is
  built to catch wrong code, not to resist a deliberately adversarial
  agent.

## What a result file contains, and sharing

A raw result contains your hostname, your workdir path (which includes your
username), busiest process names, and security product names. **Do not post a
raw result in public.** Use:

```sh
agentperf share my-result.json
```

`share` keeps only the fields agentperf writes and drops everything else,
so a note typed into the file by hand never travels. In what it keeps, it
removes or generalizes: hostname, label, run ids (at any depth; timestamps
are cut to the day), workdir and other home paths with everything under
them (macOS, Linux, and Windows forms, UNC paths, `~/` and `~user`, and
home folder names that contain spaces), volume names, process names (kept
only as counts), security brand names (kept only as detected/active
flags), tool paths (file names only), email, IP, and MAC addresses,
Tailscale host names, and your user and host names in any letter case.
Schema text (benchmark ids, categories, units, method strings, scoring
config) holds none of these, so it comes through byte-identical and a
shared file scores exactly like the original, even when a host or user
name is a word agentperf itself uses, such as "work" or "git": such a
name is left alone inside schema text and scrubbed everywhere else. A
string that still looks identifying after scrubbing is withheld whole,
never passed through. It prints every change.

Limits: `share` is a careful filter for files agentperf writes, not a
guarantee for text typed into them. A one-letter user or host name, and
the generic folders Shared, Public, Default, Users, and Home, are not
treated as names. A hardware serial number typed into a field by hand
looks like any other hex string and cannot be detected; the fields where
the suite itself records serial-bearing data are dropped outright. Still
read the file once before you post it.

The bundled reference profiles in `agentperf/references/` were produced with
the same anonymizer and checked for leaks in the test suite.

## Reporting a vulnerability

Open a private report through GitHub Security Advisories on this repository.
Do not include raw result files with personal data in a public issue.
