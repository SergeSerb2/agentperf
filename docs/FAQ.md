# Frequently asked questions

Short answers to the objections we expect. [METHODOLOGY.md](METHODOLOGY.md)
has the long version. Numbers below come from the bundled reference profiles
unless they say otherwise.

## Why is the reference a WSL2 VM and not a real Linux machine?

1000 is one measured PC: an i7-12700K desktop running Ubuntu in WSL2 (ext4 on
NVMe), measured in October 2026. It is not bare-metal Linux and it is not an
industry standard. It was the fastest complete profile measured when the scale
was set, and WSL2 is where a lot of Windows developers actually run agents.
Read a score as "speed relative to that one PC in WSL2, on these fixed tests".
That PC is the anchor: the profile every score is measured against. A
bare-metal Linux profile is on the roadmap; adding profiles never moves the
anchor.

## Your weights come from one person's tool calls. Why should I trust them?

You should treat them as a stated assumption, not a fact. The category weights
are a judgment call, guided by one developer's 6,416 Claude Code tool calls
(79% Bash); nobody derived 25/15/15/15/15/10/5 from that data with a formula.
Re-scoring every bundled profile under equal category weights, a
compute/git-heavy mix, and flat per-test weights does not change the rank
order; the native-Windows-vs-WSL2 gap stays between about 8x and 10x.
Absolute points move up to about 40%. The table is in
[METHODOLOGY.md](METHODOLOGY.md#sensitivity), and
`agentperf compare --weights equal` lets you check on your own results.

## How repeatable is a score?

Single runs vary about 10-15%. In the October 2026 re-measurement, the three
runs behind the reference profile scored 1006, 982, and 984 against their own
median, and the three behind the Apple M1 Pro profile scored 436, 387, and
407. For one run, the printed interval only covers spread inside that run.
For `--runs 3`, the interval bootstraps runs and samples together, but it
still holds the reference fixed. For a number you plan to compare, use
`--runs 3` on an idle, plugged-in machine, and do not argue over differences
under about 15%.

## You spawn processes from Python. Real harnesses don't.

Correct, and measured. In the bundled profiles, a bash start spawned from
Python costs 3.7 ms on the WSL2 reference and 5.9 ms on the Apple M1 Pro.
The same start spawned and timed inside Node (`diag.node_spawn`) costs
2.8 ms and 1.8 ms. So the Python harness tax is not the same on every
machine (about 0.9 ms on one, about 4.1 ms on the other), and process ratios
can be off in either direction. The Node-side number is probably closer to
what Claude Code pays, since it also spawns from Node. Codex spawns from
Rust; we do not ship a Rust helper, so the Node diagnostic is the reference
point we can offer. Every run records both numbers.

## Is F_FULLFSYNC really equal to Linux fsync or FlushFileBuffers?

They make the same request: get data past the drive's volatile write cache.
Plain macOS `fsync()` does not make that request, which is why the flush
uses `F_FULLFSYNC` there. Whether any given drive honours the request is up
to its firmware, on every OS. Only two scored tests ask for durability at
all: the 256 MB write and the 256 MB copy (`transfer.write`, `transfer.copy`).
The small-file create and edit tests do not flush, like editor writes. The
plain-`fsync` speed is reported as a diagnostic on macOS so you can see the
difference on your hardware.

## Can you really bypass the page cache without root?

Best effort, per OS: `F_NOCACHE` (macOS), `posix_fadvise(DONTNEED)` before
each read (Linux; the fixture is flushed at creation so its pages are clean
and droppable), `FILE_FLAG_NO_BUFFERING` with aligned buffers (Windows). The
method used is recorded in the result, and reports warn when two machines
used different ones. Only compare cold-read numbers between machines that
used the same method. AgentMark still includes `transfer.read` across
methods; it is about 2% of the score.

## Why does the native Windows reference score so low?

The bundled Windows profile (103) is one PC, in October 2026. Each side combines three runs
(per test, the median of the run medians, then scored). The native side ran as-is: third-party real-time antivirus and the
Windows Search indexer active, shell commands through Git Bash, and an older
Python (3.11 against 3.14 in WSL2). The gap is everything that native setup
added compared with WSL2 on the same hardware. It is not a claim about every
Windows install, and it does not say how much is the antivirus alone: that PC
was never measured with scanning off. A tuned setup (Dev Drive, exclusions
for code folders) should do better; we have not measured one yet. agentperf
only recommends WSL2 when your own machine shows the slow pattern. Run it
natively and see which side of the gap you are on.

## What is LW100, and why do its parts sum to more than 100?

LW100 models the seconds of local wait behind 100 agent tool calls, using the
same tool-call history as the weights: 79 shell starts, 26 file reads, 9.5
Python starts, 7.5 folder-tree greps, 6.3 git commands, 3.4 edits, 1.6 Node
starts, 1 commit. One Bash call can contain several of these, so the parts
overlap and sum past 100 operations. It is a model, labelled as one; the
per-line breakdown is in every HTML report.

## Does local time even matter next to model latency?

On a healthy machine, mostly no. On a slow one, yes. In October 2026 we ran
real Claude Code and Codex sessions on three fixed tasks (fix a bug, rename a
function, add a small feature) on two machines: a Windows desktop with
real-time antivirus and an Apple-silicon Mac. Twelve runs in total, three
tasks per harness per machine. For Claude Code, local tools took 20-26% of
each task's wall time on the Windows PC (1.7-2.4 s of tool time per task) and
2-3% on the Mac (about 0.2 s). Codex showed the same pattern with wider
spread. So local speed is a rounding error until it is not: the gap between a
good setup and a bad one is the difference between 2% and a quarter of every
task. `agentperf live` runs the same three tasks on your machine.

## Why is the network scored separately?

Reach measures DNS, TLS, and first byte to the model APIs, plus throughput to
a CDN. That is mostly your place and your ISP, but not only that: the OS
network stack is in the path too. On the same PC and the same network, the
bundled native-Windows profile reaches the APIs faster than the WSL2
reference, because WSL2 traffic crosses a virtual network. That is why most
machines score well above 1000 on Reach: the anchor pays for its VM network.
Folding Reach into AgentMark would let a fast network hide a slow disk.
Reach is also the least stable number here: DNS largely hits the OS cache,
and routes change by the hour.

## My score is low but my machine is new. Is the benchmark broken?

Probably not: the usual causes are software, and the report names them with
evidence. The most common are real-time antivirus or indexing on Windows,
repos under `/mnt/c` in WSL, a slow login-shell profile (Codex pays it on
every command), background load during the run, and synced folders. Check
"Biggest wins" in the HTML report; each item cites the probe behind it and
labels its confidence.

## I launched from /mnt/c but my repos are in ~. Is my score wrong?

No. The score measures the benchmark work directory (`~/.agentperf/work` by
default). A launch directory under `/mnt/` is noted in the report but does not
affect the score. Only a work directory (or repo) under `/mnt/` measures NTFS
through 9P (the protocol WSL uses to serve Windows drives), and that case
gets the top warning.

## Can I compare results from different agentperf versions?

Reports check comparability on what was measured, not on version strings:
scores compare only when the corpus fixture hash, the scored benchmark
definitions, the measurement methods, and the measurement/scoring
fingerprint all match, against the same reference file. Reports warn when
any of those differ. If no warning prints, the two files should compare,
even across versions; release notes flag the changes that move scores. Suite
0.3.0 changed what some tests measure, so 0.2.x results warn against 0.3.0
profiles. That warning is the system working.

## What does `share` remove, exactly?

Hostname, label, run id (including the run ids nested inside combined
results, whose timestamps are cut to the day), workdir and other home paths
with everything under them, volume names, process names (kept as counts),
security product names (kept as detected/active flags), tool paths (file
names only), and IP/MAC addresses or user/host names wherever they appear.
Fields agentperf does not write are dropped. It prints a summary of what it changed. Read the file once before posting:
[SECURITY.md](../SECURITY.md) lists the details and the limits.
