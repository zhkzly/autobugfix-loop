from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path


class MemoryFileError(RuntimeError):
    pass


def _require_secure_posix_io() -> None:
    if (
        os.name != "posix"
        or not hasattr(os, "O_NOFOLLOW")
        or not os.supports_dir_fd
    ):
        raise MemoryFileError(
            "Memory authority requires POSIX descriptor-relative no-follow I/O"
        )


def _parts(relative: Path | str) -> tuple[str, ...]:
    path = Path(relative)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise MemoryFileError(f"memory path is not a safe relative path: {relative}")
    return path.parts


def _open_directory(root: Path, parts: tuple[str, ...]) -> int:
    _require_secure_posix_io()
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(root, flags)
    except OSError as exc:
        raise MemoryFileError(f"memory authority root is missing or redirected: {root}") from exc
    try:
        for part in parts:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        observed = os.fstat(descriptor)
        if not stat.S_ISDIR(observed.st_mode):
            raise MemoryFileError("memory authority component is not a directory")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def ensure_directory(
    root: Path | str,
    relative: Path | str,
    *,
    exist_ok: bool = True,
    mode: int = 0o700,
) -> None:
    parts = _parts(relative)
    descriptor = _open_directory(Path(root), ())
    final_created = False
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        for index, part in enumerate(parts):
            created = False
            try:
                child = os.open(part, flags, dir_fd=descriptor)
            except FileNotFoundError:
                try:
                    os.mkdir(part, mode=mode, dir_fd=descriptor)
                except OSError as exc:
                    raise MemoryFileError(
                        f"memory authority directory cannot be created: {relative}"
                    ) from exc
                child = os.open(part, flags, dir_fd=descriptor)
                created = True
            except OSError as exc:
                raise MemoryFileError(
                    f"memory authority directory is redirected: {relative}"
                ) from exc
            os.close(descriptor)
            descriptor = child
            if index == len(parts) - 1:
                final_created = created
        if not exist_ok and not final_created:
            raise MemoryFileError(f"memory authority directory already exists: {relative}")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write(
    root: Path | str,
    relative: Path | str,
    content: bytes,
    *,
    mode: int = 0o600,
) -> None:
    parts = _parts(relative)
    if parts[:-1]:
        ensure_directory(root, Path(*parts[:-1]))
    directory = _open_directory(Path(root), parts[:-1])
    temporary_name = f".{parts[-1]}.{secrets.token_hex(12)}.tmp"
    descriptor = -1
    try:
        try:
            observed = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            observed = None
        except OSError as exc:
            raise MemoryFileError(
                f"memory authority destination cannot be inspected: {relative}"
            ) from exc
        if observed is not None and not stat.S_ISREG(observed.st_mode):
            raise MemoryFileError(
                f"memory authority destination is not a regular file: {relative}"
            )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        descriptor = os.open(temporary_name, flags, mode, dir_fd=directory)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise MemoryFileError(f"memory authority write stalled: {relative}")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(
            temporary_name,
            parts[-1],
            src_dir_fd=directory,
            dst_dir_fd=directory,
        )
        os.fsync(directory)
    except OSError as exc:
        raise MemoryFileError(f"memory authority write failed: {relative}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.close(directory)


def open_lock_descriptor(
    root: Path | str,
    relative: Path | str,
    *,
    mode: int = 0o600,
) -> int:
    """Open one authority lock file without following any path component."""

    parts = _parts(relative)
    directory = _open_directory(Path(root), parts[:-1])
    try:
        flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
        descriptor = os.open(parts[-1], flags, mode, dir_fd=directory)
        observed = os.fstat(descriptor)
        if not stat.S_ISREG(observed.st_mode):
            os.close(descriptor)
            raise MemoryFileError(
                f"memory authority lock is not a regular file: {relative}"
            )
        return descriptor
    except OSError as exc:
        raise MemoryFileError(
            f"memory authority lock is missing or redirected: {relative}"
        ) from exc
    finally:
        os.close(directory)


def replace_directory(
    root: Path | str,
    source: Path | str,
    destination: Path | str,
) -> None:
    """Atomically move an authority directory between trusted parents."""

    source_parts = _parts(source)
    destination_parts = _parts(destination)
    if destination_parts[:-1]:
        ensure_directory(root, Path(*destination_parts[:-1]))
    source_parent = _open_directory(Path(root), source_parts[:-1])
    destination_parent = _open_directory(Path(root), destination_parts[:-1])
    try:
        try:
            source_stat = os.stat(
                source_parts[-1],
                dir_fd=source_parent,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise MemoryFileError(
                f"memory authority source cannot be inspected: {source}"
            ) from exc
        if not stat.S_ISDIR(source_stat.st_mode):
            raise MemoryFileError(
                f"memory authority source is not a directory: {source}"
            )
        try:
            os.stat(
                destination_parts[-1],
                dir_fd=destination_parent,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise MemoryFileError(
                f"memory authority destination cannot be inspected: {destination}"
            ) from exc
        else:
            raise MemoryFileError(
                f"memory authority destination already exists: {destination}"
            )
        os.replace(
            source_parts[-1],
            destination_parts[-1],
            src_dir_fd=source_parent,
            dst_dir_fd=destination_parent,
        )
        os.fsync(source_parent)
        if destination_parent != source_parent:
            os.fsync(destination_parent)
    except OSError as exc:
        raise MemoryFileError(
            f"memory authority directory move failed: {source} -> {destination}"
        ) from exc
    finally:
        os.close(source_parent)
        os.close(destination_parent)


def require_directory(root: Path | str, relative: Path | str) -> None:
    parts = _parts(relative)
    descriptor = _open_directory(Path(root), parts)
    os.close(descriptor)


def read_regular_file(root: Path | str, relative: Path | str, *, label: str) -> bytes:
    parts = _parts(relative)
    directory = _open_directory(Path(root), parts[:-1])
    descriptor = -1
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(parts[-1], flags, dir_fd=directory)
        observed = os.fstat(descriptor)
        if not stat.S_ISREG(observed.st_mode):
            raise MemoryFileError(f"{label} is not a regular file")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            return stream.read()
    except OSError as exc:
        raise MemoryFileError(f"{label} is missing or redirected") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory)
