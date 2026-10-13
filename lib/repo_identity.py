#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Single-sourced repo/branch identity for the simo-dev libs.

repo-id = normalized origin URL, fallback: absolute path of the main repo
(git-common-dir parent, symlinks resolved) — stable across worktrees.
`repo_path` resolves identity from that directory instead of the process cwd
(fixes worktree cwd divergence — see CLAUDE.md gotcha).

Importers: lib/afk_state.py, lib/afk_log.py, lib/review_marker.py, plus a private-only module
not part of the public export.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


def _git(*args: str, repo_path: str | None = None) -> str | None:
    try:
        out = subprocess.run(["git", *args], capture_output=True, check=True, cwd=repo_path,
                             encoding="utf-8", errors="surrogateescape")
        return out.stdout.strip() or None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def normalize_url(url: str) -> str:
    url = re.sub(r"^[a-z][a-z0-9+.-]*://", "", url.strip(), flags=re.IGNORECASE)
    url = re.sub(r"^[^/]*@", "", url)
    head, sep, tail = url.partition("/")
    if ":" in head:
        url = head.replace(":", "/") + sep + tail
    if url.endswith(".git"):
        url = url[: -len(".git")]
    return url.rstrip("/").lower()


def repo_id(repo_path: str | None = None) -> str:
    url = _git("remote", "get-url", "origin", repo_path=repo_path)
    if url:
        return normalize_url(url)
    common = _git("rev-parse", "--path-format=absolute", "--git-common-dir", repo_path=repo_path)
    if common:
        return str(Path(common).resolve().parent)
    sys.exit("repo_identity: not inside a git repository")


def current_branch(repo_path: str | None = None) -> str:
    branch = _git("branch", "--show-current", repo_path=repo_path)
    if not branch:
        sys.exit("repo_identity: no current branch (detached HEAD?)")
    return branch
