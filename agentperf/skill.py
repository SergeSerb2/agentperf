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
# The only paths install or uninstall may ever touch inside a
# destination. A manifest entry outside this set proves nothing and is
# ignored: no traversal, absolute, drive, or backslash form of a path
# can be in it, so confinement does not depend on string surgery.
_MANAGED_SET = frozenset(SKILL_FILES)


class _ConfinementError(ValueError):
    """A write or delete would escape the skill destination."""


def _is_link(path: str) -> bool:
    """True for a symlink or a Windows junction/reparse point.

    ``os.path.islink`` misses junctions on older Pythons, so also check
    ``isjunction`` where it exists and the reparse-point attribute."""
    if os.path.islink(path):
        return True
    is_junction = getattr(os.path, "isjunction", None)
    if is_junction is not None:
        try:
            if is_junction(path):
                return True
        except OSError:
            pass
    try:
        attrs = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attrs & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT


def _confined_join(dest: str, rel: str) -> str:
    """Join an allowlisted relative path onto *dest*, proven inside it."""
    if rel != MANIFEST_NAME and rel not in _MANAGED_SET:
        raise _ConfinementError("not a managed skill file: {!r}".format(rel))
    full = os.path.join(dest, *rel.split("/"))
    dest_abs = os.path.abspath(dest)
    try:
        inside = os.path.commonpath([dest_abs, os.path.abspath(full)]) == dest_abs
    except ValueError:
        inside = False
    if not inside:
        raise _ConfinementError("path escapes the skill folder: {!r}".format(rel))
    return full


def _link_in_path(dest: str, rel: str) -> Optional[str]:
    """The first existing link on the path dest/rel (dest included)."""
    if _is_link(dest):
        return dest
    current = dest
    for part in rel.split("/"):
        current = os.path.join(current, part)
        if os.path.lexists(current) and _is_link(current):
            return current
    return None
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


def _disk_files(dest: str) -> Tuple[Dict[str, bytes], List[str]]:
    """Files under *dest* as ({posix relative path: bytes}, [links]).

    Links are never followed or read: a linked entry is reported by
    name so callers can refuse to write or delete through it, and
    linked directories are never descended into."""
    out: Dict[str, bytes] = {}
    links: List[str] = []
    for root, dirs, names in os.walk(dest):
        kept = []
        for name in dirs:
            full = os.path.join(root, name)
            if _is_link(full):
                links.append(os.path.relpath(full, dest).replace(os.sep, "/"))
            else:
                kept.append(name)
        dirs[:] = kept
        for name in names:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, dest).replace(os.sep, "/")
            if _is_link(full):
                links.append(rel)
                continue
            try:
                with open(full, "rb") as fh:
                    out[rel] = fh.read()
            except OSError:
                # Unreadable: report it like a link (present but
                # unverifiable) instead of crashing the inspection.
                links.append(rel)
    return out, sorted(links)


def _recorded_files(manifest: Optional[Dict[str, object]]) -> Dict[str, str]:
    """The manifest's managed entries: allowlisted paths, string hashes.

    Anything else in a manifest (traversal paths, absolute paths,
    backslash tricks, non-string values) is dropped here, so no caller
    can act on it."""
    files = (manifest or {}).get("files")
    if not isinstance(files, dict):
        return {}
    return {rel: want for rel, want in files.items()
            if rel in _MANAGED_SET and isinstance(want, str)}


def _load_manifest(dest: str) -> Optional[Dict[str, object]]:
    """The install manifest, or None when absent, linked, or malformed.

    A manifest whose ``files`` is not an object cannot establish
    ownership of anything; the copy is foreign."""
    path = os.path.join(dest, MANIFEST_NAME)
    if _is_link(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("files"), dict):
        return None
    return doc


def inspect(dest: str) -> Tuple[str, Dict[str, object]]:
    """Classify an install destination without touching it.

    Returns (state, detail). States: ``absent`` (nothing there),
    ``foreign`` (files but no agentperf manifest), ``clean`` (on-disk
    files match the manifest exactly), ``modified`` (managed files differ
    from the manifest, or extra files are present).
    """
    if not os.path.isdir(dest):
        # A file or link sitting at the destination is foreign, not absent:
        # install must not treat taken ground as free ground.
        return ("foreign", {}) if os.path.lexists(dest) else ("absent", {})
    manifest = _load_manifest(dest)
    if manifest is None or manifest.get("skill") != SKILL_NAME:
        return "foreign", {}
    disk, links = _disk_files(dest)
    recorded = _recorded_files(manifest)
    changed = [rel for rel, want in recorded.items()
               if rel not in disk or _hash(disk[rel]) != want]
    extra = sorted([rel for rel in disk
                    if rel not in recorded and rel != MANIFEST_NAME] + links)
    detail = {"installed_version": manifest.get("package_version"),
              "changed": changed, "extra": extra}
    return ("modified" if changed or extra else "clean"), detail


