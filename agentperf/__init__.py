"""agentperf: measure how well a computer serves AI coding agents.

Standard library only. Supports Python 3.8 through 3.14 on macOS, Linux
(including WSL2), and Windows.
"""

# SUITE_VERSION identifies the benchmark suite that produced a result
# (tests, weights, corpus). PACKAGE_VERSION identifies the installed
# program. They are bumped together at release time, but reports label
# them separately so an old result never looks like the running program.
SUITE_VERSION = "0.3.0"
SCHEMA_VERSION = 1


def _package_version() -> str:
    try:
        from importlib import metadata

        return metadata.version("agentperf")
    except Exception:  # running from a checkout or a .pyz without dist info
        return SUITE_VERSION


PACKAGE_VERSION = _package_version()
