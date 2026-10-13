"""Install, locate, and remove the bundled agentperf Agent Skill.

The canonical skill lives in ``skills/agentperf/`` at the repo root (that
is what the Claude Code plugin loads). An identical copy ships inside the
package as ``agentperf/skill/`` so pip, pipx, and ``.pyz`` users can
install it with ``agentperf skill install``. Tests keep the two copies
byte-identical.

An installed copy carries a ``.agentperf-skill.json`` manifest with the
SHA-256 of every installed file. Install and uninstall compare the files
on disk with that manifest and refuse to touch a copy the user edited
unless ``--force`` is given.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from typing import Dict, List, Optional, Tuple

from . import PACKAGE_VERSION

SKILL_NAME = "agentperf"
MANIFEST_NAME = ".agentperf-skill.json"
SKILL_FILES = [
    "SKILL.md",
    "references/multi-machine.md",
    "references/windows-and-wsl.md",
    "references/reading-results.md",
    "references/troubleshooting.md",
    "references/safety-and-privacy.md",
]
# target name -> skills directory, relative to home (or to a project root)
TARGET_DIRS = {
    "claude": os.path.join(".claude", "skills"),
    "codex": os.path.join(".codex", "skills"),
    "agents": os.path.join(".agents", "skills"),
}
PROJECT_DEFAULT_TARGETS = ["claude", "agents"]

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
BUNDLED_DIR = os.path.join(_PKG_DIR, "skill")


def bundled_files() -> Dict[str, bytes]:
    """Every bundled skill file as {relative path: bytes} (disk or .pyz)."""
    out: Dict[str, bytes] = {}
    for rel in SKILL_FILES:
        path = os.path.join(BUNDLED_DIR, *rel.split("/"))
        data = None
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            pass
        if data is None:
            import pkgutil
            data = pkgutil.get_data("agentperf", "skill/" + rel)
        if data is None:
            raise ValueError("bundled skill file is missing: " + rel)
        out[rel] = data
    return out


def bundled_location() -> str:
    """Where the bundled skill lives, as a display string."""
    if os.path.isdir(BUNDLED_DIR):
        return BUNDLED_DIR
    import zipfile
    parts = _PKG_DIR.split(os.sep)
    for i in range(1, len(parts)):
        candidate = os.sep.join(parts[:i])
        if candidate and os.path.isfile(candidate) and zipfile.is_zipfile(candidate):
            return "{}:agentperf/skill (inside the single-file build; use " \
                   "`agentperf skill install` to copy it out)".format(candidate)
    return "agentperf/skill (inside the package; use " \
           "`agentperf skill install` to copy it out)"


def target_path(target: str, project: Optional[str] = None,
                home: Optional[str] = None) -> str:
    base = os.path.abspath(project) if project else (home or os.path.expanduser("~"))
    return os.path.join(base, TARGET_DIRS[target], SKILL_NAME)


def resolve_targets(requested: Optional[List[str]], project: Optional[str]) -> List[str]:
    if requested:
        bad = [t for t in requested if t not in TARGET_DIRS]
        if bad:
            raise ValueError("unknown skill target {} (valid: {})".format(
                ", ".join(bad), ", ".join(sorted(TARGET_DIRS))))
        return list(requested)
    return list(PROJECT_DEFAULT_TARGETS) if project else sorted(TARGET_DIRS)


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _disk_files(dest: str) -> Dict[str, bytes]:
    """All files under *dest* as {posix relative path: bytes}."""
    out: Dict[str, bytes] = {}
    for root, _dirs, names in os.walk(dest):
        for name in names:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, dest).replace(os.sep, "/")
            with open(full, "rb") as fh:
                out[rel] = fh.read()
    return out


def _load_manifest(dest: str) -> Optional[Dict[str, object]]:
    try:
        with open(os.path.join(dest, MANIFEST_NAME), encoding="utf-8") as fh:
            doc = json.load(fh)
        return doc if isinstance(doc, dict) else None
    except (OSError, ValueError):
        return None


def inspect(dest: str) -> Tuple[str, Dict[str, object]]:
    """Classify an install destination without touching it.

    Returns (state, detail). States: ``absent`` (nothing there),
    ``foreign`` (files but no agentperf manifest), ``clean`` (on-disk
    files match the manifest exactly), ``modified`` (managed files differ
    from the manifest, or extra files are present).
    """
    if not os.path.isdir(dest):
        return "absent", {}
    manifest = _load_manifest(dest)
    if manifest is None or manifest.get("skill") != SKILL_NAME:
        return "foreign", {}
    disk = _disk_files(dest)
    recorded = manifest.get("files") or {}
    changed = [rel for rel, want in recorded.items()
               if rel not in disk or _hash(disk[rel]) != want]
    extra = sorted(rel for rel in disk if rel not in recorded and rel != MANIFEST_NAME)
    detail = {"installed_version": manifest.get("package_version"),
              "changed": changed, "extra": extra}
    return ("modified" if changed or extra else "clean"), detail


def _write_skill(dest: str, files: Dict[str, bytes]) -> None:
    os.makedirs(dest, exist_ok=True)
    for rel, data in files.items():
        full = os.path.join(dest, *rel.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(data)
    manifest = {"skill": SKILL_NAME, "package_version": PACKAGE_VERSION,
                "files": {rel: _hash(data) for rel, data in sorted(files.items())}}
    with open(os.path.join(dest, MANIFEST_NAME), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")


def install_one(dest: str, force: bool = False) -> Tuple[str, str]:
    """Install or update one destination. Returns (status, message)."""
    files = bundled_files()
    state, detail = inspect(dest)
    if state == "absent":
        _write_skill(dest, files)
        return "installed", "installed the agentperf skill"
    if state in ("foreign", "modified") and not force:
        why = ("no agentperf manifest (not installed by agentperf)"
               if state == "foreign"
               else "files differ from what agentperf installed "
                    "(changed: {}; extra: {})".format(
                        ", ".join(detail["changed"]) or "none",
                        ", ".join(detail["extra"]) or "none"))
        return "refused", "refusing to overwrite: {}. Re-run with --force to replace it.".format(why)
    if state == "clean":
        disk = _disk_files(dest)
        if all(_hash(disk.get(rel, b"")) == _hash(data) for rel, data in files.items()):
            _write_skill(dest, files)  # refresh the manifest version
            return "current", "already up to date (agentperf {})".format(PACKAGE_VERSION)
        _write_skill(dest, files)
        return "updated", "updated from agentperf {} to {}".format(
            detail.get("installed_version") or "?", PACKAGE_VERSION)
    # forced over a foreign or modified copy: replace managed files only,
    # so a user's own extra files in the directory survive.
    _write_skill(dest, files)
    kept = detail.get("extra") if state == "modified" else None
    note = " (kept extra files: {})".format(", ".join(kept)) if kept else ""
    return "installed", "installed with --force over the existing copy" + note


def uninstall_one(dest: str, force: bool = False) -> Tuple[str, str]:
    """Remove one destination. Returns (status, message)."""
    state, detail = inspect(dest)
    if state == "absent":
        return "absent", "nothing installed here"
    if state == "foreign" and not force:
        return "refused", ("refusing to remove: no agentperf manifest, so agentperf "
                            "did not install this copy. Re-run with --force to remove it anyway.")
    if state == "modified" and not force:
        return "refused", ("refusing to remove a modified copy (changed: {}; extra: {}). "
                            "Re-run with --force to remove it.".format(
                                ", ".join(detail["changed"]) or "none",
                                ", ".join(detail["extra"]) or "none"))
    if state == "foreign":  # forced: the user asked for the whole directory
        shutil.rmtree(dest)
        return "removed", "removed the whole directory (--force, no manifest)"
    manifest = _load_manifest(dest) or {}
    for rel in list((manifest.get("files") or {}).keys()) + [MANIFEST_NAME]:
        try:
            os.remove(os.path.join(dest, *rel.split("/")))
        except OSError:
            pass
    leftover = _disk_files(dest) if os.path.isdir(dest) else {}
    for sub in ("references",):
        try:
            os.rmdir(os.path.join(dest, sub))
        except OSError:
            pass
    try:
        os.rmdir(dest)
    except OSError:
        pass
    if leftover:
        return "removed", "removed the skill; kept your extra files: {}".format(
            ", ".join(sorted(leftover)))
    return "removed", "removed the agentperf skill"
