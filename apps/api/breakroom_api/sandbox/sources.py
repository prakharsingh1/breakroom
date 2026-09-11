"""Bounded source archives are data here: never import, build, or run them."""
from __future__ import annotations

import hashlib
import io
import json
import math
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

MAX_ARCHIVE = 1024 * 1024
MAX_EXPANDED = 4 * 1024 * 1024
MAX_FILE = 256 * 1024
MAX_FILES = 128
CAPABILITIES = frozenset({"payments", "tickets", "reconciliation", "virtual_clock", "events"})
REFERENCE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*")
ALLOWED_SUFFIXES = {".py", ".json", ".txt", ".md", ".toml", ".csv"}


class SourceError(ValueError):
    pass


def bounded_json(raw: bytes, *, limit=65536):
    if len(raw) > limit:
        raise SourceError("JSON message exceeds its byte limit")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise SourceError("Duplicate JSON key")
            result[key] = value
        return result
    def reject(value):
        raise SourceError("Nonfinite JSON value")
    try:
        value = json.loads(raw, object_pairs_hook=unique, parse_constant=reject)
        pending, count = [(value, 0)], 0
        while pending:
            item, depth = pending.pop()
            count += 1
            if depth > 16 or count > 10000:
                raise SourceError("JSON structure exceeds its bounds")
            if isinstance(item, float) and not math.isfinite(item):
                raise SourceError("Nonfinite JSON value")
            if isinstance(item, dict):
                pending.extend((child, depth + 1) for child in item.values())
            elif isinstance(item, list):
                pending.extend((child, depth + 1) for child in item)
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise SourceError("Expected bounded JSON with unique keys") from exc


@dataclass(frozen=True)
class AgentPackage:
    manifest: dict
    files: dict[str, bytes]
    sha256: str
    archive: bytes

    def extract(self, target: Path):
        # Validation has already rejected links, traversal and colliding paths.
        # The directory is newly allocated by the worker, never supplied by a customer.
        for name, content in self.files.items():
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            destination.chmod(0o444)


def validate_archive(raw: bytes, *, github_prefix=False) -> AgentPackage:
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_ARCHIVE:
        raise SourceError("Agent ZIP must be at most 1 MiB")
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
        entries = archive.infolist()
        if not 1 <= len(entries) <= MAX_FILES * 2:
            raise SourceError("Archive contains too many entries")
        files, normalized, total = {}, set(), 0
        prefix = None
        for entry in entries:
            name = entry.filename
            if (not name or len(name) > 240 or "\\" in name or "\x00" in name
                    or name.startswith("/") or not re.fullmatch(r"[A-Za-z0-9_./-]+", name)):
                raise SourceError("Archive contains an unsafe path")
            parts = name.rstrip("/").split("/")
            if any(part in {"", ".", ".."} for part in parts):
                raise SourceError("Hidden files and relative path traversal are not accepted")
            mode = entry.external_attr >> 16
            if stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR} or entry.flag_bits & 1:
                raise SourceError("Links, special files and encrypted ZIP entries are not accepted")
            if github_prefix:
                if prefix is None:
                    prefix = parts[0]
                if parts[0] != prefix:
                    raise SourceError("GitHub archive has multiple root directories")
                parts = parts[1:]
            if parts and (parts[0] in {".github", ".gitignore", ".gitattributes", "LICENSE", "LICENSE.txt"}):
                continue
            if any(part.startswith(".") for part in parts):
                raise SourceError("Hidden files including credentials are not accepted")
            if entry.is_dir():
                continue
            if not parts:
                raise SourceError("Archive file has no relative path")
            name = "/".join(parts)
            lowered = name.casefold()
            if lowered in normalized or any(lowered.startswith(other + "/") or other.startswith(lowered + "/") for other in normalized):
                raise SourceError("Archive contains duplicate or colliding paths")
            if parts[0] in {"breakroom", "breakroom.py", "sitecustomize.py", "usercustomize.py"}:
                raise SourceError("Agent package cannot replace the sandbox adapter interface")
            if PurePosixPath(name).suffix.lower() not in ALLOWED_SUFFIXES:
                raise SourceError("Only Python and supported text/data files are accepted")
            if name.endswith(("requirements.txt", "pyproject.toml")):
                raise SourceError("This runtime uses bundled pure-Python code; remote dependency installation is not supported")
            if entry.file_size > MAX_FILE or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                raise SourceError("Archive file exceeds supported size or compression")
            total += entry.file_size
            if total > MAX_EXPANDED or len(files) >= MAX_FILES:
                raise SourceError("Expanded archive exceeds 4 MiB or 128 files")
            with archive.open(entry) as stream:
                content = stream.read(MAX_FILE + 1)
            if len(content) != entry.file_size or len(content) > MAX_FILE:
                raise SourceError("Archive entry size is inconsistent")
            content.decode("utf-8")
            files[name], normalized = content, normalized | {lowered}
        manifest = bounded_json(files.get("breakroom-agent.json", b""), limit=16384)
        if (not isinstance(manifest, dict) or set(manifest) != {"version", "entrypoint", "capabilities"}
                or manifest["version"] != 1 or type(manifest["version"]) is not int
                or not isinstance(manifest["entrypoint"], str) or len(manifest["entrypoint"]) > 200
                or not REFERENCE.fullmatch(manifest["entrypoint"])):
            raise SourceError("breakroom-agent.json needs version 1, an entrypoint and capabilities")
        capabilities = manifest["capabilities"]
        if (not isinstance(capabilities, list) or len(capabilities) > len(CAPABILITIES)
                or any(not isinstance(item, str) for item in capabilities)
                or len(set(capabilities)) != len(capabilities) or not set(capabilities) <= CAPABILITIES):
            raise SourceError("Unsupported or duplicate adapter capabilities")
        module = manifest["entrypoint"].split(":")[0].replace(".", "/")
        if module + ".py" not in files and module + "/__init__.py" not in files:
            raise SourceError("Entrypoint module is missing from the package")
        return AgentPackage(manifest, files, hashlib.sha256(raw).hexdigest(), raw)
    except (zipfile.BadZipFile, RuntimeError, UnicodeError, OSError, EOFError) as exc:
        raise SourceError("Agent archive is invalid or unsupported") from exc
