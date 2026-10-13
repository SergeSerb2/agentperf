"""Deterministic fixture repo generator for ``agentperf live``."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess

from ..util import rmtree

FILLER_COUNT = 150

def _sequences_src(seed_bug):
    expr = "values[start:end]" if seed_bug else "values[start:end + 1]"
    return '''"""Sequence helpers."""


def window_sum(values, start, end):
    """Return the sum of values[start] through values[end], inclusive."""
    return sum(%s)


def moving_average(values, width):
    if width <= 0:
        raise ValueError("width must be positive")
    out = []
    for i in range(len(values) - width + 1):
        out.append(sum(values[i:i + width]) / width)
    return out
''' % expr

_LABELS = '''"""Label formatting."""


def format_label(name, value):
    return "%s: %s" % (name, value)


def parse_label(text):
    name, _, value = text.partition(":")
    return name.strip(), value.strip()
'''

_NUMBERS = '''"""Numeric helpers."""


def double(value):
    return value * 2


def is_even(value):
    return value % 2 == 0
'''

_TEST_SEQ = '''import unittest

from calcbox.sequences import moving_average, window_sum


class WindowSumTests(unittest.TestCase):
    def test_inclusive_end(self):
        self.assertEqual(window_sum([1, 2, 3, 4], 0, 2), 6)

    def test_single_element(self):
        self.assertEqual(window_sum([5, 6, 7], 1, 1), 6)

    def test_moving_average(self):
        self.assertEqual(moving_average([1, 2, 3], 2), [1.5, 2.5])


if __name__ == "__main__":
    unittest.main()
'''

_TEST_LABELS = '''import unittest

from calcbox.labels import format_label, parse_label


class LabelTests(unittest.TestCase):
    def test_format(self):
        self.assertEqual(format_label("age", 3), "age: 3")

    def test_parse(self):
        self.assertEqual(parse_label("age: 3"), ("age", "3"))


if __name__ == "__main__":
    unittest.main()
'''

_TEST_NUMBERS = '''import unittest

from calcbox.numbers import double, is_even


class NumberTests(unittest.TestCase):
    def test_double(self):
        self.assertEqual(double(3), 6)

    def test_is_even(self):
        self.assertTrue(is_even(4))
        self.assertFalse(is_even(5))


if __name__ == "__main__":
    unittest.main()
'''

# Modules that call format_label (the rename task touches these plus
# calcbox/labels.py itself and tests/test_labels.py).
_CONSUMERS = ["report", "summary", "invoice", "badge", "header", "footer", "caption"]


def _consumer_src(name):
    return (
        '"""%s rendering."""\n\nfrom calcbox.labels import format_label\n\n\n'
        "def %s_line(value):\n    return format_label(\"%s\", value)\n" % (name, name, name)
    )


_TEST_CONSUMERS = '''import unittest

from calcbox.report import report_line


class ConsumerTests(unittest.TestCase):
    def test_report_line(self):
        self.assertEqual(report_line(9), "report: 9")


if __name__ == "__main__":
    unittest.main()
'''


def _filler_src(index):
    digest = hashlib.sha256(("filler-%d" % index).encode("ascii")).hexdigest()
    a = int(digest[:4], 16) % 100
    b = int(digest[4:8], 16) % 100
    return (
        '"""Generated filler module %d."""\n\nVALUE_%d = %d\n\n\n'
        "def filler_%d(x):\n    return x * %d + %d\n" % (index, index, a, index, a, b)
    )


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def build_fixture(dest, seed_bug=False):
    """Create the fixture repo at *dest*. seed_bug plants the fix-task bug."""
    dest = os.path.abspath(dest)
    os.makedirs(os.path.join(dest, "calcbox", "filler"), exist_ok=True)
    os.makedirs(os.path.join(dest, "tests"), exist_ok=True)

    _write(os.path.join(dest, "calcbox", "__init__.py"), '"""calcbox fixture package."""\n')
    _write(os.path.join(dest, "calcbox", "sequences.py"), _sequences_src(seed_bug))
    _write(os.path.join(dest, "calcbox", "labels.py"), _LABELS)
    _write(os.path.join(dest, "calcbox", "numbers.py"), _NUMBERS)
    _write(os.path.join(dest, "calcbox", "filler", "__init__.py"), "")
    for name in _CONSUMERS:
        _write(os.path.join(dest, "calcbox", name + ".py"), _consumer_src(name))
    for i in range(FILLER_COUNT):
        _write(os.path.join(dest, "calcbox", "filler", "mod%03d.py" % i), _filler_src(i))

    _write(os.path.join(dest, "tests", "__init__.py"), "")
    _write(os.path.join(dest, "tests", "test_sequences.py"), _TEST_SEQ)
    _write(os.path.join(dest, "tests", "test_labels.py"), _TEST_LABELS)
    _write(os.path.join(dest, "tests", "test_numbers.py"), _TEST_NUMBERS)
    _write(os.path.join(dest, "tests", "test_consumers.py"), _TEST_CONSUMERS)
    _write(os.path.join(dest, "README.md"),
           "# calcbox\n\nFixture package for agentperf live. Run tests with `python -m unittest`.\n")

    def git(*args):
        subprocess.run(["git", "-C", dest] + list(args), check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       encoding="utf-8", errors="replace")

    git("init", "-q")
    git("add", "-A")
    git("-c", "user.name=agentperf", "-c", "user.email=agentperf@localhost",
        "commit", "-q", "-m", "initial commit")
    return dest


def fresh_copy(base, dest):
    """Copy a built fixture (including .git) to *dest*."""
    if os.path.exists(dest):
        rmtree(dest)  # clears the read-only bits git sets on Windows
    shutil.copytree(base, dest, symlinks=False)
    return dest


def tree_hash(root):
    """Hash of all file paths+contents under *root*, excluding .git."""
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        if ".git" in dirnames:
            dirnames.remove(".git")
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            h.update(rel.encode("utf-8"))
            with open(path, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()
