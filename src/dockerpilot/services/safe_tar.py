"""Constrained TAR extraction for configuration imports and container restores.

Do not use TarFile.extractall here: its extraction filter is not present on all
supported Python 3.10 installations. Extract only ordinary files, directories
and links that stay within the intended restore root.
"""

from __future__ import annotations

import os
import posixpath
import re
import shutil
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Collection


class UnsafeArchiveError(ValueError):
    """An archive member is unsafe to extract into the requested directory."""


def _parts(name: str) -> tuple[str, ...]:
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or "\x00" in name
        or re.match(r"^[A-Za-z]:", name)
    ):
        raise UnsafeArchiveError(f"Unsafe archive path: {name!r}")
    if ".." in name.split("/"):
        raise UnsafeArchiveError(f"Parent traversal in archive path: {name!r}")
    return PurePosixPath(name).parts


def _within_root(root: Path, candidate: Path) -> bool:
    return candidate.resolve(strict=False).is_relative_to(root.resolve())


def _check_destination(root: Path, relative: Path) -> Path:
    candidate = root / relative
    if not _within_root(root, candidate):
        raise UnsafeArchiveError(f"Archive target outside restore root: {relative}")
    # Reject existing links even if they currently happen to point inward.
    if root.is_symlink() or any(
        (root / Path(*relative.parts[:index])).is_symlink()
        for index in range(1, len(relative.parts) + 1)
    ):
        raise UnsafeArchiveError(f"Symlink in archive destination: {relative}")
    return candidate


def _link_destination(
    root: Path,
    relative: Path,
    linkname: str,
    *,
    hardlink: bool,
    strip_root: str | None,
) -> Path:
    if not linkname or linkname.startswith("/") or "\\" in linkname or "\x00" in linkname:
        raise UnsafeArchiveError(f"Unsafe link target: {linkname!r}")
    if re.match(r"^[A-Za-z]:", linkname):
        raise UnsafeArchiveError(f"Unsafe link target: {linkname!r}")
    if hardlink:
        parts = _parts(linkname)
        if strip_root:
            if not parts or parts[0] != strip_root:
                raise UnsafeArchiveError(f"Hardlink outside archive root: {linkname!r}")
            parts = parts[1:]
        linked = Path(*parts)
    else:
        normalized = posixpath.normpath(
            posixpath.join(str(PurePosixPath(*relative.parts).parent), linkname)
        )
        if normalized.startswith("/") or normalized == ".." or normalized.startswith("../"):
            raise UnsafeArchiveError(f"Symlink escapes archive root: {linkname!r}")
        linked = Path(normalized)
    if hardlink:
        # A hardlink copies an inode; its source must be a normal path rather
        # than a symlink (including links along parent directories).
        return _check_destination(root, linked)

    # A symlink target is *not* an extraction destination. Following a safe
    # internal symlink chain is valid (for example c -> b -> a), so do not
    # reject existing symlinks here. Validate the unresolved path as well:
    # normalizing "alias/../file" first could hide traversal through an alias.
    target = root / relative.parent / linkname
    if not _within_root(root, target):
        raise UnsafeArchiveError(f"Symlink target outside restore root: {linkname!r}")
    return root / linked


