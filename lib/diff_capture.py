#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Capture a git diff into a temp file, gated by size: prints the full diff
under threshold, else `git diff --stat` plus per-file slices for files under
1000 lines, plus the temp file's path. Applied at the capture point so a
large diff never enters the calling session's context whole in the first
place. See CONTEXT.md 'Diff capture gate'."""
import subprocess
import sys
import tempfile

DEFAULT_THRESHOLD = 50_000
SLICE_LINE_LIMIT = 1000


class GitError(Exception):
    pass


def _run(args, repo):
    return subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def untracked_files(repo):
    # core.quotePath=false: without it, status octal-escapes non-ASCII paths
    # (e.g. "caf\303\251.txt"), and a naive `line[3:]` slice would keep the
    # quotes and later commands on that literal would silently fail.
    result = _run(["-c", "core.quotePath=false", "status", "--porcelain", "--untracked-files=all"], repo)
    if result.returncode != 0:
        raise GitError(result.stderr)
    return [line[3:] for line in result.stdout.splitlines() if line.startswith("?? ")]


def capture(repo, diff_args, include_untracked):
    result = _run(["diff", *diff_args], repo)
    if result.returncode != 0:
        raise GitError(result.stderr)
    parts = [result.stdout]
    if include_untracked:
        for f in untracked_files(repo):
            # `diff --no-index` exits 1 whenever the two sides differ, which they
            # always do here (comparing against /dev/null) -- not a failure.
            parts.append(_run(["diff", "--no-index", "/dev/null", f], repo).stdout)
    return "".join(parts)


def _line_count(repo, path, diff_args, is_untracked):
    if is_untracked:
        result = _run(["diff", "--no-index", "--numstat", "/dev/null", path], repo)
    else:
        result = _run(["diff", "--numstat", *diff_args, "--", path], repo)
    lines = result.stdout.strip().splitlines()
    if not lines:
        return 0
    added, removed, _ = lines[0].split("\t", 2)
    return (0 if added == "-" else int(added)) + (0 if removed == "-" else int(removed))


def _slice(repo, path, diff_args, is_untracked):
    if is_untracked:
        return _run(["diff", "--no-index", "/dev/null", path], repo).stdout
    return _run(["diff", *diff_args, "--", path], repo).stdout


def summarize(repo, diff_args, include_untracked):
    stat = _run(["diff", "--stat", *diff_args], repo)
    if stat.returncode != 0:
        raise GitError(stat.stderr)
    tracked = _run(["diff", "--name-only", *diff_args], repo).stdout.splitlines()
    out = [stat.stdout]
    files = [(f, False) for f in tracked]
    if include_untracked:
        untracked = untracked_files(repo)
        files += [(f, True) for f in untracked]
        if untracked:
            # `git diff --stat` above never lists untracked files -- without
            # this, a large untracked file is invisible in the summary.
            out.append("\nUntracked files (not shown in --stat above):\n")
            out.extend(f"  {f}\n" for f in untracked)
    for path, is_untracked in files:
        n = _line_count(repo, path, diff_args, is_untracked)
        if n < SLICE_LINE_LIMIT:
            out.append(f"\n--- {path} ---\n")
            out.append(_slice(repo, path, diff_args, is_untracked))
        elif is_untracked:
            out.append(f"\n--- {path} --- ({n} lines, not inlined)\n")
    return "".join(out)


def main(argv):
    if "--" not in argv:
        print(
            "usage: diff_capture.py <repo> [--threshold N] [--include-untracked] -- <git-diff-args...>",
            file=sys.stderr,
        )
        return 2
    split = argv.index("--")
    head, diff_args = argv[:split], argv[split + 1:]
    if not diff_args:
        print("missing git diff arguments after --", file=sys.stderr)
        return 2

    repo = None
    threshold = DEFAULT_THRESHOLD
    include_untracked = False
    i = 0
    while i < len(head):
        tok = head[i]
        if tok == "--threshold":
            i += 1
            if i >= len(head):
                print("--threshold requires a value", file=sys.stderr)
                return 2
            try:
                threshold = int(head[i])
            except ValueError:
                print(f"--threshold value must be an integer: {head[i]!r}", file=sys.stderr)
                return 2
        elif tok == "--include-untracked":
            include_untracked = True
        elif repo is None:
            repo = tok
        else:
            print(f"unexpected argument: {tok}", file=sys.stderr)
            return 2
        i += 1
    if not repo:
        print("missing <repo>", file=sys.stderr)
        return 2

    try:
        full = capture(repo, diff_args, include_untracked)
    except GitError as e:
        print(str(e), file=sys.stderr, end="")
        return 1

    with tempfile.NamedTemporaryFile(
        mode="w", delete=False, prefix="simo-dev-diff-", suffix=".diff", encoding="utf-8"
    ) as f:
        f.write(full)
        tmp_path = f.name

    if len(full) <= threshold:
        print(full, end="")
        return 0

    try:
        summary = summarize(repo, diff_args, include_untracked)
    except GitError as e:
        print(str(e), file=sys.stderr, end="")
        return 1
    print(summary, end="")
    print(f"\nFull diff written to: {tmp_path} ({len(full)} chars)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
