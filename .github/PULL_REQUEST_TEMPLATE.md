## What changed

<!-- One short paragraph. If this changes a scored test, a weight, the corpus,
or the scoring math, say so first: that moves every score and needs a suite
minor-version bump plus a CHANGELOG entry (see CONTRIBUTING.md). -->

## Checklist

- [ ] `python3 -m unittest discover -s tests -t .` passes
- [ ] `python3 -m agentperf run --quick --no-network --clean` smoke run passes
- [ ] New or changed behavior has a test
- [ ] No new dependencies (standard library only), Python 3.8 compatible
- [ ] No hostnames, usernames, home paths, IPs, or raw results in the diff
- [ ] Docs updated if behavior or claims changed (README / METHODOLOGY / FAQ / CHANGELOG)
