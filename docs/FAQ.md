# Frequently asked questions

Short answers to the objections we expect. [METHODOLOGY.md](METHODOLOGY.md)
has the long version, with sources for every number below.

## Why is the reference a WSL2 VM and not a real Linux machine?

1000 is one measured PC: an i7-12700K desktop running Ubuntu in WSL2 (ext4 on
NVMe), measured in October 2026. It is not bare-metal Linux and it is not an
industry standard. It was the fastest complete profile measured when the scale
was set, and WSL2 is where a lot of Windows developers actually run agents.
Read a score as "speed relative to that one PC in WSL2, on these fixed tests".
A bare-metal Linux profile is on the roadmap; adding profiles never moves the
anchor.

## Your weights come from one person's tool calls. Why should I trust them?

You should treat them as a stated assumption, not a fact. The category weights
and the LW100 mix come from one developer's 6,416 Claude Code tool calls (79%
Bash). Re-scoring every bundled profile under equal category weights, a
compute/git-heavy mix, and flat per-test weights does not change the rank
order, and the native-Windows-vs-WSL2 gap stays between 11x and 15x.
Absolute points move up to about 30%. The table is in
[METHODOLOGY.md](METHODOLOGY.md#sensitivity), and
`agentperf compare --weights equal` lets you check on your own results.

## How repeatable is a score?

Single runs vary about 10-15%. Three back-to-back runs on the reference
machine itself scored 1000, 1154, and 1006 against the first. The printed
"90% within-run interval" only covers spread inside one run, so it is much
narrower than run-to-run spread. For a number you plan to compare, use
`--runs 3` on an idle, plugged-in machine, and do not argue over differences
under about 15%.

## You spawn processes from Python. Real harnesses don't.

Correct, and measured: on a busy M5 Pro a shell start cost 3.67 ms spawned
from Python vs 2.53 ms spawned from Node (`diag.node_spawn`, timed inside
Node). The Python harness adds a roughly constant tax to every machine, which
compresses process-spawn ratios toward 1. Cross-machine process gaps are
therefore understated, not invented. Codex spawns from Rust; we do not ship a
Rust helper, so the Node diagnostic is the reference point we can offer.

## Is F_FULLFSYNC really equal to Linux fsync or FlushFileBuffers?

They make the same request: get data past the drive's volatile write cache.
Plain macOS `fsync()` does not make that request, which is why scored writes
use `F_FULLFSYNC` there. Whether any given drive honours the request is up to
its firmware, on every OS. The plain-`fsync` speed is reported as a diagnostic
on macOS so you can see the difference on your hardware.

## Can you really bypass the page cache without root?

Best effort, per OS: `F_NOCACHE` (macOS), `posix_fadvise(DONTNEED)` before
each read (Linux; the fixture is flushed at creation so its pages are clean
and droppable), `FILE_FLAG_NO_BUFFERING` with aligned buffers (Windows). The
method used is recorded in the result. Only compare cold-read numbers between
machines that used the same method.

## "67 on Windows vs 1000 in WSL2" - is Windows really 15x slower?

That figure is one PC, in October 2026, with third-party real-time antivirus
scanning and the Windows Search indexer active on the native side. It shows
what per-process and per-file security filtering costs an agent workload on
that setup; it is not a claim about every Windows install. A tuned native
setup (Dev Drive, antivirus exclusions for code folders) can do much better,
and agentperf only recommends WSL2 when your own machine shows the slow
pattern. Run it natively and see which side of the gap you are on.

## What is LW100, and why do its parts sum to more than 100?

LW100 models the seconds of local wait behind 100 agent tool calls, using the
same tool-call history as the weights: 79 shell starts, 26 file reads, 9.5
Python starts, 7.5 folder-tree greps, 6.3 git commands, 3.4 edits, 1.6 Node
starts, 1 commit. One Bash call can contain several of these, so the parts
overlap and sum past 100 operations. It is a model, labelled as one; the
per-line breakdown is in every HTML report.

## Why is the network scored separately?

Reach measures your place and your ISP (DNS, TLS and first byte to the model
APIs, throughput to a CDN), not your computer. Folding it into AgentMark would
let a fast network hide a slow disk. Reach is also the least stable number
here: DNS largely hits the OS cache, and routes change by the hour.

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
through 9P, and that case gets the top warning.

## Can I compare results from different agentperf versions?

Reports check comparability on what was measured: they warn when two files
have different corpus hashes or different scored test definitions. Release
notes say when a version moves scores. If no warning prints, the comparison
is sound even across versions (the bundled 0.1.0 profiles compare cleanly
with 0.2.x results for exactly this reason).

## What does `share` remove, exactly?

Hostname, label, run id, workdir and other home paths, process names (kept as
counts), security product names (kept as detected/active flags), tool paths
(file names only), and IP/MAC addresses or user/host names left in free-text
fields. It prints every change it makes. Read the file once before posting:
[SECURITY.md](../SECURITY.md) lists the details and the limits.
