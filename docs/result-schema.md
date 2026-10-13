# Result and summary fields

This page lists the fields an agent or script needs. It covers three things: the result file that `run` writes, the `--json` summaries that commands write for machines, and the exit codes.

Only `agentperf share` output is anonymized. Result files and `--json` summaries are not safe to post in public. See `SECURITY.md`.

## Exit codes

Exit codes are stable. Scripts may rely on them.

| Code | Meaning |
|---|---|
| 0 | Success. |
| 1 | The command ran, but the work failed or was refused: an empty or all-failed run, a refused work folder, a failed remote host, a skill copy conflict (`skill install` / `uninstall` with status `refused`), or a failed live task. A remote command also passes through its own non-zero code when that code is not 1 or 2. |
| 2 | Usage or bad input: unknown flags, a missing file, a malformed result file, incompatible inputs to `combine` or `merge`, an unknown `--ref`, an invalid label, or an ambiguous `--wsl` form. |
| 130 | Interrupted with Ctrl-C. Partial work files stay in the work folder. |

Two special cases:

- `doctor` exits 0 when the check itself ran, even when it finds problems. Read `ok` and `checks` in its summary.
- `agentperf skill` with no subcommand exits 2. `agentperf` with no command prints help and exits 2. `--help` and `--version` exit 0.

## The `--json` flag

`run`, `report`, `compare`, `doctor`, `remote`, and the three `skill` commands accept:

```sh
--json=PATH   # write the summary to PATH; human output stays on stdout
--json=-      # write only the JSON to stdout; progress goes to stderr
--json        # same as --json=-
```

Put `--json` after file arguments, or use the `--json=PATH` form. A bare `--json result.json` would take `result.json` as the JSON path.

Every summary is one JSON object with:

- `command`: what produced it (`run`, `report`, `compare`, `doctor`, `remote`, `skill install`, `skill uninstall`, or `skill path`).
- `summary_version`: the summary format version. This release writes `1`.
- `package_version`: the installed program version.

Summaries are views. They never change scoring. Numbers use `null` when a value does not exist (for example Reach with `--no-network`). Never read `null` as zero.

## Result summary (`run` and `report`)

`run` writes this after it saves the result file. `report` writes the same shape for a saved result (`command` differs).

| Field | Meaning |
|---|---|
| `label`, `profile` | Machine label and profile (`quick` or `standard`). |
| `suite_version` | Suite that measured the result. The running program is `package_version`. |
| `result_file`, `html_file` | Absolute paths, or `null` when no HTML was written. |
| `started_at`, `duration_s` | When the run started (UTC) and how long it took. |
| `fingerprint` | Measurement and scoring fingerprint of the result. |
| `corpus_fixture_hash` | Hash of the test inputs. Must match for a strict comparison. |
| `system` | Small system subset: OS family and version, CPU model, CPU count, RAM, file system, Python, WSL flag. Keys with no value are omitted. |
| `benchmarks_run` | How many benchmarks produced values. |
| `skipped` | Test id to skip reason. Empty object when nothing skipped. |
| `findings` | List of `{level, topic, text}`. Levels: `warn`, `tip`, `info`. |
| `reference.label`, `reference.fingerprint` | What the scores compare against. `reference` is `null` only when no reference could be loaded. |
| `agentmark` | Headline score. Reference speed is 1000. |
| `agentmark_ci` | 90% interval `[low, high]`, or `null`. |
| `ci_kind` | `within-run interval`, or `bootstrap over runs and samples` for combined runs. |
| `coverage` | Share (0 to 1) of the reference's test weight that this result scored. |
| `reach`, `reach_ci` | Network score and interval, or `null`. |
| `lw100_seconds`, `lw100_reference_seconds` | Estimated local wait per 100 tool calls, on the parts both sides measured. `null` when no parts measured. |
| `categories` | Category name to score (reference speed is 1000 in each). |
| `comparability_warnings` | Plain-word reasons the two files may not compare strictly. Empty list means comparable. |
| `verdict` | One-sentence reading of the score. |
| `biggest_drag` | `{id, ratio, gain_points}` for the costliest test, or `null`. Ratio under 1 means slower than the reference. |
| `top_causes` | Up to three ranked causes: `{topic, confidence, gain_estimated, gain_points, fix}`. Gains are upper bounds. `gain_points` is `null` when not estimated. |
| `minor_causes` | Topic names of real but small causes (each under +5 AgentMark). |

With no reference, `agentmark`, `reach`, and category scores are absent or `null`, `top_causes` is empty, and `lw100_seconds` still reports when its parts measured. `lw100_reference_seconds` appears only with a reference.

## Compare summary (`compare`, and `remote --compare`)

| Field | Meaning |
|---|---|
| `reference.label`, `reference.fingerprint` | The scoring baseline. |
| `weights` | `default`, or `equal` for the sensitivity check. |
| `common_tests` | Test ids every compared file (reference included) shares. |
| `html_file` | Comparison report path, or `null`. |
| `machines[]` | One entry per result, in input order. See below. |

Each `machines[]` entry:

| Field | Meaning |
|---|---|
| `label` | Machine label from its result file. |
| `agentmark` | Score on all of that machine's tests. |
| `coverage` | Share of reference weight scored (0 to 1). |
| `agentmark_common` | Score on `common_tests` only. Rank machines on this. |
| `reach` | Network score, or `null`. |
| `lw100_common_seconds` | LW100 on the common tests, or `null`. |
| `categories` | Category name to full-test score. |
| `comparability_warnings` | Warnings for this machine against the reference. |

