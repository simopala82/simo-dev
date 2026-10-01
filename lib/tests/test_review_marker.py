"""Tests for lib/review_marker.py (per-(repo+session) review record)."""
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

LIB = Path(__file__).parent.parent / "review_marker.py"

spec = importlib.util.spec_from_file_location("review_marker", LIB)
rm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rm)

NOW = 1785400000
REPO = "gitlab.example/team/repo-a"


def test_parse_valid_entry():
    assert rm._parse(f"{REPO}:sess-1:{NOW}") == (REPO, "sess-1", NOW)


def test_parse_entry_with_colon_in_repo_id():
    assert rm._parse(f"host:8080/team/repo:sess-1:{NOW}") == ("host:8080/team/repo", "sess-1", NOW)


def test_parse_non_numeric_epoch_returns_none():
    assert rm._parse(f"{REPO}:sess-1:notanumber") is None


def test_parse_too_few_fields_returns_none():
    assert rm._parse(f"{REPO}:{NOW}") is None


def test_parse_empty_session_returns_none():
    assert rm._parse(f"{REPO}::{NOW}") is None


@pytest.fixture
def state(tmp_path, monkeypatch):
    """Isolated state file, frozen clock, stubbed repo identity."""
    f = tmp_path / "review-loop"
    monkeypatch.setenv("REVIEW_MARKER_STATE_FILE", str(f))
    monkeypatch.setattr(rm, "_now", lambda: float(NOW))
    monkeypatch.setattr(rm, "repo_id", lambda repo_path=None: REPO)
    return f


