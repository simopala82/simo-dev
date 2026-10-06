#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Per-(repo+session) review record for the simo-dev review-loop reminder.

Answers one question: has a code review already covered the files this commit
will include? The record is written from an observed harness event — the
code-reviewer subagent CONCLUDING, on SubagentStop (hooks/review_record.py) —
never from an agent's claim of completion. Both earlier triggers fired at the
start of the work they certified: a `Skill` tool return fires when the loop
STARTS, and an `Agent` tool return is a launch acknowledgement for a background
dispatch. A final instruction step can be skipped too; all three made the old
marker lie.

State file: ~/.claude/state/review-loop (override: REVIEW_MARKER_STATE_FILE).
Entry format: <repo-id>:<session-id>:<epoch>
  Parsed via rsplit(':', 2). session-id has no ':', epoch numeric; repo-id may
  contain ':' (remoteless fallback path). The epoch lives in the CONTENT, not the
  file mtime, so touch/copy/rsync cannot forge a review.
repo-id comes from lib/repo_identity.py — stable across worktrees of one repo.
Entries past PRUNE_AFTER_SECONDS are ignored on read and are cleared from disk only
when compaction runs (see _compact_if_large), so below its threshold they stay in the
file — housekeeping only either way, since the reader filters them. There is no
review TTL; coverage is decided by comparing file mtimes against the record. Two paths
skip that comparison rather than fail it: a path git reports as a tracked deletion, which
has no mtime to compare (check(), where the gap is spelled out), and a plugin manifest
whose only content change is its "version" value, which a release writes after the review
by construction (_is_version_only_bump).

Usage:
    review_marker.py record --repo <path> --session <id>
    review_marker.py check  --repo <path> --session <id>
                            [--expect-skill-log <skill>] --files <f> [<f> ...]
      exit 0  a matching record exists and no file is newer than it, counting the two skip
              paths described above as not newer
      exit 1  no record; a file changed after it; the list named a path that is neither
              present nor a tracked deletion; the deletions could not be listed at all;
              or a path could not be stat'd (reason on stdout). EVERY file changed after
              the record is listed; the other cases stop at the first, since nothing
              after them is trustworthy.
      exit 2  no --session (argparse); a caller without one gets the reminder
      exit 3  --expect-skill-log only: this session has logged other simo-dev skills
              but never that one. Two readings fit — the skill was not invoked in this
              session, or the hooks are silent here — and the printed message carries
              both. simo-dev:commit step 1 settles it by invoking the Skill and re-running
              this check once; only a second rc 3 is a stop.