def _write_skill(dest: str, files: Dict[str, bytes]) -> None:
    """Write the managed files and manifest. Never through a link.

    Raises _ConfinementError when any target path (or a directory on
    the way to it) is a link or escapes *dest*; ``--force`` never
    bypasses this, because writing through a link writes wherever the
    link points."""
    for rel in list(files) + [MANIFEST_NAME]:
        bad = _link_in_path(dest, rel)
        if bad is not None:
            raise _ConfinementError(
                "refusing to write through a link: {}. Remove the link "
                "yourself if this copy should be replaced.".format(bad))
        _confined_join(dest, rel)
    os.makedirs(dest, exist_ok=True)
    for rel, data in files.items():
        full = _confined_join(dest, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(data)
    manifest = {"skill": SKILL_NAME, "package_version": PACKAGE_VERSION,
                "files": {rel: _hash(data) for rel, data in sorted(files.items())}}
    with open(_confined_join(dest, MANIFEST_NAME), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")


def install_one(dest: str, force: bool = False) -> Tuple[str, str]:
    """Install or update one destination. Returns (status, message).

    Confinement refusals (a link where a managed file or directory
    belongs) hold even with ``force``: force replaces contents, it
    never widens where writes may land."""
    files = bundled_files()
    state, detail = inspect(dest)
    try:
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
            disk, _links = _disk_files(dest)
            if all(_hash(disk.get(rel, b"")) == _hash(data) for rel, data in files.items()):
                _write_skill(dest, files)  # refresh the manifest version
                return "current", "already up to date (agentperf {})".format(PACKAGE_VERSION)
            _write_skill(dest, files)
            return "updated", "updated from agentperf {} to {}".format(
                detail.get("installed_version") or "?", PACKAGE_VERSION)
        # forced over a foreign or modified copy: replace managed files only,
        # so a user's own extra files in the directory survive.
        _write_skill(dest, files)
    except _ConfinementError as exc:
        return "refused", str(exc)
    except OSError as exc:
        return "failed", "could not write the skill: {}".format(exc)
    kept = detail.get("extra") if state == "modified" else None
    note = " (kept extra files: {})".format(", ".join(kept)) if kept else ""
    return "installed", "installed with --force over the existing copy" + note


def uninstall_one(dest: str, force: bool = False) -> Tuple[str, str]:
    """Remove one destination. Returns (status, message).

    Only recognized skill files are ever removed, each by an
    allowlisted, confined path: manifest entries outside the managed
    set are ignored, and a forced uninstall of a foreign copy removes
    the recognized skill files and keeps everything else. The whole
    directory is removed only when nothing but agentperf's own files
    was ever in it (it is empty afterwards). A managed file that
    cannot be removed is a failure, never a silent "extra file"."""
    if os.path.lexists(dest) and _is_link(dest):
        return "refused", ("refusing to remove through a link: {}. Remove "
                            "the link yourself if this copy should go.".format(dest))
    state, detail = inspect(dest)
    if state == "absent":
        return "absent", "nothing installed here"
    if state == "foreign" and not force:
        return "refused", ("refusing to remove: no agentperf manifest, so agentperf "
                            "did not install this copy. Re-run with --force to remove "
                            "the recognized skill files anyway (your other files stay).")
    if state == "modified" and not force:
        return "refused", ("refusing to remove a modified copy (changed: {}; extra: {}). "
                            "Re-run with --force to remove it.".format(
                                ", ".join(detail["changed"]) or "none",
                                ", ".join(detail["extra"]) or "none"))
    if state == "foreign":
        candidates = [rel for rel in SKILL_FILES + [MANIFEST_NAME]
                      if os.path.lexists(_confined_join(dest, rel))]
        if not candidates:
            return "refused", ("no recognized agentperf skill files here; "
                                "nothing was removed")
    else:
        recorded = _recorded_files(_load_manifest(dest))
        candidates = list(recorded) + [MANIFEST_NAME]
    removed: List[str] = []
    failed: List[str] = []
    for rel in candidates:
        full = _confined_join(dest, rel)
        bad = _link_in_path(dest, "/".join(rel.split("/")[:-1])) \
            if "/" in rel else None
        if bad is not None:
            failed.append("{} (through a link)".format(rel))
            continue
        if not os.path.lexists(full):
            continue
        if os.path.isdir(full) and not _is_link(full):
            failed.append("{} (a directory, not a file)".format(rel))
            continue
        try:
            os.remove(full)  # a link at full is unlinked, never followed
            removed.append(rel)
        except OSError:
            try:
                os.rmdir(full)  # a directory symlink on Windows
                removed.append(rel)
            except OSError:
                failed.append(rel)
    disk, links = _disk_files(dest) if os.path.isdir(dest) else ({}, [])
    leftover = sorted(set(disk) | set(links))
    for sub in ("references",):
        sub_path = os.path.join(dest, sub)
        if not _is_link(sub_path):
            try:
                os.rmdir(sub_path)
            except OSError:
                pass
    try:
        os.rmdir(dest)
    except OSError:
        pass
    if failed:
        return "failed", ("could not remove: {} (removed: {}). The copy is "
                          "still partly installed.".format(
                              ", ".join(failed),
                              ", ".join(removed) or "nothing"))
    if leftover:
        return "removed", "removed the skill; kept your extra files: {}".format(
            ", ".join(leftover))
    return "removed", "removed the agentperf skill"