@pytest.fixture
def tree(tmp_path):
    """A real git repo holding one file, mtime pinned before the record.

    Git-backed because check() asks git to tell a tracked deletion apart from a path that
    simply does not exist; `deleted.py` is committed and then removed so the deletion case
    exercises the same code path production takes. repo_id stays stubbed by `state`.
    """
    d = tmp_path / "tree"
    d.mkdir()
    (d / "a.py").write_text("x\n")
    (d / "deleted.py").write_text("gone\n")
    for cmd in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "T"],
                ["add", "a.py", "deleted.py"], ["commit", "-qm", "init"]):
        subprocess.run(["git", "-C", str(d), *cmd], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (d / "deleted.py").unlink()
    os.utime(d / "a.py", (NOW - 60, NOW - 60))
    return d


def test_record_writes_entry(state, tree):
    assert rm.record(str(tree), "sess-1") == 0
    assert state.read_text() == f"{REPO}:sess-1:{NOW}\n"


def test_record_is_idempotent_per_repo_session(state, tree):
    """Idempotent in what _lookup answers, not in how many lines are on disk.

    record() appends rather than rewriting (see the stale-read test below), so a repeated
    record leaves a duplicate line until compaction collapses it. What must not change is
    the answer: _lookup takes the newest epoch for the (repo, session) pair.
    """
    rm.record(str(tree), "sess-1")
    rm.record(str(tree), "sess-1")
    assert rm._lookup(REPO, "sess-1", float(NOW)) == NOW
    assert {line.rsplit(":", 1)[0] for line in state.read_text().splitlines()} == {f"{REPO}:sess-1"}


def test_record_keeps_other_sessions(state, tree):
    rm.record(str(tree), "sess-1")
    rm.record(str(tree), "sess-2")
    assert sorted(state.read_text().splitlines()) == [
        f"{REPO}:sess-1:{NOW}", f"{REPO}:sess-2:{NOW}"]


def test_check_passes_when_record_covers_file(state, tree):
    rm.record(str(tree), "sess-1")
    assert rm.check(str(tree), "sess-1", ["a.py"]) == 0


def test_check_fails_when_file_changed_after_record(state, tree):
    rm.record(str(tree), "sess-1")
    os.utime(tree / "a.py", (NOW + 60, NOW + 60))
    assert rm.check(str(tree), "sess-1", ["a.py"]) == 1


def test_check_lists_every_file_changed_after_the_record(state, tree, capsys):
    (tree / "b.py").write_text("v1\n")
    rm.record(str(tree), "sess-1")
    os.utime(tree / "a.py", (NOW + 60, NOW + 60))
    os.utime(tree / "b.py", (NOW + 60, NOW + 60))
    assert rm.check(str(tree), "sess-1", ["a.py", "b.py"]) == 1
    out = capsys.readouterr().out
    assert "a.py changed after the last review" in out
    assert "b.py changed after the last review" in out


def test_check_keeps_reporting_after_a_stale_file(state, tree, capsys):
    """A stale file must not hide a later one; the scan runs to the end."""
    (tree / "b.py").write_text("v1\n")
    os.utime(tree / "b.py", (NOW - 60, NOW - 60))
    rm.record(str(tree), "sess-1")
    os.utime(tree / "a.py", (NOW + 60, NOW + 60))
    assert rm.check(str(tree), "sess-1", ["a.py", "b.py"]) == 1
    assert capsys.readouterr().out.count("changed after the last review") == 1


def test_check_fails_closed_immediately_on_an_unusable_list(state, tree, capsys):
    """A missing non-deletion means the list itself is unusable — that still short-circuits."""
    rm.record(str(tree), "sess-1")
    os.utime(tree / "a.py", (NOW + 60, NOW + 60))
    assert rm.check(str(tree), "sess-1", ["ghost.py", "a.py"]) == 1
    out = capsys.readouterr().out
    assert "the file list is unusable" in out
    assert "a.py changed after the last review" not in out


def test_check_fails_without_any_record(state, tree):
    assert rm.check(str(tree), "sess-1", ["a.py"]) == 1


def test_check_fails_on_session_mismatch(state, tree):
    rm.record(str(tree), "sess-1")
    assert rm.check(str(tree), "sess-2", ["a.py"]) == 1


def test_check_skips_deleted_file(state, tree):
    # A commit that removes a file must not fail the check forever: a tracked deletion has
    # no mtime, so it is skipped rather than counted as changed.
    rm.record(str(tree), "sess-1")
    assert rm.check(str(tree), "sess-1", ["deleted.py"]) == 0
    # ...and it does not mask a real edit to a file that IS present.
    os.utime(tree / "a.py", (NOW + 60, NOW + 60))
    assert rm.check(str(tree), "sess-1", ["deleted.py", "a.py"]) == 1


def test_check_fails_when_a_path_is_missing_and_not_a_deletion(state, tree):
    # zsh field-splits an unquoted command substitution, so `--files $(git diff --name-only)`
    # turns a changed `two words.txt` into two arguments. Both miss on disk. Skipping them the
    # way a tracked deletion is skipped left NOTHING compared and returned rc 0 — full
    # coverage reported for a file written after the review (measured 2026-08-04). git can
    # tell the two apart: a mangled fragment is not a tracked deletion.
    rm.record(str(tree), "sess-1")
    assert rm.check(str(tree), "sess-1", ["two", "words.txt"]) == 1


def test_check_passes_for_an_existing_file_beside_a_deletion(state, tree):
    # The guard above must not turn an ordinary covered commit into a reminder.
    rm.record(str(tree), "sess-1")
    assert rm.check(str(tree), "sess-1", ["a.py", "deleted.py"]) == 0


def test_check_skips_a_deletion_staged_with_git_rm(state, tree):
    # `git ls-files --deleted` compares the index against the worktree, so a deletion already
    # staged with `git rm` is absent from it — the path is no longer in the index. Using it
    # would fail a deletion-only commit the moment it was staged, which is exactly the
    # "must not fail forever" case above. `diff --diff-filter=D HEAD` covers both.
    subprocess.run(["git", "-C", str(tree), "rm", "-q", "a.py"], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rm.record(str(tree), "sess-1")
    assert rm.check(str(tree), "sess-1", ["a.py"]) == 0


def test_check_fails_closed_when_deletions_cannot_be_listed(state, tree, monkeypatch):
    # repo_id() returns on its first git call and never checks for a work tree, so it succeeds
    # on a bare repo or a .git dir where the deletion listing exits 128. Treating "cannot say"
    # as "skip" would be a fail-open in a control whose whole job is refusing false coverage —
    # and the OSError branch in check() already fails closed for an equally genuine unknown.
    monkeypatch.setattr(rm, "_tracked_deletions", lambda repo: None)
    rm.record(str(tree), "sess-1")
    assert rm.check(str(tree), "sess-1", ["nope.py"]) == 1


def test_check_handles_a_non_ascii_path(state, tree):
    # `git diff --name-only` C-quotes a non-ASCII path ("caf\303\251.txt", quotes included)
    # while the deletion listing returns it raw, so the two sides must agree on encoding or
    # such a path is never present and never a known deletion — rc 1 forever, and the mtime
    # comparison that is the actual check never runs.
    p = tree / "café.txt"
    p.write_text("x\n")
    for cmd in (["add", "café.txt"], ["commit", "-qm", "add non-ascii"]):
        subprocess.run(["git", "-C", str(tree), *cmd], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.utime(p, (NOW - 60, NOW - 60))
    rm.record(str(tree), "sess-1")
    assert rm.check(str(tree), "sess-1", ["café.txt"]) == 0
    # ...and once committed, deleting it is a deletion git reports raw, so it is recognised
    # rather than read as a mangled argument.
    p.unlink()
    assert rm.check(str(tree), "sess-1", ["café.txt"]) == 0


def test_check_handles_a_path_containing_a_carriage_return(state, tree):
    # A \r is legal in a filename on macOS and Linux, and git -z emits it raw. Decoding the
    # deletion listing in text mode switched on universal-newline translation, which rewrote
    # that \r to \n before the NUL split, so the path was never in the deletion set: check()
    # called it a missing non-deletion and returned rc 1 for good, and no later commit
    # touching this repo could clear it (measured 2026-08-16, git 2.55.0).
    name = "we\rird.txt"
    p = tree / name
    p.write_text("x\n")
    for cmd in (["add", name], ["commit", "-qm", "add carriage-return path"]):
        subprocess.run(["git", "-C", str(tree), *cmd], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.utime(p, (NOW - 60, NOW - 60))
    rm.record(str(tree), "sess-1")
    # Present first: that leg goes through stat() and never reaches the deletion listing, so
    # it pins the argv/filesystem half of the round trip the deletion leg cannot see.
    assert rm.check(str(tree), "sess-1", [name]) == 0
    p.unlink()
    assert rm.check(str(tree), "sess-1", [name]) == 0


def _caller_listing(tree, zero_terminated):
    """A candidate list of the shape the callers build — re-implemented, not executed.

    Same two listings as skills/commit/SKILL.md step 1 (the hook adds a `sort -zu` dedup
    this omits): tracked changes against HEAD plus untracked non-ignored files.
    `zero_terminated=False` reproduces the pre-fix form, which is what makes the assertion
    below discriminating rather than a restatement of check()'s own behaviour.

    Re-implemented means it pins the git-to-_tracked_deletions contract, NOT either
    caller's source: a caller that dropped `-z` would leave this green. The hook is
    guarded by hooks/test_review_loop_warn.sh assertions 12 and 12b, the skill body by
    test_commit_skill_preflight_lists_nul_terminated below.
    """
    flag = ["-z"] if zero_terminated else []
    out = b""
    for cmd in (["diff", "--name-only", *flag, "HEAD"],
                ["ls-files", "--others", "--exclude-standard", *flag]):
        out += subprocess.run(["git", "-C", str(tree), *cmd],
                              capture_output=True, check=True).stdout
    sep = "\0" if zero_terminated else "\n"
    return [p for p in out.decode("utf-8", "surrogateescape").split(sep) if p]


def test_caller_listing_round_trips_a_carriage_return_path(state, tree):
    """The two callers list with -z so their paths spell what _tracked_deletions spells.

    Without -z git C-quotes the path — `"we\\rird.txt"`, quotes included — which is absent
    from disk and absent from the deletion set, so check() reports the list unusable and
    returns rc 1 for this repo forever. Feeding check() a list git actually produced is the
    point: check() alone was already correct once _tracked_deletions stopped decoding in
    text mode, and a test that hand-feeds it the raw name never exercises the listing half
    of the round trip. What guards the callers themselves is named in _caller_listing.
    """
    name = "we\rird.txt"
    p = tree / name
    p.write_text("x\n")
    for cmd in (["add", name], ["commit", "-qm", "add carriage-return path"]):
        subprocess.run(["git", "-C", str(tree), *cmd], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rm.record(str(tree), "sess-1")
    p.unlink()

    listed = _caller_listing(tree, zero_terminated=True)
    assert name in listed
    assert rm.check(str(tree), "sess-1", listed) == 0

    quoted = _caller_listing(tree, zero_terminated=False)
    assert name not in quoted
    assert rm.check(str(tree), "sess-1", quoted) == 1


def test_commit_skill_preflight_lists_nul_terminated():
    """The other caller of check() is a bash block in a skill body — nothing executes it.

    hooks/tests/test_review_loop_warn.py has its own carriage-return-path assertion, which executes it;
    this is the textual counterpart for skills/commit/SKILL.md step 1, worth the strings it
    names and no more. Without it that block could lose `-z` and both suites would stay green
    while the pre-flight returned rc 1 forever for any repo holding a path git C-quotes.
    Anchored on the step-1 heading so it reads the pre-flight block and not the cap-decision
    block further down, which is read by eye and deliberately keeps the non-`-z` form.
    """
    skill = (LIB.parent.parent / "skills" / "commit" / "SKILL.md").read_text()
    # Named before the split: a reworded heading otherwise fails as a bare IndexError, which
    # tells the maintainer nothing about which anchor moved.
    assert "### 1. Review-loop pre-flight" in skill
    body = skill.split("### 1. Review-loop pre-flight", 1)[1]
    block = body.split("```bash", 1)[1].split("```", 1)[0]
    # A textual guard is worth exactly the strings it names: without this one a refactor that
    # moved the call out of the block would keep every flag assertion below green while the
    # pre-flight no longer ran at all.
    # Two substrings, not the one literal `review_marker.py check`: the block now resolves the
    # plugin root into a shell variable first, so the script path is quoted and the closing quote
    # lands between the two words. Splitting keeps this assertion's job — proving the call is
    # still inside this block — without pinning the quoting style.
    assert "review_marker.py" in block
    assert "check --repo" in block
    assert "diff --name-only -z HEAD" in block
    assert "ls-files --others --exclude-standard -z" in block
    assert "read -r -d ''" in block
    # -z suppresses quoting outright, so the flag left beside it would read as still
    # carrying a case of its own.
    assert "core.quotePath" not in block


def test_check_ignores_a_record_for_another_repo(state, tree):
    # Same session id, different repo: the lookup keys on both, so a newer record from
    # elsewhere must not vouch for this tree.
    os.utime(tree / "a.py", (NOW + 60, NOW + 60))
    _write_state([("gitlab.example/team/other", "sess-1", NOW + 120)])
    assert rm.check(str(tree), "sess-1", ["a.py"]) == 1


def test_entries_older_than_prune_window_are_dropped(state, tree):
    _write_state([(REPO, "old", int(NOW - rm.PRUNE_AFTER_SECONDS - 1))])
    assert rm.check(str(tree), "old", ["a.py"]) == 1


def test_check_cli_requires_a_session_id(state, tree):
    # Both callers can supply the harness session id, so there is no session-less mode.
    # Omitting it is an argparse error — rc 2, empty stdout — which the hook surfaces as
    # the reminder instead of as a silent pass.
    rm.record(str(tree), "sess-1")
    with pytest.raises(SystemExit) as exc:
        rm.main(["check", "--repo", str(tree), "--files", "a.py"])
    assert exc.value.code == 2


def test_check_cli_accepts_a_session_id(state, tree):
    rm.record(str(tree), "sess-1")
    assert rm.main(["check", "--repo", str(tree), "--session", "sess-1", "--files", "a.py"]) == 0


def _write_state(entries):
    f = rm._state_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("".join(f"{r}:{s}:{t}\n" for r, s, t in entries), encoding="utf-8")


def _skill_log(tmp_path, monkeypatch, lines):
    """Point HOME at tmp_path and seed the dev-flow skill log there."""
    monkeypatch.setenv("HOME", str(tmp_path))
    state_dir = tmp_path / ".claude" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "dev-flow-skills.log").write_text("".join(lines))


def _log_line(session, skill="simo-dev:commit"):
    return f"2026-08-10T00:00:00Z\t{session}\trepo-a\tmain\t{skill}\n"


def test_probe_no_longer_fires_when_only_other_sessions_logged(state, tree, tmp_path, monkeypatch, capsys):
    """Pins the removal of a permanent false positive, not a behaviour worth having."""
    _skill_log(tmp_path, monkeypatch, [_log_line("other-session")])
    assert rm.check(str(tree), "sess-1", [], expect_skill_log="simo-dev:commit") == 1
    assert "no review record" in capsys.readouterr().out


def test_probe_reports_dead_hooks_when_the_session_logged_other_skills(state, tree, tmp_path, monkeypatch):
    """Mid-session death: earlier skills logged, the calling one did not.

    Measured on ee6461c5 (2026-08-09): task-intake and grill-me logged, then a
    concurrent release removed the cache dir and plan-write logged nothing.
    """
    _skill_log(tmp_path, monkeypatch, [
        _log_line("other-session"),
        _log_line("sess-1", "simo-dev:task-intake"),
        _log_line("sess-1", "simo-dev:grill-me"),
    ])
    assert rm.check(str(tree), "sess-1", [], expect_skill_log="simo-dev:commit") == 3


def test_probe_silent_when_the_calling_skill_logged(state, tree, tmp_path, monkeypatch, capsys):
    _skill_log(tmp_path, monkeypatch, [_log_line("other-session"),
                                       _log_line("sess-1", "simo-dev:commit")])
    assert rm.check(str(tree), "sess-1", [], expect_skill_log="simo-dev:commit") == 1
    assert "no review record" in capsys.readouterr().out


def test_probe_silent_when_log_absent(state, tree, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert rm.check(str(tree), "sess-1", [], expect_skill_log="simo-dev:commit") == 1
    assert "no review record" in capsys.readouterr().out


def test_probe_fires_when_only_this_session_logged_other_skills(state, tree, tmp_path, monkeypatch):
    """A line from THIS session is what proves the logger works — condition 3 is scoped to it."""
    _skill_log(tmp_path, monkeypatch, [_log_line("sess-1", "simo-dev:task-intake")])
    assert rm.check(str(tree), "sess-1", [], expect_skill_log="simo-dev:commit") == 3


def test_probe_silent_when_log_holds_only_legacy_lines(state, tree, tmp_path, monkeypatch):
    """The escape hatch: nothing parseable ever logged, so absence proves nothing."""
    _skill_log(tmp_path, monkeypatch, ["repo-a\tmain\tsimo-dev:commit\n"])
    assert rm.check(str(tree), "sess-1", [], expect_skill_log="simo-dev:commit") == 1


def test_probe_silent_when_log_is_empty(state, tree, tmp_path, monkeypatch):
    _skill_log(tmp_path, monkeypatch, [])
    assert rm.check(str(tree), "sess-1", [], expect_skill_log="simo-dev:commit") == 1


def test_probe_not_reachable_without_the_flag(state, tree, tmp_path, monkeypatch):
    """hooks/review_loop_warn.py must never get rc 3 — it is not a skill."""
    _skill_log(tmp_path, monkeypatch, [_log_line("other-session")])
    assert rm.check(str(tree), "sess-1", []) == 1


def test_expect_skill_log_flag_reaches_check(state, tree, tmp_path, monkeypatch):
    _skill_log(tmp_path, monkeypatch, [_log_line("other-session"),
                                       _log_line("sess-1", "simo-dev:task-intake")])
    assert rm.main(["check", "--repo", str(tree), "--session", "sess-1",
                    "--expect-skill-log", "simo-dev:commit"]) == 3


def test_probe_silent_on_an_empty_session_id(state, tree, tmp_path, monkeypatch, capsys):
    """An unset CLAUDE_CODE_SESSION_ID must keep degrading to rc 1, not hard-stop.

    It matches no log line, so every other condition would hold and the caller would be
    told to restart a session whose hooks may well be alive.
    """
    _skill_log(tmp_path, monkeypatch, [_log_line("other-session")])
    assert rm.check(str(tree), "", [], expect_skill_log="simo-dev:commit") == 1
    assert "no review record" in capsys.readouterr().out


def test_probe_fires_on_the_stale_record_path(state, tree, tmp_path, monkeypatch, capsys):
    """A record older than a changed file is the same dead-hook situation.

    The loop re-runs, the reviewer returns, review_record.py is dead so the epoch never
    advances, and rc 1 would send the caller round again forever.
    """
    _skill_log(tmp_path, monkeypatch, [_log_line("other-session"),
                                       _log_line("sess-1", "simo-dev:task-intake")])
    _write_state([(REPO, "sess-1", int(NOW - 3600))])
    os.utime(tree / "a.py", (NOW, NOW))
    assert rm.check(str(tree), "sess-1", ["a.py"], expect_skill_log="simo-dev:commit") == 3
    out = capsys.readouterr().out
    assert "changed after the last review" in out
    assert "re-run" in out


def test_stale_record_stays_rc1_when_the_calling_skill_logged(state, tree, tmp_path, monkeypatch, capsys):
    _skill_log(tmp_path, monkeypatch, [_log_line("sess-1", "simo-dev:commit")])
    _write_state([(REPO, "sess-1", int(NOW - 3600))])
    os.utime(tree / "a.py", (NOW, NOW))
    assert rm.check(str(tree), "sess-1", ["a.py"], expect_skill_log="simo-dev:commit") == 1
    assert "changed after the last review" in capsys.readouterr().out


def test_stale_record_not_reachable_without_the_flag(state, tree, tmp_path, monkeypatch):
    _skill_log(tmp_path, monkeypatch, [_log_line("other-session")])
    _write_state([(REPO, "sess-1", int(NOW - 3600))])
    os.utime(tree / "a.py", (NOW, NOW))
    assert rm.check(str(tree), "sess-1", ["a.py"]) == 1


def test_flag_order_relative_to_files_does_not_matter(state, tree, tmp_path, monkeypatch):
    """argparse's nargs="*" stops at the next ---prefixed token, so both orders parse alike.

    Pinned because the skill body used to justify its ordering on the opposite claim.
    """
    _skill_log(tmp_path, monkeypatch, [_log_line("other-session"),
                                       _log_line("sess-1", "simo-dev:task-intake")])
    before = rm.main(["check", "--repo", str(tree), "--session", "sess-1",
                      "--expect-skill-log", "simo-dev:commit", "--files", "a.py"])
    after = rm.main(["check", "--repo", str(tree), "--session", "sess-1",
                     "--files", "a.py", "--expect-skill-log", "simo-dev:commit"])
    assert before == after == 3


def test_probe_never_turns_a_passing_check_into_a_failure(state, tree, tmp_path, monkeypatch):
    """rc 0 stays rc 0 with the flag set and a skill log that would otherwise fire rc 3.

    Every other rc-0 assertion in this file omits expect_skill_log, so _dead_hooks_verdict
    short-circuits on the falsy flag and would hold even if the probe were wired into the
    passing path. This one passes the flag and seeds a log meeting all four conditions.
    """
    _skill_log(tmp_path, monkeypatch, [_log_line("other-session"),
                                       _log_line("sess-1", "simo-dev:task-intake")])
    assert rm._hooks_look_dead("sess-1", "simo-dev:commit") is True
    rm.record(str(tree), "sess-1")
    assert rm.check(str(tree), "sess-1", ["a.py"], expect_skill_log="simo-dev:commit") == 0


def test_probe_silent_when_this_session_never_logged(state, tree, tmp_path, monkeypatch, capsys):
    """A session with no lines of its own proves nothing, however busy other sessions were.

    hooks/skill_logger.py drops the line without a word when the payload cwd is not a git
    repository (it exits 0 on an empty --git-common-dir) and when stdin JSON cannot produce a
    skill name. Such a session logs nothing, ever, for any skill — while hooks/review_record.py
    takes its repo from the reviewer message's REPO: marker and keeps working. Keyed on any
    parseable line, the probe called that a dead hook on every single call.
    """
    _skill_log(tmp_path, monkeypatch, [_log_line("other-session"),
                                       _log_line("other-session", "simo-dev:review-loop")])
    assert rm.check(str(tree), "sess-1", [], expect_skill_log="simo-dev:commit") == 1
    assert "no review record" in capsys.readouterr().out


def test_probe_silent_when_this_session_never_logged_on_the_stale_path(
        state, tree, tmp_path, monkeypatch, capsys):
    """Same evidence gap on the other failing path — a record exists and a file is newer."""
    _skill_log(tmp_path, monkeypatch, [_log_line("other-session")])
    _write_state([(REPO, "sess-1", int(NOW - 3600))])
    os.utime(tree / "a.py", (NOW, NOW))
    assert rm.check(str(tree), "sess-1", ["a.py"], expect_skill_log="simo-dev:commit") == 1
    out = capsys.readouterr().out
    assert "changed after the last review" in out
    assert "re-run" not in out


def test_dead_hooks_message_leads_with_the_verifiable_hypothesis(
        state, tree, tmp_path, monkeypatch, capsys):
    """The message must offer the cheap check before the expensive remedy.

    Its old text asserted "a plugin release orphans the hooks of an already-open session".
    That path closed when the four hook entries moved into ~/.claude/settings.json with
    absolute paths into the source tree — docs/plugin-release.md, the paragraph beginning
    "simo-dev's own **hooks** are exempt because their four entries no longer live in the
    plugin". A message naming a cause that can no longer occur sends the reader past the
    one that can.
    """
    _skill_log(tmp_path, monkeypatch, [_log_line("sess-1", "simo-dev:task-intake")])
    assert rm.check(str(tree), "sess-1", [], expect_skill_log="simo-dev:commit") == 3
    out = capsys.readouterr().out
    assert "re-run" in out and "Skill" in out
    assert "plugin release" not in out and "orphan" not in out


@pytest.fixture
def store_writes(state, monkeypatch):
    """Every open mode, and every replace, that targets the state file.

    Two channels because either alone is blind: a rewrite via `open(f,"w")` shows up as a
    truncating mode, a rewrite via a temp file plus a rename never opens the store at all —
    and that second idiom is the one _compact_if_large uses two functions away, so it is
    what a regression would most plausibly look like.

    The rename channel is watched at `os.replace`/`os.rename` AND at `Path.replace`. On
    3.11+ the first two suffice — `Path.replace` resolves `os.replace` at call time, so
    patching the os level catches both spellings plus `shutil.move`. On 3.10, the floor this
    module declares, `Path.replace` goes through `self._accessor.replace`, bound at class
    definition, and the os-level spy never fires: the guard tests then pass vacuously.
    Patching both is version-independent and costs one line.
    """
    opens: list[str] = []
    replaces: list[str] = []
    real_open = Path.open
    real_replace, real_rename = os.replace, os.rename
    real_path_replace = Path.replace

    def spy_open(self, mode="r", *args, **kwargs):
        if self == state:
            opens.append(mode)
        return real_open(self, mode, *args, **kwargs)

    def make_spy(real):
        def spy(src, dst, *args, **kwargs):
            if Path(dst) == state:
                replaces.append(str(src))
            return real(src, dst, *args, **kwargs)
        return spy

    def spy_path_replace(self, target):
        if Path(target) == state:
            replaces.append(str(self))
        return real_path_replace(self, target)

    monkeypatch.setattr(Path, "open", spy_open)
    monkeypatch.setattr(os, "replace", make_spy(real_replace))
    monkeypatch.setattr(os, "rename", make_spy(real_rename))
    monkeypatch.setattr(Path, "replace", spy_path_replace)
    return opens, replaces


def test_record_writes_the_store_by_appending_and_nothing_else(state, store_writes):
    """Pins the mechanism, because the outcome does not distinguish the two implementations.

    "The other session's line survived" passes for a read-modify-write too, as long as its
    read saw that line — which it always does in a sequential test. Both re-derivations of
    the old rewrite were caught here only once the replace channel was watched as well.
    """
    opens, replaces = store_writes
    rm.record("/irrelevant", "sess-A")

    assert opens, "record() never opened the state file"
    # Not "the first open is 'a'": the newline repair reads the tail in "rb" first. What has
    # to hold is that the only WRITE is an append.
    assert any("a" in m for m in opens), f"record() never appended; opens={opens}"
    truncating = [m for m in opens if "w" in m or "x" in m or "+" in m]
    assert not truncating, f"state file opened in a truncating mode: {truncating}"
    assert not replaces, f"state file replaced wholesale: {replaces}"


def test_the_store_write_spy_does_see_a_replace(state, store_writes, monkeypatch):
    """Positive control for the fixture: the assertions above are only worth something if a
    real rewrite would register. Compaction is the one legitimate rewrite, so it is the probe.
    """
    _opens, replaces = store_writes
    monkeypatch.setattr(rm, "_COMPACT_AT_LINES", 1)
    state.write_text("".join(f"{REPO}:sess-A:{NOW - i}\n" for i in range(4)))
    rm.record("/irrelevant", "sess-A")
    assert replaces, "the replace spy never fired even though compaction ran"


def test_record_keeps_a_line_written_after_its_own_read(state):
    """The interleaving itself: the other session's line lands while we are mid-record.

    A sequential test cannot suspend record(), so the line is planted before the call and
    the assertion is that nothing removed it. Weaker than the mechanism test above, and
    kept because it is the failure as an operator saw it.
    """
    state.write_text(f"{REPO}:sess-Z:{NOW}\n")
    rm.record("/irrelevant", "sess-A")
    text = state.read_text()
    assert "sess-Z" in text
    assert "sess-A" in text


def test_compaction_collapses_duplicates_past_the_threshold(state, monkeypatch):
    monkeypatch.setattr(rm, "_COMPACT_AT_LINES", 5)
    # 5 appends stay under the threshold; the 6th crosses it and triggers the rewrite.
    for _ in range(6):
        rm.record("/irrelevant", "sess-A")
    lines = [line for line in state.read_text().splitlines() if line.strip()]
    assert len(lines) == 1
    assert rm._lookup(REPO, "sess-A", float(NOW)) == NOW


def test_compaction_drops_expired_entries(state, monkeypatch):
    state.write_text(f"{REPO}:sess-old:{NOW - rm.PRUNE_AFTER_SECONDS - 1}\n")
    monkeypatch.setattr(rm, "_COMPACT_AT_LINES", 1)
    rm.record("/irrelevant", "sess-A")
    assert "sess-old" not in state.read_text()


def test_compaction_does_not_rewrite_when_it_cannot_shrink_the_file(state, store_writes, monkeypatch):
    """Once the live pairs alone fill the file, rewriting leaves it above the trigger and
    every later record() rewrites too — the hot-path race back for good. The rewrite is
    required to at least halve the file, which amortises it away instead.

    Asserted on the replace channel, not on the resulting line count: with 6 distinct pairs
    a rewrite emits the same 6 lines in the same order, so the content cannot tell a skipped
    compaction from one that ran.
    """
    _opens, replaces = store_writes
    monkeypatch.setattr(rm, "_COMPACT_AT_LINES", 3)
    state.write_text("".join(f"{REPO}:sess-{i}:{NOW}\n" for i in range(5)))
    rm.record("/irrelevant", "sess-new")
    assert not replaces, "compaction rewrote the store although it could not shrink it"
    assert len([line for line in state.read_text().splitlines() if line.strip()]) == 6


def test_compaction_survives_a_failing_replace(state, monkeypatch):
    """The record is already on disk when compaction runs; a cold-path OSError must not
    surface as a caller-visible failure, and must not leave the temp file behind.

    The failure is injected on `replace`, not on `write_text`: replacing write_text means
    the temp file is never created, and then the cleanup this test exists for is a no-op
    that could be deleted with every assertion still green.
    """
    monkeypatch.setattr(rm, "_COMPACT_AT_LINES", 1)
    state.write_text("".join(f"{REPO}:sess-A:{NOW - i}\n" for i in range(4)))

    def boom(self, target):
        raise OSError("cross-device link")

    monkeypatch.setattr(Path, "replace", boom)
    assert rm.record("/irrelevant", "sess-A") == 0
    assert not list(state.parent.glob("*.tmp.*")), "compaction left a temp file behind"


def test_compaction_survives_a_failing_cleanup(state, monkeypatch):
    """The cleanup after a failed rewrite must not itself surface as a caller failure.

    Reached only when both the replace and the unlink fail — EACCES or EROFS on the state
    directory hits both. Without the suppression this raises out of record(), which is the
    same caller-visible failure the surrounding handler exists to prevent.
    """
    monkeypatch.setattr(rm, "_COMPACT_AT_LINES", 1)
    state.write_text("".join(f"{REPO}:sess-A:{NOW - i}\n" for i in range(4)))

    def boom_replace(self, target):
        raise OSError("read-only file system")

    def boom_unlink(self, missing_ok=False):
        raise OSError("read-only file system")

    monkeypatch.setattr(Path, "replace", boom_replace)
    monkeypatch.setattr(Path, "unlink", boom_unlink)
    assert rm.record("/irrelevant", "sess-A") == 0


def test_compaction_keeps_the_newest_epoch_per_pair(state, monkeypatch):
    """After compaction _lookup must still answer with the latest review, not the first.

    Keeping the older epoch would make check() report "changed after the last review" for
    every file touched between the two, so a false reminder on every later commit. The
    other compaction tests run at a frozen clock, where every epoch is equal and the
    selection cannot be observed at all.
    """
    monkeypatch.setattr(rm, "_COMPACT_AT_LINES", 2)
    state.write_text("".join(f"{REPO}:sess-A:{NOW - 500 + i}\n" for i in range(3)))
    rm.record("/irrelevant", "sess-A")
    assert [line for line in state.read_text().splitlines() if line.strip()] == [
        f"{REPO}:sess-A:{NOW}"]
    assert rm._lookup(REPO, "sess-A", float(NOW)) == NOW


def test_compaction_does_not_run_below_the_default_threshold(state, store_writes):
    """Pins the threshold guard at its production value.

    Every other compaction test lowers _COMPACT_AT_LINES via monkeypatch, so none of them
    exercises the default — and without the guard a single live pair rewrites the store on
    nearly every record, which is the hot-path read-modify-write this change removed.
    """
    _opens, replaces = store_writes
    for _ in range(20):
        rm.record("/irrelevant", "sess-A")
    assert not replaces, f"compaction rewrote the store {len(replaces)} times below the threshold"


def _fail_nth_text_read(state, monkeypatch, n, exc):
    """Make the nth text-mode read of the store raise. Binary reads are left alone — the
    newline repair uses one and runs before compaction.
    """
    calls = {"n": 0}
    real_open = Path.open

    def flaky(self, mode="r", *args, **kwargs):
        if self == state and "r" in mode and "b" not in mode:
            calls["n"] += 1
            if calls["n"] == n:
                raise exc
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", flaky)


def test_compaction_gives_up_when_the_store_cannot_be_read(state, monkeypatch):
    """The record is already appended; a store that vanishes before the line count must not
    turn into a caller-visible failure.
    """
    monkeypatch.setattr(rm, "_COMPACT_AT_LINES", 1)
    _fail_nth_text_read(state, monkeypatch, 1, OSError("vanished"))
    assert rm.record("/irrelevant", "sess-A") == 0


def test_compaction_gives_up_when_the_entries_cannot_be_read(state, monkeypatch):
    """The second read — the one _live_entries does — used to sit outside the guard, so its
    failure escaped record() with the line already on disk. Failing read #1 does not reach
    it: that one is the line count, which was always caught.
    """
    monkeypatch.setattr(rm, "_COMPACT_AT_LINES", 1)
    state.write_text("".join(f"{REPO}:sess-A:{NOW - i}\n" for i in range(4)))
    _fail_nth_text_read(state, monkeypatch, 2, OSError("vanished mid-compaction"))
    assert rm.record("/irrelevant", "sess-A") == 0


def test_an_undecodable_store_heals_instead_of_wedging(state, monkeypatch):
    """Strict decoding made an undecodable store permanent: check() raised on every call and
    compaction — the only thing that could rewrite the file clean — bailed before reaching
    it. Reading with surrogateescape lets the garbage fail _parse and drop out, so the next
    compaction removes it and the mechanism recovers on its own.
    """
    monkeypatch.setattr(rm, "_COMPACT_AT_LINES", 1)
    state.write_bytes(b"\xff\xfe not utf-8\n" * 4)
    assert rm.record("/irrelevant", "sess-A") == 0
    assert rm._lookup(REPO, "sess-A", float(NOW)) == NOW
    assert state.read_text() == f"{REPO}:sess-A:{NOW}\n"


def test_record_repairs_a_store_whose_last_line_lost_its_newline(state):
    """A write cut short mid-line leaves a tail; appending onto it splices the two into one
    entry whose repo-id is the concatenation, and _lookup then misses the session that just
    recorded — the same failure this whole change removes. The rewrite this replaced
    discarded such a tail for free.
    """
    state.write_text(f"{REPO}:sess-Z:{NOW}")  # no trailing newline
    rm.record("/irrelevant", "sess-A")
    assert rm._lookup(REPO, "sess-A", float(NOW)) == NOW
    assert rm._lookup(REPO, "sess-Z", float(NOW)) == NOW


def test_check_survives_an_undecodable_store(state, tree):
    """The caller-facing half: under strict decoding check() raised UnicodeDecodeError on
    every call (measured on 3.10.20 and 3.14.6). It must answer instead.
    """
    state.write_bytes(b"\xff\xfe not utf-8\n")
    assert rm.check(str(tree), "sess-1", ["a.py"]) == 1


def test_compaction_survives_garbage_that_parses(state, monkeypatch):
    """Garbage the reader accepts is the case the lenient read created.

    b"\\xff\\xfe not utf-8" carries no colon, so it fails _parse and never reaches the write.
    Three colon-separated fields with a numeric tail do reach it, carrying surrogates from
    the lenient decode, and a strict encode there raises UnicodeEncodeError.

    Asserted on the store being HEALED, not on record() returning cleanly: the ValueError
    guard swallows that encode failure either way, so "no crash, no temp file" holds with
    the strict write too. What only the lenient write buys is compaction actually completing
    — the strict one gives up and leaves the garbage on disk for good.
    """
    monkeypatch.setattr(rm, "_COMPACT_AT_LINES", 1)
    state.write_bytes(b"".join(b"\xff\xfe:sess-B:%d\n" % (NOW - i) for i in range(4)))
    assert rm.record("/irrelevant", "sess-A") == 0
    assert not list(state.parent.glob("*.tmp.*")), "compaction left a temp file behind"
    assert rm._lookup(REPO, "sess-A", float(NOW)) == NOW
    lines = state.read_text(errors="surrogateescape").splitlines()
    assert len(lines) == 2, f"compaction did not rewrite the store: {lines!r}"


MANIFEST_REL = ".claude-plugin/plugin.json"

MANIFEST_TEXT = """{
  "$schema": "https://anthropic.com/claude-code/plugin.schema.json",
  "name": "simo-dev",
  "version": "0.1.70",
  "description": "probe",
  "skills": "./skills/"
}
"""


@pytest.fixture
def manifest_tree(tree):
    """`tree` plus a committed plugin manifest, mtime pinned before the record.

    Built on `tree` so the git repo, the identity stub and the frozen clock stay shared.
    The manifest is committed clean; each test dirties it the way it wants and then
    pushes its mtime past NOW, which is what makes check() reach the stale branch.
    """
    m = tree / MANIFEST_REL
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_text(MANIFEST_TEXT)
    for cmd in (["add", MANIFEST_REL], ["commit", "-qm", "manifest"]):
        subprocess.run(["git", "-C", str(tree), *cmd], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.utime(m, (NOW - 60, NOW - 60))
    return tree


def _dirty(tree, text):
    """Rewrite the manifest and stamp it as written after the review."""
    m = tree / MANIFEST_REL
    m.write_text(text)
    os.utime(m, (NOW + 60, NOW + 60))
    return m


def test_check_skips_a_manifest_whose_only_change_is_the_version(state, manifest_tree):
    # The release script rewrites this one line AFTER the review-loop is clean
    # (docs/plugin-release.md), so the bump is newer than the record by construction.
    # Counting it as unreviewed work sent the flow back through review-loop, and any
    # finding there forced a second release: 0.1.68, 0.1.69 and 0.1.70 were burned that
    # way on 2026-08-12 and none of them reached a commit.
    rm.record(str(manifest_tree), "sess-1")
    _dirty(manifest_tree, MANIFEST_TEXT.replace('"0.1.70"', '"0.1.71"'))
    assert rm.check(str(manifest_tree), "sess-1", [MANIFEST_REL]) == 0


def test_check_flags_a_manifest_that_changed_more_than_the_version(state, manifest_tree):
    # The escape hatch. Both manifest gotchas in docs/plugin-release.md ("Do not
    # reference the standard hooks file", "Do not declare agents as a directory
    # string") are single-field edits that broke plugin loading, so a manifest
    # carrying anything besides the version line must still reach a reviewer.
    rm.record(str(manifest_tree), "sess-1")
    _dirty(manifest_tree, MANIFEST_TEXT
           .replace('"0.1.70"', '"0.1.71"')
           .replace('"description": "probe"', '"description": "changed"'))
    assert rm.check(str(manifest_tree), "sess-1", [MANIFEST_REL]) == 1


def test_check_flags_a_version_edit_outside_a_claude_plugin_dir(state, manifest_tree):
    # Only a plugin manifest gets the exemption. A `plugin.json` anywhere else is an
    # ordinary file whose "version" line nothing machine-writes.
    other = manifest_tree / "plugin.json"
    other.write_text(MANIFEST_TEXT)
    for cmd in (["add", "plugin.json"], ["commit", "-qm", "other"]):
        subprocess.run(["git", "-C", str(manifest_tree), *cmd], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rm.record(str(manifest_tree), "sess-1")
    other.write_text(MANIFEST_TEXT.replace('"0.1.70"', '"0.1.71"'))
    os.utime(other, (NOW + 60, NOW + 60))
    assert rm.check(str(manifest_tree), "sess-1", ["plugin.json"]) == 1


def test_check_flags_an_untracked_manifest(state, manifest_tree):
    # A brand-new manifest has no diff against HEAD, so the helper sees no version
    # line and fails closed. A first release of a new plugin is a real review target.
    rm.record(str(manifest_tree), "sess-1")
    new = manifest_tree / "other-plugin" / ".claude-plugin" / "plugin.json"
    new.parent.mkdir(parents=True)
    new.write_text(MANIFEST_TEXT)
    os.utime(new, (NOW + 60, NOW + 60))
    rel = "other-plugin/.claude-plugin/plugin.json"
    assert rm.check(str(manifest_tree), "sess-1", [rel]) == 1


def test_check_flags_the_manifest_when_git_cannot_answer(state, manifest_tree, monkeypatch):
    # Fail closed on an unknown, same contract as _tracked_deletions returning None:
    # a git that cannot describe the diff is not evidence that the diff is harmless.
    rm.record(str(manifest_tree), "sess-1")
    _dirty(manifest_tree, MANIFEST_TEXT.replace('"0.1.70"', '"0.1.71"'))
    monkeypatch.setattr(rm.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no git")))
    assert rm.check(str(manifest_tree), "sess-1", [MANIFEST_REL]) == 1


def test_check_flags_a_compact_manifest_smuggling_a_field_beside_the_version(state, manifest_tree):
    # The bypass a substring test let through. One physical line carrying several fields is a
    # single replacement whose both sides contain `"version":`, so `'"version":' in ln` passed
    # it and admitted the `"hooks"` key that docs/plugin-release.md documents as breaking
    # plugin loading. The line anchor in _VERSION_LINE_RE is what rejects it.
    compact = '{"name":"p","version":"0.1.0","skills":"./skills/"}\n'
    m = manifest_tree / MANIFEST_REL
    m.write_text(compact)
    for cmd in (["add", MANIFEST_REL], ["commit", "-qm", "compact"]):
        subprocess.run(["git", "-C", str(manifest_tree), *cmd], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rm.record(str(manifest_tree), "sess-1")
    _dirty(manifest_tree,
           '{"name":"p","version":"0.1.1","skills":"./skills/","hooks":"./hooks/hooks.json"}\n')
    assert rm.check(str(manifest_tree), "sess-1", [MANIFEST_REL]) == 1


def test_check_flags_a_version_value_the_release_script_could_not_write(state, manifest_tree):
    # plugin-release.py validates --version against ^\d+\.\d+\.\d+$, so a non-semver value was
    # typed by a human. Nothing else validates it before the commit, which is why it has to
    # stay reviewable rather than ride the release exemption.
    rm.record(str(manifest_tree), "sess-1")
    _dirty(manifest_tree, MANIFEST_TEXT.replace('"0.1.70"', '"0.2"'))
    assert rm.check(str(manifest_tree), "sess-1", [MANIFEST_REL]) == 1


def test_check_skips_the_bump_even_when_git_is_configured_to_colorize(state, manifest_tree):
    # color.ui=always emits ANSI escapes on a pipe too, so every content line stops starting
    # with '-'/'+' and the helper silently answered False for every manifest — the fix
    # disabling itself with no error. --no-color is what keeps this measurable.
    subprocess.run(["git", "-C", str(manifest_tree), "config", "color.ui", "always"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rm.record(str(manifest_tree), "sess-1")
    _dirty(manifest_tree, MANIFEST_TEXT.replace('"0.1.70"', '"0.1.71"'))
    assert rm.check(str(manifest_tree), "sess-1", [MANIFEST_REL]) == 0


@pytest.mark.parametrize("sep", [" ", " ", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\r"])  # first two are U+2028, U+2029
def test_check_flags_a_field_smuggled_behind_a_non_newline_separator(state, manifest_tree, sep):
    # Two layers underneath the anchor, each measured to admit this on its own: str.splitlines()
    # breaks on the first eight — its whole non-\n, non-\r boundary set on CPython 3.14.6 — and
    # `text=True` on subprocess.run rewrites a lone \r to \n before any split runs. Git treats
    # none of the nine as a line terminator, so each cut the smuggled field off before the anchor
    # saw it and the pair came back exempt — the bypass the anchor exists to close, reopened by
    # the parsing beneath it. `\r\n` is deliberately NOT in this list: git ends the line on its
    # \n and emits three changed lines, which `len(changed) != 2` rejects under every variant, so
    # the case would pass no matter what and measure nothing.
    rm.record(str(manifest_tree), "sess-1")
    _dirty(manifest_tree, MANIFEST_TEXT.replace(
        '  "version": "0.1.70",\n',
        f'  "version": "0.1.71",{sep}  "hooks": "./hooks/hooks.json",\n'))
    assert rm.check(str(manifest_tree), "sess-1", [MANIFEST_REL]) == 1


def test_check_skips_the_bump_under_a_json_textconv_driver(state, manifest_tree, tmp_path):
    # A `diff=<driver>` textconv on *.json flattens the manifest, and the flattened line fails
    # the anchor — so without --no-textconv the helper answers False for every manifest and the
    # loop comes back, silently. Guarded here for the same reason as the color.ui case.
    flatten = tmp_path / "flatten.sh"
    flatten.write_text("#!/bin/sh\ntr '\\n' ' ' < \"$1\"\n")
    flatten.chmod(0o755)
    (manifest_tree / ".gitattributes").write_text("*.json diff=squash\n")
    subprocess.run(["git", "-C", str(manifest_tree), "config", "diff.squash.textconv",
                    str(flatten)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rm.record(str(manifest_tree), "sess-1")
    _dirty(manifest_tree, MANIFEST_TEXT.replace('"0.1.70"', '"0.1.71"'))
    assert rm.check(str(manifest_tree), "sess-1", [MANIFEST_REL]) == 0


def test_check_skips_the_bump_under_an_external_diff_driver(state, manifest_tree, monkeypatch):
    # GIT_EXTERNAL_DIFF replaces the diff output wholesale, and the helper inherits the
    # caller's environment. Without --no-ext-diff there are no content lines to parse at all.
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", "/usr/bin/true")
    rm.record(str(manifest_tree), "sess-1")
    _dirty(manifest_tree, MANIFEST_TEXT.replace('"0.1.70"', '"0.1.71"'))
    assert rm.check(str(manifest_tree), "sess-1", [MANIFEST_REL]) == 0


def test_check_flags_a_reformat_of_the_version_line_that_keeps_the_value(state, manifest_tree):
    # Guards `before.group(1) != after.group(1)`. A release always changes the value, so a line
    # that was rewritten while keeping it was not written by the release script — trailing
    # comma dropped here, but respacing and requoting land the same way.
    rm.record(str(manifest_tree), "sess-1")
    _dirty(manifest_tree, MANIFEST_TEXT.replace('  "version": "0.1.70",\n',
                                                '  "version":   "0.1.70"\n'))
    assert rm.check(str(manifest_tree), "sess-1", [MANIFEST_REL]) == 1


def test_check_flags_a_non_version_line_replaced_by_a_version_line(state, manifest_tree):
    # Guards the anchor on the `-` side. Only the `+` line being a version field is not enough:
    # the removed line has to be one too, or an arbitrary field deleted in the same one-line
    # replacement rides out of the manifest unreviewed.
    rm.record(str(manifest_tree), "sess-1")
    _dirty(manifest_tree, MANIFEST_TEXT.replace('  "description": "probe",\n',
                                                '  "version": "0.1.71",\n'))
    assert rm.check(str(manifest_tree), "sess-1", [MANIFEST_REL]) == 1


def test_check_flags_a_bump_whose_hunk_lists_the_added_line_first(state, manifest_tree):
    # Guards the changed[0]/changed[1] order clause, the last one no test killed. Moving the
    # version field upward makes git emit the `+` before the `-` — still two changed lines, so
    # only the order check stands between that hunk and the exemption. The harm blocked is
    # narrow (JSON key order is inert), but the guard is reachable, not dead code.
    rm.record(str(manifest_tree), "sess-1")
    _dirty(manifest_tree, MANIFEST_TEXT
           .replace('  "version": "0.1.70",\n', "")
           .replace('  "name": "simo-dev",\n', '  "version": "9.9.9",\n  "name": "simo-dev",\n'))
    assert rm.check(str(manifest_tree), "sess-1", [MANIFEST_REL]) == 1


def test_check_still_flags_a_stale_sibling_beside_a_version_only_bump(state, manifest_tree):
    # The exemption is per file, not a pass for the commit. A release that lands while
    # other work is still unreviewed must not certify that work.
    rm.record(str(manifest_tree), "sess-1")
    _dirty(manifest_tree, MANIFEST_TEXT.replace('"0.1.70"', '"0.1.71"'))
    os.utime(manifest_tree / "a.py", (NOW + 60, NOW + 60))
    assert rm.check(str(manifest_tree), "sess-1", [MANIFEST_REL, "a.py"]) == 1


def _cp1252_marker_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x\n")
    for cmd in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "T"],
                ["add", "a.py"], ["commit", "-qm", "init"]):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return repo


def _cp1252_check(tmp_path, missing):
    from cp1252_run import run_cp1252
    repo = _cp1252_marker_repo(tmp_path)
    env = {"REVIEW_MARKER_STATE_FILE": str(tmp_path / "state")}
    script = Path(__file__).parent.parent / "review_marker.py"
    rec = run_cp1252(script, ["record", "--repo", str(repo), "--session", "s1"], extra_env=env)
    assert rec.returncode == 0, rec.stderr.decode("utf-8", "replace")
    return run_cp1252(script, ["check", "--repo", str(repo), "--session", "s1", "--files", missing], extra_env=env)


def test_check_prints_non_ascii_path_on_cp1252_stdout(tmp_path):
    result = _cp1252_check(tmp_path, "→★.py")
    assert result.returncode == 1, result.stderr.decode("utf-8", "replace")
    assert "→★.py is missing" in result.stdout.decode("utf-8")


def test_check_prints_undecodable_path_on_cp1252_stdout(tmp_path):
    result = _cp1252_check(tmp_path, "bad\udcffname.py")
    assert result.returncode == 1, result.stderr.decode("utf-8", "replace")
    assert b"bad\xffname.py is missing" in result.stdout


def test_record_and_check_non_ascii_repo_id_on_cp1252(tmp_path):
    from cp1252_run import run_cp1252
    repo = _cp1252_marker_repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "git@gitlab.example:tëam/→★.git"], check=True)
    os.utime(repo / "a.py", (1_000_000_000, 1_000_000_000))
    state = tmp_path / "state"
    env = {"REVIEW_MARKER_STATE_FILE": str(state)}
    script = Path(__file__).parent.parent / "review_marker.py"
    rec = run_cp1252(script, ["record", "--repo", str(repo), "--session", "s1"], extra_env=env)
    assert rec.returncode == 0, rec.stderr.decode("utf-8", "replace")
    assert rec.stdout.decode("utf-8") == "review-record: gitlab.example/tëam/→★:s1\n"
    assert state.read_bytes().startswith("gitlab.example/tëam/→★:s1:".encode("utf-8"))
    chk = run_cp1252(script, ["check", "--repo", str(repo), "--session", "s1", "--files", "a.py"], extra_env=env)
    assert chk.returncode == 0, chk.stdout.decode("utf-8", "replace") + chk.stderr.decode("utf-8", "replace")


def test_record_remoteless_non_ascii_repo_path_on_cp1252(tmp_path):
    from cp1252_run import run_cp1252
    repo = tmp_path / "rëpo→"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    state = tmp_path / "state"
    script = Path(__file__).parent.parent / "review_marker.py"
    rec = run_cp1252(script, ["record", "--repo", str(repo), "--session", "s1"],
                     extra_env={"REVIEW_MARKER_STATE_FILE": str(state)})
    assert rec.returncode == 0, rec.stderr.decode("utf-8", "replace")
    rid = str(repo.resolve())
    assert rec.stdout.decode("utf-8") == f"review-record: {rid}:s1\n"
    assert state.read_bytes().startswith(f"{rid}:s1:".encode("utf-8"))


def test_check_reads_utf8_skill_log_on_cp1252(tmp_path):
    from cp1252_run import run_cp1252
    repo = _cp1252_marker_repo(tmp_path)
    home = tmp_path / "home"
    (home / ".claude" / "state").mkdir(parents=True)
    (home / ".claude" / "state" / "dev-flow-skills.log").write_bytes(
        "2026-08-10T00:00:00Z\ts1\trepo-a\tfeat/ü→\tsimo-dev:task-intake\n".encode("utf-8"))
    env = {"REVIEW_MARKER_STATE_FILE": str(tmp_path / "state"), "HOME": str(home)}
    script = Path(__file__).parent.parent / "review_marker.py"
    chk = run_cp1252(script, ["check", "--repo", str(repo), "--session", "s1", "--files", "a.py",
                              "--expect-skill-log", "simo-dev:commit"], extra_env=env)
    assert chk.returncode == 3, chk.stdout.decode("utf-8", "replace") + chk.stderr.decode("utf-8", "replace")


def test_record_compacts_non_ascii_store_on_cp1252(tmp_path):
    import time
    from cp1252_run import run_cp1252
    repo = _cp1252_marker_repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "git@gitlab.example:tëam/→★.git"], check=True)
    state = tmp_path / "state"
    entry = f"gitlab.example/tëam/→★:s1:{int(time.time())}\n"
    state.write_bytes((entry * (rm._COMPACT_AT_LINES + 1)).encode("utf-8"))
    script = Path(__file__).parent.parent / "review_marker.py"
    rec = run_cp1252(script, ["record", "--repo", str(repo), "--session", "s1"],
                     extra_env={"REVIEW_MARKER_STATE_FILE": str(state)})
    assert rec.returncode == 0, rec.stderr.decode("utf-8", "replace")
    lines = state.read_bytes().decode("utf-8").splitlines()
    assert len(lines) == 1 and lines[0].startswith("gitlab.example/tëam/→★:s1:")
