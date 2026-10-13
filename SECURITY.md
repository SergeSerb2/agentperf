# Security and privacy

## What agentperf touches

- **Local files only (system suite).** It creates a work directory
  (`~/.agentperf/work`), generates a fixed 6,000-file corpus there, runs timed
  file, git, search, and process operations inside it, and writes one result
  JSON. `--clean` deletes the work directory. It never modifies your repos.
- **System probes are read-only.** CPU, OS, filesystem type, power state,
  running security agents (process names), the busiest processes, load
  average, and similar facts. Nothing needs administrator rights.
- **Network tests (Reach)** contact public endpoints only (model API hosts
  for DNS/TLS timing, a public speed-test endpoint, the npm registry). They
  send **no credentials and no API keys**. Skip them with `--no-network`.
- **`--fetch-tools`** downloads a pinned ripgrep (SHA-256 checked), and Node
  only if Node is missing. Without the flag, nothing is downloaded.
- **`remote`** copies agentperf itself as one `.pyz` to `~/.agentperf` on a
  host you name over SSH and runs it with that host's Python.
- **`live` spends tokens.** It starts real Claude Code / Codex sessions with
  your existing login on a throwaway generated repo. It is never run by
  default. Use `live --dry-run` first.

## What a result file contains, and sharing

A raw result contains your hostname, your workdir path (which includes your
username), busiest process names, and security product names. **Do not post a
raw result in public.** Use:

```sh
agentperf share my-result.json
```

`share` removes or generalizes: hostname, label, run id, workdir and other
paths, process names (kept only as counts), security brand names (kept only
as detected/active flags), and tool paths (file names only). It prints every
change. Still read the file once before you post it.

The bundled reference profiles in `agentperf/references/` were produced with
the same anonymizer and checked for leaks in the test suite.

## Reporting a vulnerability

Open a private report through GitHub Security Advisories on this repository.
Do not include raw result files with personal data in a public issue.
