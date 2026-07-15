from __future__ import annotations

import base64
import json
import os
import stat
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any, Iterable, Mapping


FileIdentity = tuple[int, int, int, int, int]


def _identity(path: Path) -> FileIdentity:
    observed = path.stat(follow_symlinks=False)
    return (
        observed.st_dev,
        observed.st_ino,
        observed.st_size,
        observed.st_mtime_ns,
        observed.st_ctime_ns,
    )


def snapshot_regular_files(root: Path) -> dict[Path, FileIdentity]:
    snapshot: dict[Path, FileIdentity] = {}
    if not root.exists() or root.is_symlink():
        return snapshot
    for directory, names, files in os.walk(root, followlinks=False):
        names[:] = [name for name in names if name != ".git"]
        parent = Path(directory)
        for name in files:
            path = parent / name
            try:
                observed = path.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISREG(observed.st_mode):
                snapshot[path.resolve()] = _identity(path)
    return snapshot


_SECRET_FIELD_MARKERS = (
    "token",
    "apikey",
    "secret",
    "password",
    "privatekey",
    "credential",
)


def _is_secret_field(name: str) -> bool:
    normalized = "".join(character.lower() for character in name if character.isalnum())
    return any(marker in normalized for marker in _SECRET_FIELD_MARKERS)


def _secret_string_values(
    value: Any,
    *,
    field_name: str | None = None,
) -> Iterable[str]:
    if isinstance(value, str):
        if field_name is not None and _is_secret_field(field_name):
            yield value
        return
    if isinstance(value, Mapping):
        for name, item in value.items():
            yield from _secret_string_values(item, field_name=str(name))
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _secret_string_values(item, field_name=field_name)


def credential_markers(
    auth_path: Path | None,
    environment: Mapping[str, str],
) -> tuple[bytes, ...]:
    raw_values: set[bytes] = set()
    if auth_path is not None and auth_path.is_file() and not auth_path.is_symlink():
        auth = auth_path.read_bytes()
        if auth:
            raw_values.add(auth)
        try:
            payload = json.loads(auth.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            payload = None
        for value in _secret_string_values(payload):
            encoded = value.encode("utf-8")
            if len(encoded) >= 8:
                raw_values.add(encoded)
    for name in ("CODEX_API_KEY", "OPENAI_API_KEY"):
        value = environment.get(name, "").encode("utf-8")
        if len(value) >= 8:
            raw_values.add(value)

    markers: set[bytes] = set()
    for value in raw_values:
        markers.add(value)
        markers.add(base64.b64encode(value))
        markers.add(base64.urlsafe_b64encode(value).rstrip(b"="))
        markers.add(value.hex().encode("ascii"))
        markers.add(urllib.parse.quote_from_bytes(value).encode("ascii"))
    return tuple(sorted((item for item in markers if len(item) >= 8), key=len, reverse=True))


def _regular_files(root: Path) -> Iterable[Path]:
    if not root.exists() or root.is_symlink():
        return
    if root.is_file():
        yield root
        return
    for directory, names, files in os.walk(root, followlinks=False):
        names[:] = [name for name in names if name != ".git"]
        parent = Path(directory)
        for name in files:
            path = parent / name
            try:
                observed = path.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISREG(observed.st_mode):
                yield path


def _atomic_redact(path: Path, content: bytes, markers: tuple[bytes, ...]) -> None:
    redacted = content
    replacement = b"<AUTObugfix-credential-redacted>"
    for marker in markers:
        redacted = redacted.replace(marker, replacement)
    mode = stat.S_IMODE(path.stat(follow_symlinks=False).st_mode)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".redacting", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(redacted)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def redact_credential_leaks(
    roots: Iterable[Path],
    markers: tuple[bytes, ...],
    *,
    baseline: Mapping[Path, FileIdentity] | None = None,
) -> tuple[Path, ...]:
    if not markers:
        return ()
    leaks: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        for path in _regular_files(root):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            if baseline is not None and baseline.get(resolved) == _identity(path):
                continue
            content = path.read_bytes()
            if any(marker in content for marker in markers):
                _atomic_redact(path, content, markers)
                leaks.append(resolved)
    return tuple(leaks)
