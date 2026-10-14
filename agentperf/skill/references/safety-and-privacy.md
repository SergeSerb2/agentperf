# Safety and privacy

## Work folders

A run deletes its own `corpus/`, `repo/`, and `scratch/` folders inside its work folder. For that reason:

- Let agentperf use its default work folder (`~/.agentperf/work`) unless the user needs a specific disk measured.
- Never set `--workdir` to a user repo, a home folder, or any folder with files in it. agentperf refuses a non-empty folder it did not create. That refusal protects the user. Do not work around it.
- A folder agentperf owns carries a `.agentperf-owned` marker. `--clean` removes only the entries agentperf owns.
- One run at a time per work folder. A lock file enforces this.

## Consent and side effects

- `agentperf live` starts real Claude Code and Codex sessions. It costs tokens and runs with open permissions inside throwaway fixture copies. Run `agentperf live --dry-run` first. Start a real live run only after the user says yes to the session count. Verification runs the agent's code in a separate process and decides pass/fail in the parent; it is built to catch wrong code, not to resist a deliberately adversarial agent.
- `--fetch-tools` downloads a pinned ripgrep build (and Node, if missing) into `~/.agentperf/tools`. It checks SHA-256 and never changes `PATH`. Say that a download will happen the first time you use it on a machine.
- Network (Reach) tests contact public hosts and move about 33 MB. They send no keys and no user data. Use `--no-network` when the user is offline or on a metered link.
- Never change antivirus, indexing, power plans, Defender settings, or WSL configuration. You may explain what a change would do. The user makes it.
- Do not kill processes you did not start. Name a busy process from the findings and suggest a restart.
- Clean up what you create: fetched results the user does not want, scratch HTML files, and temporary folders. Keep the result JSON and the report the user asked for.

## Privacy

A raw result file holds the hostname, home paths, process names, and security product names. Treat it as private.

- To share a result in public, run `agentperf share result.json` and post only its output. Read the printed change list. Skim the file once before you post it.
- `--json` summaries are not anonymized. They carry labels, paths, and findings from the raw result. Keep them on the user's machines.
- When you quote a result in chat, quote scores, causes, and fixes. Leave out hostnames, user names, raw paths, and process lists unless the user needs them for the fix.
- Remote runs copy `agentperf.pyz` to `~/.agentperf/` on each host and fetch the result back over SSH. The result file on the host is removed after a good fetch. The `.pyz` stays, so later runs and `remote --mode doctor` are fast. Tell the user it is there.
