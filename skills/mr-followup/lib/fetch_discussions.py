#!/usr/bin/env python3
"""
Fetch unresolved review comments from a GitLab MR.

Usage:
    python3 fetch_discussions.py <project> <mr_iid> <host>

    project   GitLab project path, e.g. myorg/geo/backend
    mr_iid    MR internal ID (shown in URL / glab mr view)
    host      GitLab host, e.g. gitlab.example.com (or a full https:// URL)

Token resolution (first match wins):
    1. GITLAB_TOKEN env var
    2. macOS keychain: see the sibling keychain.py for the lookup strategies
"""
import http.client
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Iterator, NoReturn

sys.path.insert(0, str(Path(__file__).resolve().parent))
from keychain import get_token as keychain_get_token  # noqa: E402

# Fail-closed contract, same rc as lib/check_mr_state.py: a fetch that could not
# complete must never be readable as "zero review comments". The signal goes on
# BOTH channels -- rc 2 for a caller that checks the exit status, the stdout
# marker for one that only captures output. ASCII-only, like check_mr_state.py's
# sentinels: an em dash here raises UnicodeEncodeError under an ascii stdout
# (measured with PYTHONIOENCODING=ascii), which loses the marker on the one path
# whose job is to survive a failure.
UNAVAILABLE = "REVIEW COMMENTS UNAVAILABLE - fetch failed, see stderr"
RC_UNVERIFIABLE = 2


def unverifiable(message: str) -> NoReturn:
    print(UNAVAILABLE)
    print(message, file=sys.stderr)
    sys.exit(RC_UNVERIFIABLE)


def get_token() -> str:
    token, err = keychain_get_token(service="gitlab-api-token", env_var="GITLAB_TOKEN")
    if not token:
        unverifiable(err)
    return token


def paginated_get(url: str, token: str) -> Iterator[list]:
    page = 1
    while True:
        paged_url = f"{url}?per_page=100&page={page}"
        req = urllib.request.Request(paged_url, headers={"PRIVATE-TOKEN": token})
        with urllib.request.urlopen(req) as resp:
            batch = json.load(resp)
            if not batch:
                return
            yield batch
            next_page = resp.headers.get("X-Next-Page", "")
            if not next_page:
                return
            page = int(next_page)


def fetch_discussions(project: str, mr_iid: str, token: str, gitlab_url: str) -> list:
    project_encoded = urllib.parse.quote(project, safe="")
    url = f"{gitlab_url}/api/v4/projects/{project_encoded}/merge_requests/{mr_iid}/discussions"
    items = []
    for batch in paginated_get(url, token):
        items.extend(batch)
    return items


def main() -> None:
    if len(sys.argv) != 4:
        print(f"Usage: {sys.argv[0]} <project> <mr_iid> <host>", file=sys.stderr)
        sys.exit(1)

    project, mr_iid, host = sys.argv[1], sys.argv[2], sys.argv[3]
    gitlab_url = host if host.startswith(("http://", "https://")) else f"https://{host}"
    token = get_token()
    # OSError covers HTTPError and URLError (both subclass it) plus socket
    # timeouts; ValueError covers a non-JSON body via JSONDecodeError and a
    # non-integer X-Next-Page; HTTPException covers a failure during the body
    # read, which urlopen has already returned from, so urllib never wraps it
    # into URLError (measured with IncompleteRead: it escaped OSError/ValueError
    # and fell open at rc 1 with empty stdout). Observed cases, not a closed set.
    try:
        discussions = fetch_discussions(project, mr_iid, token, gitlab_url)
    except (OSError, ValueError, http.client.HTTPException) as exc:
        unverifiable(f"Error: could not fetch discussions for {project}!{mr_iid} - {type(exc).__name__}: {exc}")

    count = 0
    for d in discussions:
        # Skip fully resolved threads (individual notes have no resolved field)
        if d.get("resolved"):
            continue

        for note in d.get("notes", []):
            if note.get("system"):
                continue

            pos = note.get("position") or {}
            path = pos.get("new_path") or pos.get("old_path") or "(general comment)"
            line = pos.get("new_line") or pos.get("old_line") or "?"
            author = note["author"]["name"]

            print(f"## {author} — {path} line {line}")
            print(note["body"])
            print()
            count += 1

    if count == 0:
        print("No open review comments found.")


if __name__ == "__main__":
    main()
