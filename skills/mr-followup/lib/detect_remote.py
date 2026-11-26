#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Detect the git forge and repo slug from origin. Python port of detect_remote.sh
(OSS migration step 7c) -- same URL classification (git@host:path.git,
ssh://git@host/path, https://host/path[.git]), same SSH host-alias resolution via
`ssh -G`, same DETECT_REMOTE_URL test-isolation override.

Usage: detect_remote.py <worktree-abs>
Prints `FORGE=<gitlab|github>  SLUG=<owner/repo>  API_HOST=<hostname>` to stdout and
exits 0 on success. On failure (missing argument, unresolvable forge) prints a
message to stderr and exits 1 -- this is a deliberate contract CHANGE from the bash
predecessor (approved design, Decision 1): the bash version was `source`d and
`export`ed FORGE/SLUG/API_HOST into the caller's shell, which a Python subprocess
cannot do. Its callers are now Python (lib/mr_create.py and lib/mr_followup.py import
detect()); the stdout line stays for a manual run.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

_GITLAB_HOST_RE = re.compile(r"^gitlab\.")


def _forge_for_host(host: str) -> str | None:
    if _GITLAB_HOST_RE.match(host):
        return "gitlab"
    if "github.com" in host:
        return "github"
    return None


def _host_from_url(url: str) -> str:
    host = re.sub(r"^(ssh://)?git@", "", url)
    host = re.sub(r"^https?://", "", host)
    host = re.sub(r"[:/].*$", "", host)
    return host


def _slug_from_url(url: str) -> str:
    slug = re.sub(r"^(ssh://git@[^/]+/|git@[^:]+:|https?://[^/]+/)", "", url)
    slug = re.sub(r"\.git$", "", slug)
    return slug


def _resolve_ssh_alias(host: str) -> str:
    """Resolve a custom SSH host alias to its real HostName via `ssh -G`. Returns ''
    on any failure or on no `hostname` line -- the caller treats that the same as an
    alias that did not resolve."""
    try:
        result = subprocess.run(["ssh", "-G", host], capture_output=True, text=True, check=False)
    except OSError:
        return ""
    for line in result.stdout.splitlines():
        if line.startswith("hostname "):
            return line.split(None, 1)[1].strip()
    return ""


def detect(repo: str | None) -> tuple[str, str, str] | None:
    """Returns (forge, slug, api_host) on success, None on failure (a message is
    already printed to stderr in that case)."""
    override = os.environ.get("DETECT_REMOTE_URL", "")
    if override:
        url = override
    elif not repo:
        print("detect_remote: missing repo path argument (pass the absolute "
              "worktree/repo path as the first argument)", file=sys.stderr)
        return None
    else:
        result = subprocess.run(["git", "-C", repo, "remote", "get-url", "origin"],
                                capture_output=True, text=True, check=False)
        url = result.stdout.strip()

    host = _host_from_url(url)
    forge = _forge_for_host(host)
    if forge is not None:
        api_host = host
    else:
        resolved = ""
        if url.startswith("git@") or url.startswith("ssh://git@"):
            resolved = _resolve_ssh_alias(host)
        forge = _forge_for_host(resolved) if resolved else None
        if forge is None:
            print("detect_remote: unknown forge for origin '{}' (host '{}' -> '{}')".format(
                url, host, resolved or "?"), file=sys.stderr)
            return None
        api_host = resolved

    slug = _slug_from_url(url)
    return forge, slug, api_host


def main(argv: list[str]) -> int:
    repo = argv[0] if argv else None
    result = detect(repo)
    if result is None:
        return 1
    forge, slug, api_host = result
    print("FORGE={}  SLUG={}  API_HOST={}".format(forge, slug, api_host))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
