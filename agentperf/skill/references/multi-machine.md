# Several machines in one command

`agentperf remote` copies agentperf to each host, runs there, and brings the result back. Hosts run one after another, in the order you give. One failed host does not stop the next one.

Always use this command for other machines. Do not copy `agentperf.pyz` by hand with `scp`, and do not drive runs with your own `ssh` calls. The command already handles PowerShell and POSIX quoting, unique result names (a failed run never fetches an old file), and label checks. A hand-rolled copy loses all three.

On each host, files land in `~/.agentperf/` (the copied program and the work folder). That is by design, even when your local outputs must stay in one folder. Use `--out-dir` to control where fetched results land here.

## Forms

```sh
# Two POSIX hosts, quick look
agentperf remote my-mac linux-box --quick --fetch-tools --json=fleet.json

# One Windows PC: native first, then WSL2, with one comparison report
agentperf remote my-pc --wsl my-pc:Ubuntu --fetch-tools --compare fleet.html --json=fleet.json

# WSL2 only (single-host short form)
agentperf remote my-pc --wsl Ubuntu --fetch-tools

# Numbers you will compare: three runs per machine
agentperf remote my-mac linux-box my-pc --wsl my-pc:Ubuntu --runs 3 --fetch-tools --compare fleet.html --json=fleet.json

# Preflight every host without benchmarking
agentperf remote my-mac linux-box my-pc --mode doctor
```

Rules for the flags:

- Positional `HOST` values are SSH aliases. They run in order.
- `--wsl HOST:DISTRO` adds one WSL run on that host. You may repeat it. WSL runs come after the positional hosts, in the order given.
- `--wsl DISTRO` without a host works only with exactly one host. It means: run that host inside the distro, and do not run it natively.
- `--label` works for a single run only. With several runs, labels come from the host names. A WSL run is `host-wsl-distro`, with the distro in lower case.
- If two runs would get the same label - including labels that differ only in case, which name the same file on Windows - the command stops with exit code 2 before anything runs. Run the hosts in separate commands with distinct `--label` values. Labels must be portable file names: no Windows reserved names (CON, NUL, and friends), no trailing dot.
- `--compare FILE.html` writes one comparison after the runs. It needs at least one fetched result.
- `--out-dir` says where fetched results land. The default is `results/`.
- Arguments after `--` go to the remote command. Prefer the named flags. They are checked; raw extras are not.

## What to read afterwards

Read the `--json` summary. Its `hosts` list has one entry per run, in run order:

- `host`, `wsl`, `label`: what ran.
- `exit_code`: the remote command's exit code, or `null` if setup failed.
- `result_file`: the fetched result path, or `null`.
- `error`: the setup failure, or `null`.
- `doctor` (doctor mode only): that host's doctor summary. Read `doctor.ok`, `doctor.checks`, and `doctor.missing_tools` for machine health. `exit_code` 0 only means the check ran; it never means the machine is well.

`results` lists the fetched files. `compare_html` is the comparison path, or `null`. When you asked for `--compare`, `comparison.machines` holds the scored rows. Rank on `agentmark_common`. It uses only the tests every machine ran.

The command exits 0 only when every run succeeded and every result was fetched. If one host fails, the exit code is not 0, but the other results are still there. Report the failure plainly. Do not hide it behind the successes.

## Order and timing

Benchmarks measure the machine as it is right now. Two runs at once would measure each other. That is why the order is fixed and sequential:

1. Positional hosts, in the order given.
2. `--wsl HOST:DISTRO` runs, in the order given.

Never start a second `remote` command that touches the same PC while the first one runs. In particular, do not run native Windows and WSL2 in parallel from two shells.
