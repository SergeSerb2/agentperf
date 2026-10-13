# Windows, WSL2, and macOS notes

## Windows over SSH

agentperf supports Windows hosts whose SSH shell is PowerShell. It detects the host type itself. You do not need to say which is which.

Keep these facts in mind:

- OpenSSH on Windows ends the session's processes when the session ends. Keep the SSH command running until the benchmark finishes. Do not start a benchmark, disconnect, and collect it later.
- Scheduled tasks run below normal priority by default. Never benchmark through a scheduled task. The numbers will read low for a reason that has nothing to do with agent work.
- Leave antivirus and search indexing as they are. agentperf reports their effect. Changing them is the user's call, not yours.
- The scored shell is Git Bash on Windows. If git is missing, git and shell tests skip and coverage drops. `doctor` says so before you start.
- PowerShell starts more slowly than Git Bash on most PCs. That shows up as a diagnostic, not as part of the score. It still matters for agents that run commands through PowerShell.

## WSL2

Run agentperf inside the distro, on the Linux file system:

```sh
agentperf remote my-pc --wsl Ubuntu --fetch-tools
```

Rules:

- The work folder must not be under `/mnt/`. A folder under `/mnt/c` measures NTFS through a slow bridge. `doctor` warns, and the report puts this finding first. The default work folder (`~/.agentperf/work` inside the distro) is correct.
- List distros with `wsl -l -v` on the Windows host when you are unsure of the name. Names are case-sensitive in `--wsl`.
- Native Windows and WSL2 on one PC share the hardware. Run them one after the other. `agentperf remote my-pc --wsl my-pc:Ubuntu` does that for you.
- Do not shut down WSL (`wsl --shutdown`) to "clean up". It stops the user's other Linux work. agentperf cleans its own files.

## macOS

- Without the Xcode Command Line Tools, `/usr/bin/git` is a shim that can pause for a dialog. agentperf probes git before it times it, so a missing install skips the git tests instead of hanging. The fix is `xcode-select --install`. Do not run that install without asking; it opens dialogs and downloads software.
- Spotlight indexes new files while the benchmark writes thousands of them. If the report blames background load, note it. Adding code folders to Spotlight privacy is the user's choice.
- A background process stuck near 100% CPU will hold the whole score down. Name it from the findings. Suggest a restart of that process. Do not kill processes you did not start.
- On battery, numbers move. `doctor` warns. Ask the user to plug in for a number they will compare.