"""
from __future__ import annotations

import argparse
import contextlib
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# Single-source repo identity from the sibling helper. Inserting the script's own
# dir makes `import repo_identity` resolve both when run as `python3
# review_marker.py` and when loaded via importlib in tests.
_LIB_DIR = str(Path(__file__).resolve().parent)
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)
from repo_identity import repo_id  # noqa: E402
from skill_log import _fields as _skill_log_fields  # noqa: E402

PRUNE_AFTER_SECONDS = 7 * 24 * 3600

# Compaction threshold. The append in record() runs on every review; the rewrite in
# _compact_if_large runs once per roughly this many records, which is what makes its
# race affordable where the same race was not affordable on the hot path.
_COMPACT_AT_LINES = 500


def _now() -> float:
    return time.time()


def _state_file() -> Path:
    return Path(os.environ.get("REVIEW_MARKER_STATE_FILE") or os.path.expanduser("~/.claude/state/review-loop"))


def _skill_log_file() -> Path:
    # expanduser, not Path.home(): honours a monkeypatched HOME in the tests, same
    # as _state_file()'s fallback.
    return Path(os.path.expanduser("~/.claude/state/dev-flow-skills.log"))


def _hooks_look_dead(session: str, skill: str) -> bool:
    """True when THIS session's skill log is silent for (session, skill).

    Four conditions, all required: `session` is non-empty (see the guard below), the
    log exists, it carries at least one parseable line OF THIS SESSION — proof the
    logger has worked for this session — and none of this session's lines is
    (session, skill). Condition 3 is the escape hatch, and scoping it to the session
    is what closes the permanent false positive: a session whose logger never writes
    at all — a cwd that is not a git repository, or malformed stdin JSON, hooks/skill_logger.py
    exits 0 on both — supplies no evidence of its own, yet under the old unscoped form
    any other session's line satisfied condition 3 and rc 3 fired on every call.

    Silence is not proof the hooks are dead. It fits equally well a `skill` that was
    never invoked in this session, which is the normal state the first time a session
    reaches a commit. This function does not resolve that ambiguity; simo-dev:commit
    step 1 does, by invoking the Skill and re-running the check once.

    Keyed on the CALLING skill, not on "any line for this session". The one death
    observed so far was mid-session: on ee6461c5 (2026-08-09) task-intake and grill-me
    had already logged when the cache dir went, and plan-write logged nothing. A
    session-only test stays silent for exactly that. A line this session wrote earlier
    is still evidence — it satisfies condition 3.

    Only reachable with --expect-skill-log, which asserts the caller runs inside a
    Skill invocation. PostToolUse on Skill fires when the Skill tool RETURNS, so a
    live hook has already written the caller's own line by the time the skill body
    runs (background on this in the author's own hook-notes file — worth keeping a similar one).
    hooks/review_loop_warn.py must
    never pass the flag: it is not a skill, and a session that used none would have
    it report dead hooks while being itself proof they are alive.

    Legacy 3-field lines carry no session column and _skill_log_fields drops them, so
    they can neither supply condition 3's evidence nor break condition 4.

    Observed limits, not closed. One: if this session invoked `skill` twice and the
    hooks died between the two, the first line still matches (session, skill) —
    condition 4 is false, so this returns False and stays silent. Closing it needs a
    comparison against the timestamp column. Two, the price of scoping condition 3:
    hooks already silent at the session's start write no line for it at all, so
    condition 3 fails and rc 3 is unreachable — the ee6461c5 diagnosis is not available
    for a death predating the session's first skill, and the flow runs the review loop
    instead. From this log alone the two cannot both be had: the same silence is the
    permanent false positive on one reading and the diagnosis on the other.
    """
    # An empty session id matches no line, so without this every other condition would
    # hold and an unset CLAUDE_CODE_SESSION_ID would hard-stop the commit flow with
    # "restart the session" — advice a restart cannot fix. It is a degenerate input, not
    # evidence of anything: fall through to rc 1, the pre-existing behaviour. That does
    # not self-heal either — the record is written under the payload's real session id,
    # which "" can never match, so the pre-flight keeps returning rc 1 — but a repeated
    # review loop is a far cheaper wrong answer than a hard stop.
    if not session:
        return False
    f = _skill_log_file()
    if not f.exists():
        return False
    session_logged = False
    for line in f.read_text(encoding="utf-8", errors="surrogateescape").splitlines():
        parts = _skill_log_fields(line)
        if parts is None:
            continue
        if parts[1] != session:
            continue
        if parts[4] == skill:
            return False
        session_logged = True
    return session_logged


def _parse(line: str) -> tuple[str, str, int] | None:
    parts = line.rsplit(":", 2)
    if len(parts) != 3 or not parts[0] or not parts[1] or not parts[2].isdigit():
        return None
    return parts[0], parts[1], int(parts[2])


def _live_entries(now: float) -> list[tuple[str, str, int]]:
    f = _state_file()
    if not f.exists():
        return []
    entries = []
    # surrogateescape, like _hooks_look_dead does on the sibling log. Under strict decoding an
    # undecodable store raises UnicodeDecodeError out of check() on every call (measured on
    # 3.10.20 and 3.14.6), and compaction — the only thing that could rewrite the file clean —
    # bails on the same read, so the store never heals. Reading leniently keeps check()
    # answering and lets the garbage fail _parse and drop out at the next compaction.
    # The write side has to match: see the encode note in _compact_if_large.
    for line in f.read_text(encoding="utf-8", errors="surrogateescape").splitlines():
        entry = _parse(line.strip()) if line.strip() else None
        if entry and now - entry[2] <= PRUNE_AFTER_SECONDS:
            entries.append(entry)
    return entries


def _lookup(rid: str, session: str, now: float) -> int | None:
    """Epoch of the newest record for this repo and session, or None.

    The match is always exact, on both keys. Every caller can supply the harness
    session id — the PreToolUse hook reads it from the payload, `simo-dev:commit`
    step 1 from CLAUDE_CODE_SESSION_ID, which carries the same value — so there is no
    session-less mode to fall back to. A caller that cannot supply one gets an argparse
    error, which reads as the reminder rather than as coverage.
    """
    epochs = [t for r, s, t in _live_entries(now) if r == rid and s == session]
    return max(epochs) if epochs else None


def _needs_leading_newline(f: Path) -> bool:
    """True when the store's last line was left without its terminating newline.

    A write cut short mid-line leaves a tail that an append would splice onto, producing one
    entry whose repo-id is the two lines concatenated — and _lookup would then miss the
    session that just recorded, which is the exact failure this module exists to prevent.
    The whole-file rewrite this replaced discarded such a tail for free; an append has to
    repair it. A missing store has no tail to splice onto; an unreadable one may well have
    but cannot be inspected, so False there is a fallback, not a fact — the append two lines
    later fails on the same store anyway.
    """
    try:
        with f.open("rb") as fh:
            if fh.seek(0, os.SEEK_END) == 0:
                return False
            fh.seek(-1, os.SEEK_END)
            return fh.read(1) != b"\n"
    except OSError:
        return False


def _compact_if_large(now: float) -> None:
    """Cold path: collapse duplicates and drop expired entries once the file grows.

    Read-modify-write, unsynchronized: an append landing between the read and the replace
    is lost — the same race lib/skill_log.py documents on its own prune. It is affordable
    here and was not affordable in record() because this runs once per _COMPACT_AT_LINES
    records while the append runs every time. The pid in the temp name keeps two concurrent
    compactions from interleaving into a corrupt file.
    """
    f = _state_file()
    # Both reads are guarded, not just the count: the record is already on disk by the time
    # this runs, so either one failing must end compaction quietly rather than surface as a
    # caller-visible failure.
    try:
        # surrogateescape here too, for the same reason as _live_entries: a strict count on
        # an undecodable store would bail before the compaction that heals it.
        with f.open(encoding="utf-8", errors="surrogateescape") as fh:
            lines = sum(1 for _ in fh)
        if lines <= _COMPACT_AT_LINES:
            return
        newest: dict[tuple[str, str], int] = {}
        for r, s, t in _live_entries(now):
            if t > newest.get((r, s), -1):
                newest[(r, s)] = t
    except (OSError, ValueError):
        return
    # Rewriting cannot help while the live pairs alone fill the file: the result would stay
    # above the trigger, so every later record() would rewrite too and the hot path would be
    # back on the read-modify-write this change removed — permanently, and silently.
    # Requiring the rewrite to at least halve the file removes that mode. The file then
    # peaks at max(_COMPACT_AT_LINES + 1, 2 * live pairs) — simulated over 5000 appends, 1 /
    # 3 / 50 / 250 live pairs all peak at 501, so below ~250 pairs the constant is the bound
    # and the pair count only takes over above it. Each cycle runs from `live` lines up to
    # _COMPACT_AT_LINES + 1, so a rewrite costs (_COMPACT_AT_LINES + 1 - live) appends below
    # the crossover — ~498 at 3 live pairs, 251 at 250 — and one per live-pair
    # count above it. Peaks are measured on the file as record() leaves it, i.e. after this
    # function has run: 500 at 3 and at 250 live pairs, 599 at 300.
    if lines < 2 * len(newest):
        return
    tmp = f.with_name(f"{f.name}.tmp.{os.getpid()}")
    try:
        # errors="surrogateescape" to match the lenient read: a garbage line that happens to
        # parse (three colon-separated fields, numeric tail) reaches here carrying surrogates,
        # and a strict encode raises UnicodeEncodeError — a ValueError, not an OSError, so it
        # would escape record() and leave the temp file behind on every call.
        tmp.write_text("".join(f"{r}:{s}:{t}\n" for (r, s), t in sorted(newest.items())),
                       encoding="utf-8", errors="surrogateescape")
        tmp.replace(f)
    except (OSError, ValueError):
        # The record is already on disk by now. A failed compaction must not turn a written
        # record into a caller-visible failure, nor leave a temp file in the state dir — and
        # the cleanup itself must not reintroduce the failure it is cleaning up after.
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def record(repo: str, session: str) -> int:
    """Append one record. The hot path never rewrites the file.

    Concurrent sessions share this store — five distinct sessions wrote it inside 134
    seconds (measured 2026-08-13). The previous read-modify-write dropped any record
    written between its read and its write, and check() then reported "no review record"
    for a review that had happened. Appending one short line does not interleave on a local
    filesystem, where O_APPEND makes the seek-and-write atomic and the line stays well under
    the stream buffer — this store lives in ~/.claude/state; over NFS the guarantee does not
    hold. The path is not race-free either way: _compact_if_large runs from here and
    rewrites, on the rare branch and with the loss documented there.
    """
    now = _now()
    rid = repo_id(repo)
    f = _state_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    # int() truncation, deliberate: the epoch must stay `.isdigit()`-parseable for _parse.
    # Consequence: a file written in the same second as the review compares as newer than
    # it, so check() reports "changed after the last review". Harmless in practice — a real
    # review takes minutes — but it does surface in scripted runs and in this mechanism's
    # own tests. Fixing it properly means floats in the state-file format, hence a migration.
    repair = _needs_leading_newline(f)
    with f.open("a", encoding="utf-8", errors="surrogateescape") as fh:
        if repair:
            fh.write("\n")
        fh.write(f"{rid}:{session}:{int(now)}\n")
    _compact_if_large(now)
    print(f"review-record: {rid}:{session}")
    return 0


def _tracked_deletions(repo: str) -> set[str] | None:
    """Paths this diff removes, staged or not, or None if git cannot say.

    Lets check() tell a legitimate deletion apart from an argument that never named a real
    file. `diff-index --diff-filter=D HEAD` on purpose, not `ls-files --deleted`: the latter
    compares the index against the working tree, so a deletion already staged with `git rm` has
    left the index and is absent from it — a deletion-only commit would fail the check the moment
    it was staged. This form compares HEAD against the working tree, staged or not.

    Plumbing `diff-index`, never porcelain `diff`: the porcelain refreshes stat-dirty entries and
    writes `.git/index` back through `index.lock`, ignoring GIT_OPTIONAL_LOCKS — a `touch` on any
    tracked file is enough (git 2.55.0, measured 2026-10-06). check() runs in the background
    beside sessions that are committing, and their `git add`/`commit` then fails on the lock.
    `diff-index` does no rename detection, so the source of a staged `git mv` is listed too: a
    real deletion, and check() only asks this set whether a missing path is one.
    `-z` also means git emits paths raw rather than C-quoted, and a C-quoted path is neither
    present on disk nor a known deletion, so this side and the caller's must agree on quoting.
    `hooks/review_loop_warn.py` and `skills/commit/SKILL.md` step 1 both list through
    `lib/changed_files.py`, which uses `-z` too, so the agreement holds for them. A status-band
    mod outside this plugin lists with `status --porcelain=v2` under `quotePath=false` and no
    `-z`: a path it gets back quoted misses here, check() returns 1, and the band reads
    `review ?` — fail-closed, never false coverage. Callers observed 2026-10-06.
    `core.quotePath=false`, which step 1 passed before, is not a substitute: it lifts quoting
    for non-ASCII bytes alone, where `-z` suppresses it entirely. Of six probes a tab, a carriage return, a double quote
    and a backslash still came back quoted from a non-`-z` listing under `quotePath=false`
    (2026-08-16), a space and a non-ASCII byte did not — the non-ASCII half reverses under
    git's default, which the hook leaves unset (2026-08-18); git 2.55.0, samples, not closed
    sets. So a caller that swapped `-z` back for the flag would leave paths this function can
    never be asked about by their real name.

    None means "cannot say" — reachable: repo_id() returns on its first git call and never
    checks for a work tree, so a bare repo or a `.git` dir gets an identity while this exits
    128. The caller fails closed on it.
    """
    try:
        raw = subprocess.run(["git", "-C", repo, "diff-index", "--name-only", "--diff-filter=D", "-z", "HEAD"],
                             capture_output=True, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    # Decoded by hand for the same reason as _is_version_only_bump below, here costing a
    # tracked path its \r before the split. Dropping `text=True` alone would not have done
    # it: subprocess enters text mode if ANY of encoding / errors / text / universal_newlines
    # is set, so `errors=` had to go with it.
    return {p for p in raw.decode("utf-8", "surrogateescape").split("\0") if p}


# One "version" field alone on its line, capturing the value. A substring test was tried and
# is not enough: any single-line replacement whose both sides merely CONTAIN `"version":`
# passes it, so a compact manifest keeping several fields on one line carries the rest of them
# past the gate — measured, `{"name":"p","version":"0.1.0",...}` gaining a `"hooks"` key
# returned True, which is the very edit docs/plugin-release.md documents as breaking plugin
# loading. Anchoring the whole line is what makes the exemption a field test.
_VERSION_LINE_RE = re.compile(r'^[-+]\s*"version":\s*"([^"]*)",?\s*$')

# Byte-identical to VERSION_RE in .claude/scripts/plugin-release.py; the two trees cannot import
# each other, so the shape is duplicated on purpose. It admits any well-formed semver, which is
# weaker than what a DEFAULT release emits — that derives the value from
# max(cache versions, source version) + 1, so it can only increment. It is not weaker than the
# script as a whole: resolve_target_version returns an explicit --version unchanged whenever it
# passes VERSION_RE and is not already in the cache, so a downgrade or a jump past the cache is
# something the script itself will write. What _SEMVER_RE buys is that a malformed value like
# "0.2" is not exempt and still reaches a reviewer.
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _is_version_only_bump(repo: str, path: str) -> bool:
    """True when the sole content change to `path` is a plugin manifest's "version" value.

    `.claude/scripts/plugin-release.py` rewrites exactly that line (`write_manifest_version`,
    one `VERSION_FIELD_RE.subn` with `count=1`); the manifest is the only *tracked* file a
    release writes. Everything else it touches — the cache copy and the `lib/.pytest_cache` the
    verify gate leaves in it, `installed_plugins.json`, the release lockfile, whatever
    `claude plugin marketplace update` refreshes — sits under the gitignored `.claude/plugins/`,
    so none of it reaches the caller's file list.
    Meanwhile `docs/plugin-release.md` requires the release to run only once the review-loop is
    clean. So the bump is newer than the record by construction, and treating it as unreviewed
    work sent `simo-dev:commit` step 1 back through review-loop; a finding there forced a second
    release, which bumped again. On 2026-08-12 that burned 0.1.68, 0.1.69 and 0.1.70, none of
    which reached a commit: `9ad3adb9` carries the survivor, 0.1.67 -> 0.1.71, in one hop.

    The exemption is granted on evidence, never on the path alone: a manifest carrying any other
    hunk, any second changed line, any other field on the version's line, or a value the release
    script could not have written is compared normally. Both entries under "Manifest gotchas" in
    `docs/plugin-release.md` are one-field edits that stopped the plugin loading, so a
    hand-edited manifest has to reach a reviewer.

    Fails closed — a git that cannot describe the diff, an untracked manifest with no diff to
    read — returning False so the caller keeps its existing behaviour, same contract as
    `_tracked_deletions` returning None.

    `diff-index -p` rather than `diff`, so the index is never rewritten — see `_tracked_deletions`;
    here the manifest itself, touched after the review, is the stat-dirty entry that triggers it.
    `-U0` so the hunk carries no context lines; the `---`/`+++` headers are dropped by name
    because they would otherwise read as a removed and an added line. The three `--no-*` flags
    each closed a way for the caller's own git configuration to disable this helper silently
    when it ran porcelain `diff`, all three measured: `color.ui=always` prefixes every content
    line with an ANSI escape, so no line starts with `-`/`+`; a `diff=<driver>` textconv on
    `*.json` flattens the manifest into a shape the line anchor rejects; `GIT_EXTERNAL_DIFF` or
    `diff.external` replaces the output wholesale. `diff-index` ignores all of them even without
    the flags (one combined probe, git 2.55.0, 2026-10-06), so today they are a guard against a
    swap back to porcelain, not load-bearing. Each of the three fails toward the loop this
    helper exists to end, not toward admitting anything — but a fix that turns itself off with
    no error is the failure mode this repo keeps paying for. A mode-only change (`old mode`/
    `new mode`) still rides along unnoticed — it carries no content and is left as a known,
    harmless gap.
    """
    if Path(path).parts[-2:] != (".claude-plugin", "plugin.json"):
        return False
    try:
        raw = subprocess.run(["git", "-C", repo, "diff-index", "-p", "--no-color", "--no-ext-diff",
                              "--no-textconv", "-U0", "HEAD", "--", path],
                             capture_output=True, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    # Decoded here rather than by `text=True`, which also switches on universal-newline
    # translation and rewrites a lone \r inside a line to \n before anything below sees it.
    out = raw.decode("utf-8", "surrogateescape")
    # Then split("\n"), never splitlines(): that breaks on eight more separators, which git does
    # not treat as line terminators either — VT, FF, FS, GS, RS, NEL, U+2028, U+2029 (0x0b, 0x0c,
    # 0x1c, 0x1d, 0x1e, 0x85, 0x2028, 0x2029; swept over U+0000-U+10FFFF on CPython 3.14.6, so
    # the eight are the whole set for this version, not a sample). Both layers matter and each
    # was measured to admit the same bypass on its own: a version line carrying one of the nine,
    # followed by a second field, was cut before the anchor below saw the smuggled part, so
    # `check()` returned 0 for a manifest that had gained a "hooks" key (git 2.55.0). The anchor
    # is a field test only while it spans what git calls a line.
    changed = [ln for ln in out.split("\n")
               if ln[:1] in ("-", "+") and not ln.startswith(("---", "+++"))]
    if len(changed) != 2 or not changed[0].startswith("-") or not changed[1].startswith("+"):
        return False
    before, after = _VERSION_LINE_RE.match(changed[0]), _VERSION_LINE_RE.match(changed[1])
    if not before or not after:
        return False
    return before.group(1) != after.group(1) and bool(_SEMVER_RE.match(after.group(1)))


def _dead_hooks_verdict(because: str, session: str, expect_skill_log: str) -> bool:
    """Print the dead-hooks diagnosis and return True when it applies.

    Called from BOTH failing paths, not just the no-record one. A record that exists
    but is older than a changed file is the same dead-hook situation whenever the
    hooks died after that record was written: the loop re-runs, the reviewer returns,
    review_record.py is dead so the epoch never advances, and rc 1 sends the caller
    round again forever. Gating this on `epoch is None` alone left the mid-session
    shape — the one the spec calls typical — looping with no diagnosis.
    """
    if not (expect_skill_log and _hooks_look_dead(session, expect_skill_log)):
        return False
    print(f"{because}, and this session has logged other simo-dev skills but never "
          f"{expect_skill_log}. Cheapest hypothesis first: this check was reached outside a "
          f"{expect_skill_log} Skill invocation. Invoke the Skill, then re-run this check once "
          "— PostToolUse on Skill writes the entry when the tool returns, so inside a real "
          "invocation the re-run can only confirm. If it still lands here, the hooks are "
          "silent for real; causes observed so far, not a closed set: the plugin is set to "
          "false in ~/.claude/settings.json (re-enable it), the hook scripts are missing from "
          ".claude/local-marketplace/simo-dev/hooks/ in the source tree (restore them), or the "
          "hook entries in ~/.claude/settings.json changed after this session started (restart "
          "the session).")
    return True


def check(repo: str, session: str, files: list[str], expect_skill_log: str = "") -> int:
    now = _now()
    rid = repo_id(repo)
    epoch = _lookup(rid, session, now)
    if epoch is None:
        if _dead_hooks_verdict(f"no review record for {rid}", session, expect_skill_log):
            return 3
        print(f"no review record for {rid}")
        return 1
    deletions: set[str] | None | bool = False   # False = not looked up yet
    stale = False
    for f in files:
        try:
            mtime = (Path(repo) / f).stat().st_mtime
        except FileNotFoundError:
            # A deleted path has no mtime to compare against. Skip it: treating it as
            # changed would make every commit that removes a file — any refactor —
            # fail this check forever. The gap is that a deletion made after the
            # review is not detected here.
            #
            # But only for a deletion git actually knows about. A path that is missing for
            # any other reason means the file list itself is unusable, and skipping it is
            # how an unreviewed diff passed: zsh field-splits an unquoted command
            # substitution, so `--files $(git diff --name-only HEAD)` hands a changed
            # `two words.txt` over as two arguments, both of which miss, leaving nothing
            # compared and returning rc 0 (measured 2026-08-04).
            if deletions is False:
                deletions = _tracked_deletions(repo)
            if deletions is None:
                # Fail closed, same as the OSError branch below: an unknown is not coverage.
                print(f"cannot list deletions in {repo} — not a work tree?")
                return 1
            if f in deletions:
                continue
            print(f"{f} is missing and is not a deletion in this diff — the file list is unusable")
            return 1
        except OSError:
            # Anything else (permissions, bad symlink) is a genuine unknown: fail
            # toward the reminder.
            print(f"cannot verify {f} against the last review")
            return 1
        if mtime > epoch:
            # A release bumps this line after the review by construction — see
            # _is_version_only_bump. Skipped before the dead-hooks verdict below, which
            # would otherwise raise the dead-hooks question over a file that is not
            # evidence at all.
            if _is_version_only_bump(repo, f):
                continue
            # Ask the dead-hooks question ONCE, on the first stale file: the answer does
            # not depend on which file, and its message is a procedure to follow rather
            # than a per-file report, so enumerating the rest underneath it would only
            # bury the diagnosis.
            if not stale and _dead_hooks_verdict(
                    f"{f} changed after the last review", session, expect_skill_log):
                return 3
            # Otherwise report and keep going. Stopping at the first one hid how wide the
            # gap was: an operator told only "CONTEXT.md changed after the last review"
            # re-reviewed that file and hit the same failure on the next one. The two
            # branches above still return immediately — they mean the LIST is unusable, so
            # anything after them would be judged against input that cannot be trusted.
            print(f"{f} changed after the last review")
            stale = True
    return 1 if stale else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_record = sub.add_parser("record")
    p_record.add_argument("--repo", required=True)
    p_record.add_argument("--session", required=True)
    p_check = sub.add_parser("check")
    p_check.add_argument("--repo", required=True)
    p_check.add_argument("--session", required=True)
    p_check.add_argument("--files", nargs="*", default=[])
    p_check.add_argument("--expect-skill-log", default="",
                         help="name of the calling skill; enables the hook-liveness probe")
    args = parser.parse_args(argv)
    if args.cmd == "record":
        return record(args.repo, args.session)
    return check(args.repo, args.session, args.files, args.expect_skill_log)


if __name__ == "__main__":
    # A Windows pipe/redirect defaults to the ANSI code page (cp1252), which cannot encode
    # most non-ASCII text. stderr keeps backslashreplace: reconfigure() without errors= resets it to strict.
    # stdout keeps surrogateescape: it echoes paths from argv, which may not be valid UTF-8.
    sys.stdout.reconfigure(encoding="utf-8", errors="surrogateescape")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.exit(main())
