"""Pure helper functions (no Home Assistant imports, unit-testable).

Everything that decides *which* file goes *where* lives here, so the rules are
easy to read, review and test.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
import fnmatch
import hashlib
import os
from pathlib import Path, PurePosixPath
import re

# Characters pCloud (and most file systems) do not accept in names.
_FORBIDDEN_CHARS = re.compile(r'[\x00-\x1f\x7f<>:"\\|?*]')
_MAX_NAME_LENGTH = 255
_HASH_CHUNK = 1024 * 1024


class UnsafePathError(ValueError):
    """Raised when a path would leave its allowed base directory."""


@dataclass(frozen=True, slots=True)
class WatchFolder:
    """A local folder that is synced to a remote sub folder."""

    local: str
    remote: str


@dataclass(frozen=True, slots=True)
class LocalFile:
    """A file found while scanning a watch folder."""

    path: str
    relative: str  # POSIX style, relative to the watch folder
    size: int
    mtime: float


def sanitize_name(name: str) -> str:
    """Return a single, safe path component for pCloud.

    Raises UnsafePathError for names that cannot be made safe ("", ".", "..").
    """
    cleaned = _FORBIDDEN_CHARS.sub("_", name).strip().rstrip(".")
    if cleaned in ("", ".", ".."):
        raise UnsafePathError(f"Invalid name: {name!r}")
    return cleaned[:_MAX_NAME_LENGTH]


def split_remote_path(path: str) -> list[str]:
    """Split a remote path into sanitized components.

    Traversal components ("..") are rejected instead of silently dropped so a
    misconfiguration is visible to the user.
    """
    parts: list[str] = []
    for part in path.replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise UnsafePathError(f"'..' is not allowed in remote paths: {path!r}")
        parts.append(sanitize_name(part))
    return parts


def join_remote(*segments: str) -> str:
    """Join remote path segments into a normalized absolute pCloud path."""
    parts: list[str] = []
    for segment in segments:
        if segment:
            parts.extend(split_remote_path(segment))
    return "/" + "/".join(parts)


def date_subfolder(moment: datetime) -> str:
    """Return the date based sub folder (YYYY/MM/DD)."""
    return moment.strftime("%Y/%m/%d")


def split_patterns(value: str | None) -> list[str]:
    """Split a comma / newline separated pattern list."""
    if not value:
        return []
    return [p.strip() for p in re.split(r"[,\n]", value) if p.strip()]


def matches(relative: str, include: Iterable[str], exclude: Iterable[str]) -> bool:
    """Return True if a relative POSIX path passes include/exclude patterns.

    A pattern matches if it matches the file name *or* the relative path, so
    both ``*.mp4`` and ``front_door/*.jpg`` work. Any path component starting
    with a dot is matched by the ``.*`` pattern too (hidden folders).
    """
    name = PurePosixPath(relative).name
    components = PurePosixPath(relative).parts

    def _hit(pattern: str) -> bool:
        if fnmatch.fnmatchcase(name, pattern) or fnmatch.fnmatchcase(relative, pattern):
            return True
        return any(fnmatch.fnmatchcase(c, pattern) for c in components[:-1])

    include = list(include) or ["*"]
    if not any(fnmatch.fnmatchcase(name, p) or fnmatch.fnmatchcase(relative, p) for p in include):
        return False
    return not any(_hit(p) for p in exclude)


def parse_watch_folders(value: str | None) -> list[WatchFolder]:
    """Parse the watch folder option.

    One folder per line. Optionally map it to a remote sub folder::

        /media/frigate/clips -> frigate/clips
        /config/www/snapshots

    Without a mapping the last component of the local path is used.
    Empty lines and lines starting with '#' are ignored.
    """
    folders: list[WatchFolder] = []
    seen: set[str] = set()
    for raw_line in (value or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "->" in line:
            local, remote = (s.strip() for s in line.split("->", 1))
        else:
            local, remote = line, ""
        local = local.rstrip("/\\") or local
        if not os.path.isabs(local):
            raise UnsafePathError(f"Watch folder must be an absolute path: {local!r}")
        if not remote:
            remote = PurePosixPath(local.replace("\\", "/")).name or "root"
        remote = "/".join(split_remote_path(remote))
        if local in seen:
            continue
        seen.add(local)
        folders.append(WatchFolder(local=local, remote=remote))
    return folders


def is_within(path: str | Path, base: str | Path) -> bool:
    """Return True if *path* is *base* or inside it (after resolving)."""
    try:
        Path(path).resolve().relative_to(Path(base).resolve())
    except ValueError:
        return False
    return True


def scan_folder(
    base: str,
    include: list[str],
    exclude: list[str],
    *,
    min_age: float,
    now: float,
) -> Iterator[LocalFile]:
    """Yield regular files below *base* that match the patterns.

    Blocking - run in an executor. Symlinks are never followed (neither for
    folders nor for files) so a sync can never escape the configured folder.
    Files modified less than *min_age* seconds ago are skipped because they
    may still be written (e.g. a recording in progress).
    """
    base_path = Path(base)
    for root, dirs, files in os.walk(base_path, followlinks=False):
        root_path = Path(root)
        # Prune symlinked directories early.
        dirs[:] = [d for d in dirs if not (root_path / d).is_symlink()]
        for name in files:
            full = root_path / name
            try:
                if full.is_symlink():
                    continue
                stat = full.stat()
            except OSError:
                continue
            if not full.is_file():
                continue
            relative = full.relative_to(base_path).as_posix()
            if not matches(relative, include, exclude):
                continue
            if now - stat.st_mtime < min_age:
                continue
            yield LocalFile(
                path=str(full), relative=relative, size=stat.st_size, mtime=stat.st_mtime
            )


def sha1_of_file(path: str | Path) -> str:
    """Return the hex SHA-1 of a file (blocking)."""
    digest = hashlib.sha1(usedforsecurity=False)
    with open(path, "rb") as handle:
        while chunk := handle.read(_HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def sha1_of_bytes(data: bytes) -> str:
    """Return the hex SHA-1 of a bytes object."""
    return hashlib.sha1(data, usedforsecurity=False).hexdigest()


def remove_empty_parents(path: str | Path, stop_at: str | Path) -> None:
    """Remove empty parent folders of *path* up to (excluding) *stop_at* (blocking)."""
    stop = Path(stop_at).resolve()
    current = Path(path).parent.resolve()
    while current != stop and is_within(current, stop):
        try:
            current.rmdir()  # only succeeds for empty folders
        except OSError:
            return
        current = current.parent


def human_size(num: float) -> str:
    """Format a byte count for log messages."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024 or unit == "TB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"
