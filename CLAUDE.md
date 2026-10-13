# Claude Code notes

Read [AGENTS.md](AGENTS.md). It holds the rules for working on this repository: standard library only, Python 3.8 floor, scores must not move by accident, privacy, tests, and the ship checklist.

Two jobs live here. Do not mix them up:

- Working **on** agentperf (code, tests, docs): follow `AGENTS.md`.
- Running agentperf **for** a user (benchmark machines, explain a score): follow [docs/AGENTS-GUIDE.md](docs/AGENTS-GUIDE.md), or use the bundled skill in `skills/agentperf/`.

This repo is also a Claude Code plugin marketplace. The manifests are in `.claude-plugin/`, and the plugin root is the repo root. After you change them, run `claude plugin validate .`.
