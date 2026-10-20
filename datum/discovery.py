"""Safe deterministic discovery of source files."""

from __future__ import annotations

import fnmatch
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

from .config import SourceOptions
from .errors import ConfigError


@dataclass(frozen=True)
class FileEntry:
    path: Path
    relative_path: str
    size: int


@dataclass(frozen=True)
class WalkIssue:
    path: str
    message: str


@dataclass(frozen=True)
class DiscoveryResult:
    root: Path
    files: tuple[FileEntry, ...]
    walk_issues: tuple[WalkIssue, ...] = ()
    excluded: int = 0
    hidden: int = 0
    symlinks_skipped: int = 0
    non_regular_skipped: int = 0
    duplicate_targets_skipped: int = 0

    @property
    def total_bytes(self) -> int:
        return sum(entry.size for entry in self.files)


def _is_hidden(relative: str) -> bool:
    return any(part.startswith(".") for part in relative.split("/") if part)


def _is_excluded(relative: str, name: str, patterns: tuple[str, ...]) -> bool:
    for pattern in patterns:
        if fnmatch.fnmatchcase(relative, pattern):
            return True
        if "/" not in pattern and fnmatch.fnmatchcase(name, pattern):
            return True
    return False


def discover(source: SourceOptions) -> DiscoveryResult:
    root = source.path
    if not root.exists():
        raise ConfigError(f"source.path does not exist: {root}")
    if not root.is_dir():
        raise ConfigError(f"source.path must be a directory: {root}")
    root = root.resolve()
    files: list[FileEntry] = []
    issues: list[WalkIssue] = []
    excluded = 0
    hidden = 0
    symlinks_skipped = 0
    non_regular = 0
    duplicate_targets = 0
    visited_dirs: set[tuple[int, int]] = set()
    visited_files: set[tuple[int, int]] = set()

    def walk(directory: Path, relative_dir: str, ancestors: set[tuple[int, int]]) -> None:
        nonlocal excluded, hidden, symlinks_skipped, non_regular, duplicate_targets
        try:
            stat_result = directory.stat()
            identity = (stat_result.st_dev, stat_result.st_ino)
            if identity in ancestors or identity in visited_dirs:
                return
            visited_dirs.add(identity)
            entries = list(os.scandir(directory))
        except OSError as error:
            issues.append(WalkIssue(path=relative_dir or ".", message=str(error)))
            return
        entries.sort(key=lambda entry: entry.name)
        for entry in entries:
            relative = f"{relative_dir}/{entry.name}" if relative_dir else entry.name
            try:
                is_link = entry.is_symlink()
                if is_link and not source.follow_symlinks:
                    symlinks_skipped += 1
                    continue
                if _is_hidden(relative) and not source.include_hidden:
                    hidden += 1
                    continue
                if _is_excluded(relative, entry.name, source.exclude):
                    excluded += 1
                    continue
                entry_stat = entry.stat(follow_symlinks=source.follow_symlinks)
                mode = entry_stat.st_mode
                if stat.S_ISDIR(mode):
                    if not source.recursive:
                        continue
                    real_directory = Path(entry.path).resolve()
                    if not real_directory.is_relative_to(root):
                        issues.append(
                            WalkIssue(path=relative, message="symlink escapes the source root; skipped")
                        )
                        continue
                    real_stat = real_directory.stat()
                    real_identity = (real_stat.st_dev, real_stat.st_ino)
                    if real_identity in ancestors:
                        issues.append(WalkIssue(path=relative, message="symlink directory loop; skipped"))
                        continue
                    walk(real_directory, relative, ancestors | {identity})
                    continue
                if not stat.S_ISREG(mode):
                    non_regular += 1
                    continue
                real_file = Path(entry.path).resolve()
                if not real_file.is_relative_to(root):
                    issues.append(WalkIssue(path=relative, message="file resolves outside the source root; skipped"))
                    continue
                file_stat = real_file.stat()
                file_identity = (file_stat.st_dev, file_stat.st_ino)
                if file_identity in visited_files:
                    duplicate_targets += 1
                    continue
                visited_files.add(file_identity)
                files.append(
                    FileEntry(
                        path=real_file,
                        relative_path=relative,
                        size=file_stat.st_size,
                    )
                )
            except OSError as error:
                issues.append(WalkIssue(path=relative, message=str(error)))

    if not source.recursive:
        try:
            entries = list(os.scandir(root))
        except OSError as error:
            raise ConfigError(f"cannot read source directory {root}: {error}") from error
        for entry in entries:
            relative = entry.name
            try:
                if entry.is_symlink() and not source.follow_symlinks:
                    symlinks_skipped += 1
                    continue
                if _is_hidden(relative) and not source.include_hidden:
                    hidden += 1
                    continue
                if _is_excluded(relative, entry.name, source.exclude):
                    excluded += 1
                    continue
                if not entry.is_file(follow_symlinks=source.follow_symlinks):
                    non_regular += 1
                    continue
                real_file = Path(entry.path).resolve()
                if not real_file.is_relative_to(root):
                    issues.append(WalkIssue(path=relative, message="file resolves outside the source root; skipped"))
                    continue
                file_stat = real_file.stat()
                files.append(FileEntry(path=real_file, relative_path=relative, size=file_stat.st_size))
            except OSError as error:
                issues.append(WalkIssue(path=relative, message=str(error)))
    else:
        walk(root, "", set())

    files.sort(key=lambda entry: entry.relative_path)
    return DiscoveryResult(
        root=root,
        files=tuple(files),
        walk_issues=tuple(issues),
        excluded=excluded,
        hidden=hidden,
        symlinks_skipped=symlinks_skipped,
        non_regular_skipped=non_regular,
        duplicate_targets_skipped=duplicate_targets,
    )