def extract_safe(
    archive: tarfile.TarFile,
    root: Path,
    *,
    strip_root: str | None = None,
    allowed_files: Collection[str] | None = None,
) -> None:
    """Extract checked members into root without writing through link paths.

    The complete member list is checked before creating directories or files.
    strip_root removes a validated top-level directory from directory backups.
    allowed_files restricts configuration imports to known regular filenames.
    """
    root = Path(root)
    if root.is_symlink():
        raise UnsafeArchiveError(f"Restore root is a symlink: {root}")
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()

    entries: list[tuple[tarfile.TarInfo, Path]] = []
    root_directory: tarfile.TarInfo | None = None
    seen: set[Path] = set()
    for member in archive.getmembers():
        parts = _parts(member.name)
        if strip_root:
            if not parts or parts[0] != strip_root:
                raise UnsafeArchiveError(f"Unexpected archive root: {member.name!r}")
            parts = parts[1:]
        relative = Path(*parts)
        if not parts:
            if not member.isdir():
                raise UnsafeArchiveError("Only the archive's root directory may be empty")
            if root_directory is not None:
                raise UnsafeArchiveError("Duplicate archive root directory")
            # Both dot-rooted volume backups and stripped bind-mount backups
            # contain the metadata of the destination directory itself.
            root_directory = member
            continue
        if relative in seen:
            raise UnsafeArchiveError(f"Repeated archive member: {member.name!r}")
        seen.add(relative)
        if not (member.isdir() or member.isfile() or member.issym() or member.islnk()):
            raise UnsafeArchiveError(f"Unsupported archive member type: {member.name!r}")
        if allowed_files is not None and (
            len(relative.parts) != 1
            or relative.name not in allowed_files
            or not member.isfile()
        ):
            raise UnsafeArchiveError(f"File not allowed in config archive: {member.name!r}")
        _check_destination(root, relative)
        entries.append((member, relative))

    kinds = {rel: member for member, rel in entries}
    for member, relative in entries:
        for index in range(1, len(relative.parts)):
            ancestor = Path(*relative.parts[:index])
            if ancestor in kinds and not kinds[ancestor].isdir():
                raise UnsafeArchiveError(f"Non-directory archive parent: {ancestor}")
        if member.issym():
            _link_destination(
                root, relative, member.linkname, hardlink=False, strip_root=strip_root
            )
        elif member.islnk():
            target = _link_destination(
                root, relative, member.linkname, hardlink=True, strip_root=strip_root
            )
            if target == root / relative:
                raise UnsafeArchiveError(f"Hardlink refers to itself: {member.name!r}")
            if target.relative_to(root) in kinds and not kinds[target.relative_to(root)].isfile():
                raise UnsafeArchiveError(f"Hardlink does not refer to a regular file: {member.linkname!r}")

    directories = [(member, rel) for member, rel in entries if member.isdir()]
    files = [(member, rel) for member, rel in entries if member.isfile()]
    hardlinks = [(member, rel) for member, rel in entries if member.islnk()]
    symlinks = [(member, rel) for member, rel in entries if member.issym()]

    for member, relative in sorted(directories, key=lambda pair: len(pair[1].parts)):
        path = _check_destination(root, relative)
        path.mkdir(parents=True, exist_ok=True)

    for member, relative in files:
        path = _check_destination(root, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = archive.extractfile(member)
        if stream is None:
            raise UnsafeArchiveError(f"Unreadable archive file: {member.name!r}")
        # Atomic replace avoids truncating existing hardlinks to files outside root.
        fd, temp_name = tempfile.mkstemp(prefix=".dockerpilot-restore-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as output, stream:
                shutil.copyfileobj(stream, output)
            os.chmod(temp_name, member.mode & 0o777)
            if hasattr(os, "geteuid") and os.geteuid() == 0:
                os.chown(temp_name, member.uid, member.gid)
            os.utime(temp_name, (member.mtime, member.mtime))
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    for member, relative in hardlinks:
        path = _check_destination(root, relative)
        target = _link_destination(
            root, relative, member.linkname, hardlink=True, strip_root=strip_root
        )
        if not target.is_file() or target.is_symlink():
            raise UnsafeArchiveError(f"Hardlink source not a regular file: {member.linkname!r}")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise UnsafeArchiveError(f"Unsafe hardlink destination: {relative}")
        # Link into a private staging directory, then atomically replace the
        # destination. An existing hardlink to an unrelated external file is
        # only unlinked, never written through or truncated.
        with tempfile.TemporaryDirectory(
            prefix=".dockerpilot-hardlink-", dir=path.parent
        ) as staging:
            replacement = Path(staging) / "replacement"
            os.link(target, replacement, follow_symlinks=False)
            if replacement.is_symlink():
                raise UnsafeArchiveError(f"Hardlink target became a symlink: {member.linkname!r}")
            _check_destination(root, relative)
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise UnsafeArchiveError(f"Unsafe hardlink destination: {relative}")
            os.replace(replacement, path)

    for member, relative in symlinks:
        path = _check_destination(root, relative)
        _link_destination(root, relative, member.linkname, hardlink=False, strip_root=strip_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or path.exists():
            raise UnsafeArchiveError(f"Symlink destination already exists: {relative}")
        os.symlink(member.linkname, path)
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            os.chown(path, member.uid, member.gid, follow_symlinks=False)

    # Set directory permissions after writing contents, from leaves to root.
    for member, relative in sorted(
        directories, key=lambda pair: len(pair[1].parts), reverse=True
    ):
        path = _check_destination(root, relative)
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            os.chown(path, member.uid, member.gid)
        os.chmod(path, member.mode & 0o777)
        os.utime(path, (member.mtime, member.mtime))

    # Apply the archived root's metadata last, after creating every child.
    # Skipping it breaks permissions/ownership for bind mounts (e.g. 0700)
    # because restore_container_data creates a fresh directory beforehand.
    if root_directory is not None:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            os.chown(root, root_directory.uid, root_directory.gid)
        os.chmod(root, root_directory.mode & 0o777)
        os.utime(root, (root_directory.mtime, root_directory.mtime))
