# Reading results honestly

## The three numbers

- **AgentMark** is the local-work score. The reference machine scores 1000. A score of 2000 is twice as fast. A score of 500 is about half as fast.
- **LW100** estimates the seconds an agent waits on the machine per 100 tool calls. Lower is better. It is a model built from one developer's tool-call mix. Say "estimated" when you quote it.
- **Reach** measures the network path to model APIs and registries. It is separate on purpose. A fast machine on a slow network can have a high AgentMark and a low Reach.

Rough reading of AgentMark:

| AgentMark | Reading |
|---|---|
| 850 or more | Strong host. Tools come back almost at once. |
| 550 to 850 | Good. A few fixes can still help. |
| 300 to 550 | Noticeably slower than the reference. Agents often wait. |
| Under 300 | Fix the top causes, or run agents elsewhere. |

## Variance and comparison

- A single run varies by about 10 to 15%. Treat differences under about 15% as a tie.
- `agentmark_ci` is a 90% interval. For one run it covers within-run spread only. Between runs, the spread is larger. `ci_kind` says which interval you have.
- For a number the user will act on, use `--runs 3`. The combined result keeps, per test, the median of that test's run medians, and scores that. It is not the median of the whole-run scores.
- Rank several machines on `agentmark_common` from a compare summary. It uses only the tests every machine ran. The plain `agentmark` can rest on different test sets.
- `coverage` says how much of the reference's test weight the machine scored. Low coverage means tests skipped. A score with low coverage is a partial answer. Say which tests skipped and why (`skipped` in the summary).

## Comparability

Scores compare only when all of these match, against the same reference file: the corpus fixture hash, the scored benchmark definitions, the measurement methods, and the measurement and scoring fingerprint. Version strings do not decide.

If `comparability_warnings` in the summary is not empty, tell the user. Explain what differs in plain words. Do not present a clean ranking from files that warn.

## Causes and gains

Each cause carries a confidence:

- **observed**: a probe saw it directly.
- **likely**: a probe plus a matching slow pattern.
- **possible**: a pattern only.

Keep that word when you report the cause. Do not upgrade "possible" to fact.

A gain such as "about +90 at reference speed" means: if the affected tests reached reference speed, the score would rise by about that much. It is a counterfactual estimate, not a ceiling and not a promise; a real fix can land below it or past it. Gains overlap, so they do not add up. When `gain_estimated` is false, say the gain is not estimated. Do not invent one.

## What never to claim

- No percentiles ("faster than 90% of machines"). The reference set holds a handful of machines.
- No brand blame without a probe. If no probe saw a product, describe the pattern, not a product.
- No exact ranking inside the noise. Two machines within about 15% are tied for agent work.
- No silent upgrades. If a machine was busy, on battery, or short on disk during the run, say so next to its score.
