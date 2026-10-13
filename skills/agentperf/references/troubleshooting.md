# Troubleshooting

Work from the exit code and the `--json` summary first. They name the failure more plainly than the human text.

| Symptom | Likely cause | What to do |
|---|---|---|
| Exit 2, "no such file" | A result path is wrong, or a run did not write its file. | Check `result_file` in the run summary. Use the path it names. |
| Exit 2, "not an agentperf result file" | The file is not a result JSON (often an HTML report or a summary). | Pass the result JSON, not the `.html` file and not the `--json` summary. |
| Exit 2, "--wsl DISTRO needs exactly one host" | Bare `--wsl` form with several hosts. | Use `--wsl HOST:DISTRO` for each WSL run. |
| Exit 2, two runs "would both write result label" | Two hosts map to one label. | Run them in separate commands with distinct `--label` values. |
| Exit 2, selector "matches no benchmark" | A typo in `--only` or `--exclude`. | Run `agentperf list` and copy the id exactly. |
| Exit 1, workdir "is not empty and was not created by agentperf" | The work folder holds other files. agentperf refuses to delete inside it. | Use the default work folder, or a new empty folder. Never point `--workdir` at a repo. |
| Exit 1, "Another agentperf run is using" the work folder | A run holds the lock, or an old lock file remains. | Wait for the run. If no run is active, delete `.agentperf-run.lock` in the work folder. |
| Exit 1, "No benchmark produced a result" | Tools are missing, or every selected test skipped. | Run `agentperf doctor --json=-`. Fix the `checks`. Try `--fetch-tools`. |
| Exit 1, "cannot reach 'host' over SSH" | The host is off, the alias is wrong, or keys are not set up for batch mode. | Test `ssh -o BatchMode=yes host "echo ok"`. Ask the user to fix access. Do not edit their SSH keys. |
| Exit 1, "--wsl needs a Windows host" | The host answered as POSIX, so it is not the Windows box you expected. | Check the alias. For a Linux host, drop `--wsl`. |
| Remote run fetched nothing, `result_file` is `null` | The remote command failed before it wrote, or the fetch failed. | Read `exit_code` and `error` for that host. Rerun that host alone and read its output. |
| `skill install` says "refused" | The installed copy was edited, or it has no agentperf manifest. | Show the user the path and the changed files. Only use `--force` with their agreement. |
| Score is far below the same machine's last score | Background load, battery, or a new scanner. | Read `findings` and `top_causes`. Rerun when the machine is idle and plugged in. Use `--runs 3`. |
| Git tests skipped on macOS | The git shim without Command Line Tools. | Tell the user. The fix is `xcode-select --install`. Do not start it unasked. |
| Network (Reach) tests skipped or odd | No network, a proxy, or a blocked host. | Rerun with `--no-network` for the local score. Reach does not change AgentMark. |
| WSL score is extremely low | The work folder or the repos sit under `/mnt/`. | Move work to the Linux file system (for example `~/code`). Keep the default work folder. |

If a run is interrupted (exit 130), partial files stay in the work folder by design. Rerun the same command. agentperf reuses or rebuilds its own corpus safely.