Under `remote`, this same object sits in the remote summary as `comparison`.

## Doctor summary

| Field | Meaning |
|---|---|
| `ok` | True only when `checks` is empty. This is the health bit. The exit code is 0 either way. |
| `checks` | One plain-word line per problem. |
| `tools` | Tool name to path (home shown as `~`), or `null` when missing. |
| `missing_tools` | Tool names not found. Their tests will skip. |
| `system` | Probe subset (CPU, OS, disk free, power, file system, WSL flags). Empty values are omitted. |
| `workdir` | The folder a run would use (home shown as `~`). |
| `os_family`, `python`, `suite_version` | Local facts. |
| `next` | Suggested smoke command. |

## Remote summary

| Field | Meaning |
|---|---|
| `mode` | `run`, `live`, or `doctor`. |
| `sequential` | Always `true`. Hosts ran one at a time, in `hosts` order. |
| `hosts[]` | One entry per run, in run order. See below. |
| `results` | Fetched result files (absolute paths), in run order. |
| `compare_html` | Comparison path when `--compare` wrote one, else `null`. |
| `comparison` | The compare summary object. Present only with `--compare` and at least one fetched result. |

Each `hosts[]` entry:

| Field | Meaning |
|---|---|
| `host` | SSH alias as given. |
| `wsl` | Distro name for a WSL run, else `null`. |
| `label` | Result label used on the host and in the fetched file name. |
| `mode` | The remote command that ran. |
| `exit_code` | Exit code of the remote command, or `null` when setup failed before it ran. |
| `result_file` | Fetched file path, or `null`. Always `null` for `doctor`. |
| `error` | Setup failure text, or `null`. |

A run counts as good when `error` is null, `exit_code` is 0, and (for `run` and `live`) `result_file` is set. The command exits 0 only when every run is good.

## Skill summaries

`skill install` and `skill uninstall` summaries hold `project` (absolute project root, or `null` for user folders) and `targets[]`. Each target has `target` (`claude`, `codex`, or `agents`), `path`, `status`, and `message`.

Install statuses: `installed`, `updated`, `current`, `refused`. Uninstall statuses: `removed`, `absent`, `refused`. Any `refused` makes the exit code 1.

`skill path` holds `bundled` (where the packaged skill lives) and `files` (the skill file list). With `--target` or `--project` it also holds `targets[]` with `path`, `state`, and `installed_version`. States: `absent`, `foreign` (files with no agentperf manifest), `clean` (files match the manifest), `modified` (files differ, or extra files exist).

An installed skill folder holds `.agentperf-skill.json`: `{skill, package_version, files}`, where `files` maps each skill file to the SHA-256 of what agentperf wrote. Install and uninstall compare the folder against it and refuse to touch edits without `--force`.

## The result file (`run` output)

`run` writes the full measurement record. Agents rarely need more than these top-level fields; prefer the summaries above.

| Field | Meaning |
|---|---|
| `schema` | Result format version. This suite writes `1`. |
| `kind` | `system` for suite results. Live runs write `live` with a different shape; do not feed live files to `compare`. |
| `suite_version`, `package_version` | Suite that measured; program that ran. They can differ for old files. |
| `label` | Machine name in reports. Defaults to the hostname. Private: `share` replaces it. |
| `run_id` | Short id for this run. Removed by `share`, at every depth. |
| `started_at`, `duration_s`, `profile` | Start time (UTC), seconds taken, `quick` or `standard`. |
| `corpus.files`, `corpus.bytes`, `corpus.fixture_hash` | Test project size and input hash. |
| `system` | Probed facts: CPU, OS, disks, power, load, security agents, WSL flags, work folder facts. Holds private names; `share` generalizes it. |
| `tools` | Tool name to path used. `share` keeps file names only. |
| `tool_versions` | First version line per tool. Part of any tool gap is here. |
| `benchmarks` | Test id to measurement entry. See below. |
| `skipped` | Test id to reason, for tests that did not run. |
| `fingerprint` | Measurement and scoring fingerprint over the scored tests. |
| `combined_runs` | Present after `--runs N` or `combine`: the runs that fed the medians. |
| `merged_runs` | Present after `merge`: passes folded into this file. |
| `reference_profile` | Present only in bundled reference files: name, date, and description. |
| `anonymized` | Present in `share` output: marks the file as scrubbed. |

Each `benchmarks.<id>` entry:

| Field | Meaning |
|---|---|
| `category`, `title`, `unit` | Group, display name, and unit (`ms`, `MB/s`, ...). |
| `lower_is_better`, `weight`, `scored`, `ops` | Scoring definition. `ops` divides the median for per-operation cost. |
| `median`, `min`, `max`, `p10`, `p90`, `n`, `cv` | Sample statistics. `cv` is spread over mean; above 0.15 sets `noisy`. |
| `noisy` | True when samples varied a lot. Treat the value gently. |
| `samples` | Raw measurements. |
| `run_samples`, `run_medians`, `runs` | Present in combined results: per-run detail behind the median of medians. |
| `models` | The agent action this test stands for. |
| `meta` | Method detail (for example shell kind). Part of comparability. |

### Comparability contract

Two results compare strictly only when, against the same reference file, all of these match: `corpus.fixture_hash`, every shared scored test's definition, every shared scored test's method (`meta`), and the fingerprint over the shared scored tests. Version strings inform; they never decide. `agentperf report` and `compare` print a warning per mismatch, and the summaries carry them as `comparability_warnings`. A missing test does not break comparability. It lowers `coverage`.
