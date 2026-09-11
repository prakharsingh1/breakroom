"""Fetch a commit archive from fixed GitHub hosts; no git process or build hooks."""
import re
from urllib.parse import urlparse

import httpx
import time

from .sources import MAX_ARCHIVE, SourceError, validate_archive


def import_github(repository: str, commit: str, token: str | None = None, *, transport=None):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}", repository):
        raise SourceError("Use a GitHub owner/repository name")
    if not re.fullmatch(r"[a-fA-F0-9]{40}", commit):
        raise SourceError("Pin the repository to a full 40-character commit SHA")
    if token is not None and (not re.fullmatch(r"[A-Za-z0-9_]{20,255}", token)):
        raise SourceError("Invalid GitHub read token")
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = "Bearer " + token
    url = f"https://api.github.com/repos/{repository}/zipball/{commit}"
    started = time.monotonic()
    try:
        with httpx.Client(timeout=20, follow_redirects=False, trust_env=False, transport=transport) as client:
            for _ in range(3):
                with client.stream("GET", url, headers=headers) as response:
                    if response.status_code in {301, 302, 307, 308}:
                        target = response.headers.get("location", "")
                        parsed = urlparse(target)
                        if (parsed.scheme != "https" or parsed.hostname != "codeload.github.com"
                                or parsed.username or parsed.password or parsed.port not in {None, 443}):
                            raise SourceError("GitHub archive redirect is not approved")
                        url, headers = target, {"Accept": "application/zip"}
                        continue
                    if response.status_code != 200:
                        raise SourceError("GitHub archive unavailable; check commit and repository read access")
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        if time.monotonic()-started > 30:
                            raise SourceError("GitHub archive time limit exceeded")
                        data.extend(chunk)
                        if len(data) > MAX_ARCHIVE:
                            raise SourceError("GitHub archive exceeds 1 MiB; use a small agent repository")
                    return validate_archive(bytes(data), github_prefix=True)
    except httpx.HTTPError as exc:
        raise SourceError("GitHub archive request failed; retry later") from exc
    raise SourceError("GitHub archive redirect limit exceeded")
