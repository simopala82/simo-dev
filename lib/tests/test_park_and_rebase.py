"""Tests for lib/park_and_rebase.py (stale-base rc 1 recovery)."""
import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

LIB = Path(__file__).parent.parent / "park_and_rebase.py"

spec = importlib.util.spec_from_file_location("park_and_rebase", LIB)
par = importlib.util.module_from_spec(spec)
spec.loader.exec_module(par)


def rec(commits=("a1",), staged=(), base="master", expected_drop=0, token="tok0"):
    return {"pre_head": "a1", "base": base, "commits": list(commits), "staged": list(staged),
            "expected_drop": expected_drop, "token": token}


# --- refusals: the two path classes `git add -A` cannot carry -------------------------

def test_refusals_none():
    assert par.refusals({"a.py"}, {"cache/x"}, set()) == []


def test_refusals_ignored_path_the_base_adds():
    out = par.refusals({"secrets.env", "a.py"}, {"secrets.env"}, set())
    assert len(out) == 1
    assert "secrets.env" in out[0] and "gitignored" in out[0]


def test_refusals_skip_worktree_path_the_base_touches():
    out = par.refusals({"conf.yml"}, set(), {"conf.yml"})
    assert len(out) == 1
    assert "skip-worktree" in out[0] and "conf.yml" in out[0]


def test_refusals_both_classes_reported_together():
    assert len(par.refusals({"s.env", "c.yml"}, {"s.env"}, {"c.yml"})) == 2


def test_refusals_ignore_paths_the_base_does_not_touch():
    # a gitignored file the base never adds is not at risk -- the rebase does not write it
    assert par.refusals({"a.py"}, {"local.env"}, {"pinned.yml"}) == []


# --- restore_verdict: the guard that keeps `reset HEAD~1` off a real commit -----------

def test_restore_refuses_mid_rebase():
    rc, msg = par.restore_verdict(rec(), "rebase-merge", "", None)
    assert rc == 2 and "rebase-merge" in msg


def test_restore_refuses_without_record():
    # no park happened, so HEAD~1 is a real commit
    rc, msg = par.restore_verdict(None, None, par.PARK_SUBJECT, 1)
    assert rc == 2 and "real commit" in msg


def test_restore_refuses_on_corrupt_record():
    # a present-but-unparseable record must not read as "nothing was parked"
    rc, msg = par.restore_verdict(par.CORRUPT, None, par.PARK_SUBJECT, 1)
    assert rc == 2 and "unusable" in msg and "reset HEAD~1" in msg


def test_restore_refuses_when_the_park_itself_was_skipped():
    rc, msg = par.restore_verdict(rec(), None, "feat: something real", 1)
    assert rc == 2 and "reflog" in msg


def test_restore_refuses_when_a_branch_commit_was_dropped():
    # `git rebase --skip` on a BRANCH commit leaves the park at HEAD, so the subject
    # check passes and only the commit count catches it
    rc, msg = par.restore_verdict(rec(commits=("a1", "a2")), None, par.PARK_SUBJECT, 1)
    assert rc == 2
    assert "should carry 2 commit(s)" in msg
    assert "--allow-dropped" in msg


def test_count_check_tolerates_the_drops_git_predicted():
    # squash-merged / cherry-picked branch: `git cherry` marked one commit as already
    # upstream before the park, so losing exactly that one is not a --skip
    assert par.restore_verdict(rec(commits=("a1", "a2"), expected_drop=1),
                               None, par.PARK_SUBJECT, 1) == (0, "")


def test_count_check_still_fires_past_the_predicted_drops():
    rc, _ = par.restore_verdict(rec(commits=("a1", "a2"), expected_drop=1), None, par.PARK_SUBJECT, 0)
    assert rc == 2


def test_allow_dropped_is_the_escape_hatch():
    assert par.restore_verdict(rec(commits=("a1", "a2")), None, par.PARK_SUBJECT, 1, True) == (0, "")


def test_restore_refuses_when_the_count_is_unknown():
    rc, msg = par.restore_verdict(rec(), None, par.PARK_SUBJECT, None)
    assert rc == 2 and "cannot count" in msg


def test_restore_allows_an_intact_branch():
    assert par.restore_verdict(rec(), None, par.PARK_SUBJECT, 1) == (0, "")


def test_restore_allows_a_branch_that_gained_commits():
    # a rebase that splits or adds is not the drop this guard is about
    assert par.restore_verdict(rec(commits=("a1",)), None, par.PARK_SUBJECT, 3) == (0, "")


# --- parsing ---------------------------------------------------------------------------

def test_hidden_from_add_skips_paths_absent_from_disk(tmp_path, monkeypatch):
    # sparse checkout: every excluded path is skip-worktree but not on disk, and the
    # rebase writes it without conflict -- refusing on it blocks a safe park
    (tmp_path / "present.yml").write_text("x")
    monkeypatch.setattr(
        par, "_git",
        lambda repo, args: "H tracked.py\nS present.yml\nS sparse/absent.yml\nh gone.txt\n",
    )
    assert par._hidden_from_add(str(tmp_path)) == {"present.yml"}


def test_read_record_rejects_a_partial_dict(tmp_path, monkeypatch):
    monkeypatch.setattr(par, "git_dir", lambda repo: tmp_path)
    (tmp_path / par.RECORD_NAME).write_text('{"pre_head": "abc"}')
    assert par.read_record("/x") is par.CORRUPT


@pytest.mark.parametrize("field,bad", [
    # `commits`: len() is applied to it. int/float/bool/None raise; str counts characters;
    # dict is len()-able and empty, which is the fail-open case (expected becomes 0).
    ("commits", "3"), ("commits", "3.4"), ("commits", "true"), ("commits", "null"),
    ("commits", '"a1"'), ("commits", "{}"),
    # `expected_drop`: subtracted from len(commits). str/None/list/dict raise; float makes
    # `expected` a float and the comparison silently non-integral; bool IS an int to
    # isinstance and shrinks `expected` by one, masking exactly one dropped commit.
    ("expected_drop", '"z"'), ("expected_drop", "null"), ("expected_drop", "[]"),
    ("expected_drop", "{}"), ("expected_drop", "3.4"), ("expected_drop", "true"),
])
def test_read_record_rejects_a_field_of_the_wrong_type(tmp_path, monkeypatch, capsys, field, bad):
    """The record is a file on disk a human can edit, and presence != usability.

    `json.loads` produces exactly seven Python types (NoneType, bool, dict, float, int,
    list, str), so the rows below are closed by grammar rather than sampled. They also
    discriminate this guard from a `try/except TypeError` around the arithmetic in
    `restore_verdict`, which leaves every row here returning a dict. The stderr assertion
    kills a second weaker form: giving up silently.

    What these rows do NOT pin is the bool exclusion. `commits` is `[]` here, so
    `0 <= True <= 0` is false and the range check rejects the `expected_drop`/`true` row
    first -- deleting the bool clause left the whole suite green (measured 2026-08-22, in
    review). `test_read_record_rejects_an_expected_drop_that_is_a_bool` is the row that
    discriminates it.
    """
    monkeypatch.setattr(par, "git_dir", lambda repo: tmp_path)
    fields = {"pre_head": '"x"', "base": '"master"', "commits": "[]", "staged": "[]",
              "expected_drop": "0", "token": '"tok0"'}
    fields[field] = bad
    (tmp_path / par.RECORD_NAME).write_text(
        "{" + ",".join(f'"{k}":{v}' for k, v in fields.items()) + "}")
    assert par.read_record("/x") is par.CORRUPT
    assert field in capsys.readouterr().err


@pytest.mark.parametrize("drop", ["4", "99", "-1", "-2"])
def test_read_record_rejects_an_expected_drop_outside_its_range(tmp_path, monkeypatch, capsys, drop):
    """The last type- or range-driven route to a drop guard that is off, and it needs no bad type.

    `_snapshot` counts `expected_drop` off the same commit list, so `0 <= expected_drop <=
    len(commits)` holds by construction. Above the top, `expected` goes negative and
    `kept < expected` is false for every count -- rc 0, guard off, which is the exact
    failure the type check was justified by. Below zero it inflates `expected` and refuses
    with a number that was never true (both measured 2026-08-22).
    """
    monkeypatch.setattr(par, "git_dir", lambda repo: tmp_path)
    (tmp_path / par.RECORD_NAME).write_text(
        '{"pre_head":"x","base":"master","commits":["a1","a2","a3"],"staged":[],'
        f'"expected_drop":{drop},"token":"tok0"}}')
    assert par.read_record("/x") is par.CORRUPT
    assert "expected_drop" in capsys.readouterr().err


@pytest.mark.parametrize("drop", ["0", "1", "3"])
def test_read_record_accepts_expected_drop_at_both_ends_of_its_range(tmp_path, monkeypatch, drop):
    # both ends are reachable: 0 when `git cherry` marked nothing upstream, len(commits)
    # when it marked everything. A `<` in place of either `<=` rejects a record park wrote.
    monkeypatch.setattr(par, "git_dir", lambda repo: tmp_path)
    (tmp_path / par.RECORD_NAME).write_text(
        '{"pre_head":"x","base":"master","commits":["a1","a2","a3"],"staged":[],'
        f'"expected_drop":{drop},"token":"tok0"}}')
    assert isinstance(par.read_record("/x"), dict)


@pytest.mark.parametrize("drop", ["true", "false"])
def test_read_record_rejects_an_expected_drop_that_is_a_bool(tmp_path, monkeypatch, capsys, drop):
    """`bool` is an `int` to isinstance, and the range check cannot stand in for this.

    `commits` has to be non-empty for this to discriminate: with `[]` the range check gets
    there first (`0 <= True <= 0` is false), which is how the clause ended up with no
    coverage at all in the first round. With three commits the range accepts `true`, and
    only the bool clause is left -- `expected` would become `3 - True == 2`, masking
    exactly one dropped commit at rc 0. `false` subtracts nothing and is harmless, but it
    is still the wrong type, and without the clause it is accepted (measured 2026-08-22).
    """
    monkeypatch.setattr(par, "git_dir", lambda repo: tmp_path)
    (tmp_path / par.RECORD_NAME).write_text(
        '{"pre_head":"x","base":"master","commits":["a1","a2","a3"],"staged":[],'
        f'"expected_drop":{drop},"token":"tok0"}}')
    assert par.read_record("/x") is par.CORRUPT
    assert "expected_drop" in capsys.readouterr().err


def test_read_record_absent_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(par, "git_dir", lambda repo: tmp_path)
    assert par.read_record("/x") is None


def test_every_typed_field_is_also_a_required_one():
    """`RECORD_TYPES` must stay a subset of `RECORD_KEYS`, and nothing else says so.

    The loop in `read_record` reads `data[field]` after the presence check has run over
    `RECORD_KEYS` alone, so a typed key that is not required raises an uncaught KeyError
    out of the one function whose contract is turning a bad record into rc 2 -- measured
    2026-08-22 by adding `parked_times` to the table: `KeyError: 'parked_times'`, not a
    rejection. Cheap to state, invisible until someone adds the wrong row.
    """
    assert par.RECORD_TYPES.keys() <= par.RECORD_KEYS


@pytest.mark.parametrize("body,names", [
    ("{not json", "could not be read"),
    ("[1, 2]", "not a JSON object"),
    ('{"commits":[],"staged":[],"expected_drop":0}', "`token`"),
    # `staged` is in RECORD_KEYS and NOT in RECORD_TYPES -- still required, never typed.
    # This row is what pins the accept set as byte-identical to the pre-guard one: drop
    # `staged` from RECORD_KEYS and a record without it is silently accepted.
    ('{"commits":[],"expected_drop":0,"token":"t"}', "`staged`"),
    ('{"commits":3,"staged":[],"expected_drop":0,"token":"t"}', "`commits`"),
])
def test_read_record_names_the_cause_of_every_rejection(tmp_path, monkeypatch, capsys, body, names):
    """Pins that a rejection says which cause it was, not just that it was one.

    A bare `return CORRUPT` on any of these legs passes every other test in this file --
    the sentinel is all they assert on -- so without these rows the docstring's "all four
    report which" is an untested claim, and the operator gets `restore_verdict`'s generic
    message with nothing above it to say what was wrong.

    Not one row per shape: five rows over four shapes, two of them on the missing-key shape
    because the second is what pins `staged` inside RECORD_KEYS. The fifth `_unusable` leg,
    out of range, is covered by `test_read_record_rejects_an_expected_drop_outside_its_range`
    instead -- it needs a non-empty `commits` and does not fit this fixture.
    """
    monkeypatch.setattr(par, "git_dir", lambda repo: tmp_path)
    (tmp_path / par.RECORD_NAME).write_text(body)
    assert par.read_record("/x") is par.CORRUPT
    assert names in capsys.readouterr().err


def test_read_record_reports_bytes_that_are_not_utf_8(tmp_path, monkeypatch, capsys):
    """`json.JSONDecodeError` does not cover the read, only the parse.

    `Path.read_text()` raises `UnicodeDecodeError` first, and that is a `ValueError`, not
    an `OSError` -- so with the narrower tuple it left `read_record` as a traceback, past
    `cmd_restore`'s `except GitError`: rc 1 out of the one function whose job is turning a
    bad record into rc 2 (measured 2026-08-22). `json.dumps` writes ASCII, so this shape
    needs a hand edit or another tool, which is the threat model of the whole guard.
    """
    monkeypatch.setattr(par, "git_dir", lambda repo: tmp_path)
    (tmp_path / par.RECORD_NAME).write_bytes(b'{"commits": "\xff\xfe"}')
    assert par.read_record("/x") is par.CORRUPT
    assert "UnicodeDecodeError" in capsys.readouterr().err


# --- cmd_park / cmd_restore against a real repo ----------------------------------------

# A prepare-commit-msg hook that prefixes the subject -- the class `--no-verify` does NOT
# cover. Written in python rather than `sed -i ""`, which is BSD-only and would make these
# tests error on GNU sed (or, worse, keep passing with the hook never firing).
HOOK_PREFIX_SUBJECT = (
    "#!/usr/bin/env python3\n"
    "import sys, pathlib\n"
    "p = pathlib.Path(sys.argv[1])\n"
    "lines = p.read_text().splitlines(keepends=True)\n"
    "lines[0] = '[TICKET-1] ' + lines[0]\n"
    "p.write_text(''.join(lines))\n"
)


def _run(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    """A feature branch whose base advanced on the same file -- the rc 1 shape."""
    r = tmp_path / "r"
    r.mkdir()
    _run(r, "init", "-q", "-b", "master")
    _run(r, "config", "user.email", "t@t")
    _run(r, "config", "user.name", "T")
    (r / "c.txt").write_text("L1\n")
    _run(r, "add", "-A")
    _run(r, "commit", "-qm", "base")
    _run(r, "branch", "-q", "feat")
    (r / "c.txt").write_text("L1-base\n")
    _run(r, "commit", "-qam", "advance")
    _run(r, "remote", "add", "origin", str(r))
    _run(r, "update-ref", "refs/remotes/origin/master", "refs/heads/master")
    _run(r, "checkout", "-q", "feat")
    return r


def _args(repo, **kw):
    ns = type("A", (), {"repo": str(repo), "base": "master", "allow_dropped": False})()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


@pytest.mark.parametrize("record_json,shape", [
    ('{"pre_head":"x","base":"master","commits":[],"staged":[],'
     '"expected_drop":0,"token":"tok0"}', "a well-formed record -- read_record returns a dict"),
    ('{"pre_head":"x","base":"master","commits":3,"staged":[],'
     '"expected_drop":0,"token":"tok0"}', "a type-broken one -- read_record returns CORRUPT"),
])
def test_park_refuses_when_a_record_already_exists(repo, record_json, shape):
    """`cmd_park` tests `read_record(...) is not None`, so CORRUPT must refuse like a dict.

    That identity is the precondition for validating the record inside `read_record` at all:
    every shape this change newly rejects comes back as CORRUPT, and CORRUPT is not None, so
    `park` keeps refusing to start a second park on top of an unfinished one. Collapsing
    CORRUPT into None here would let it park over the first, and `restore` would then leave
    that first park as a real commit on the branch. Both rows because they reach the branch
    by different values, and neither had a test before.
    """
    (par.git_dir(str(repo)) / par.RECORD_NAME).write_text(record_json)
    (repo / "p.txt").write_text("pending\n")
    assert par.cmd_park(_args(repo)) == 2
    assert (par.git_dir(str(repo)) / par.RECORD_NAME).exists()   # the refusal kept it


def test_park_refuses_an_unfinished_earlier_park(repo):
    # killed between the commit and the record write: clean tree, work inside the park
    # commit, no record. Parking again would rebase it onto the base and let step 5 push it.
    (repo / "p.txt").write_text("pending\n")
    _run(repo, "add", "-A")
    _run(repo, "commit", "-qm", par.PARK_SUBJECT)
    assert par.cmd_park(_args(repo)) == 2
    assert par.read_record(str(repo)) is None


def test_restore_partitions_restageable_paths_from_the_rest(repo, capsys):
    # `git add` is atomic over a pathspec list: with one batch add, the path that survived
    # stages too -- so a good path and a gone path must be asserted together, or a batch
    # implementation passes the test while re-introducing the loss
    (repo / "keep.txt").write_text("survives\n")
    _run(repo, "add", "keep.txt")
    record = par.git_dir(str(repo)) / par.RECORD_NAME
    record.write_text('{"pre_head":"x","base":"master","commits":[],'
                      '"staged":["gone.txt","keep.txt"],"expected_drop":0,"token":"tok0"}')
    _run(repo, "commit", "-q", "--allow-empty", "-m", par.PARK_SUBJECT)
    assert par.cmd_restore(_args(repo)) == 0
    err = capsys.readouterr().err
    assert "re-staged" in err and "keep.txt" in err
    assert "NOT re-staged" in err and "gone.txt" in err
    # the survivor really is in the index, not just in the message
    staged = subprocess.run(["git", "-C", str(repo), "diff", "--name-only", "--cached"],
                            capture_output=True, text=True).stdout
    assert "keep.txt" in staged


def test_restage_is_not_atomic_over_the_list(repo):
    (repo / "keep.txt").write_text("survives\n")
    done, failed = par._restage(str(repo), ["gone.txt", "keep.txt"])
    assert done == ["keep.txt"] and failed == ["gone.txt"]


@pytest.mark.parametrize("bad", ["5", "true", "3.4", '"keep.txt"', '{"keep.txt": 1}', "null"])
def test_restore_survives_a_staged_that_is_not_a_list(repo, capsys, bad):
    """Pins the isinstance guard, and discriminates it from a try/except around _restage.

    `record.get("staged") or []` passes any truthy value through, and `staged` is in
    RECORD_KEYS but not in RECORD_TYPES, so read_record checks that it is present and never
    what it holds -- the record is a file on disk a human can edit. Past `git reset HEAD~1`,
    where the module states nothing may fail, an unguarded non-list does one of two things:
    int, float and bool are not iterable and come out of `for path in paths` as an uncaught
    TypeError, while a str iterates into single characters that each become a `git add`
    pathspec -- a `.`, or any pathspec-magic
    character like `*`, matches the whole worktree, so it gets staged with no exception and
    no hint that the record was wrong (measured 2026-08-20; what a dotless string does is
    repo-dependent, not harmless by rule).

    Six values because the CONTAINER outcome set is closed by grammar, not by sampling --
    the element set is a separate question, closed separately by
    test_restore_survives_a_staged_list_holding_non_strings below, and it is larger.
    json.loads produces exactly seven Python types (NoneType, bool, dict, float, int, list,
    str), and
    `for path in <value>` partitions them -- int, float and bool raise; str and dict reach
    `git add`; None is falsy and the old `or []` swallowed it; list is the intended type.
    Each covers something the others do not. str and dict discriminate the guard from a
    `try/except TypeError` around the _restage call: that mutant absorbs the three raisers
    and passes, but leaves str running `git add -- "."`, which prints and trips the first
    assert. null pins the *reporting* edge -- it is falsy, so a guard written
    `staged and not isinstance(staged, list)` stays silent on it and every other falsy
    corrupt shape while the whole suite still passes. The other edge, a legitimate empty
    list, is pinned by test_restore_stays_quiet_when_nothing_was_staged below; without it a
    guard written `not (isinstance(staged, list) and staged)` reports "field is list, not a
    list" on every ordinary restore, also with a green suite. Both mutants were measured.
    """
    (repo / "keep.txt").write_text("survives\n")
    _run(repo, "add", "keep.txt")
    record = par.git_dir(str(repo)) / par.RECORD_NAME
    record.write_text('{"pre_head":"x","base":"master","commits":[],'
                      f'"staged":{bad},"expected_drop":0,"token":"tok0"}}')
    _run(repo, "commit", "-q", "--allow-empty", "-m", par.PARK_SUBJECT)
    assert par.cmd_restore(_args(repo)) == 0
    err = capsys.readouterr().err
    assert "re-staged" not in err          # nothing was staged, by any route
    assert "not a list" in err             # and the give-up was reported, not silent
    # the docstring's harm is an index fact, so assert the index and not only the message:
    # under the try/except mutant the str case leaves the whole worktree staged here
    assert subprocess.run(["git", "-C", str(repo), "diff", "--name-only", "--cached"],
                          capture_output=True, text=True).stdout == ""


@pytest.mark.parametrize("bad", ['[5]', '[null]', '[["keep.txt"]]', '[{"a": 1}]',
                                 '["keep.txt", 5]'])
def test_restore_survives_a_staged_list_holding_non_strings(repo, capsys, bad):
    """The same crash one level down: the container is a list, an element is not a str.

    `isinstance(staged, list)` alone does not close this. A well-formed list holding a
    non-str reaches `git add -- <value>` and raises `TypeError: expected str, bytes or
    os.PathLike object`, past `git reset HEAD~1` where nothing may fail -- and the raise
    is above the record `unlink`, so the stale park record survives too.

    The element crash set is LARGER than the container one, which is why this cannot be
    folded into the test above as "more of the same": every non-str type raises here,
    including None and dict, and those two are absorbed harmlessly as containers. The
    mixed case is the one that shows why a per-path net inside `_restage` would not be
    equivalent -- unguarded, `["keep.txt", 5]` stages keep.txt and only then raises, so
    the index is left half-rebuilt with no error and no report.
    """
    (repo / "keep.txt").write_text("survives\n")
    _run(repo, "add", "keep.txt")
    record = par.git_dir(str(repo)) / par.RECORD_NAME
    record.write_text('{"pre_head":"x","base":"master","commits":[],'
                      f'"staged":{bad},"expected_drop":0,"token":"tok0"}}')
    _run(repo, "commit", "-q", "--allow-empty", "-m", par.PARK_SUBJECT)
    assert par.cmd_restore(_args(repo)) == 0
    err = capsys.readouterr().err
    assert "re-staged" not in err
    assert "not a list of path strings" in err
    assert "a list holding" in err                  # names the offending element type
    assert subprocess.run(["git", "-C", str(repo), "diff", "--name-only", "--cached"],
                          capture_output=True, text=True).stdout == ""
    # the record is gone, so the next park does not refuse on a leftover
    assert not record.exists()


@pytest.mark.parametrize("bad", ['"a\\u0000b"', '"\\ud800"'])
def test_restore_survives_a_staged_path_execve_refuses(repo, capsys, bad):
    """The third level: the element IS a str, and subprocess still will not take it.

    `isinstance(p, str)` closes the type dimension and stops there. json.loads happily
    produces a string with an embedded NUL, or an unpaired surrogate, and neither ever
    reaches an exit code -- subprocess raises ValueError("embedded null byte") and
    UnicodeEncodeError before execve. Past `git reset HEAD~1` that is the same uncaught
    crash the type guards were added to remove, so the net belongs at the exec point in
    `_restage`, which is the only place that knows what execve accepts.

    It does not loosen the trusted caller: `cmd_park` passes a list from `_snapshot`, whose
    `_lines()` split of git output can produce neither value.
    """
    (repo / "keep.txt").write_text("survives\n")
    _run(repo, "add", "keep.txt")
    record = par.git_dir(str(repo)) / par.RECORD_NAME
    record.write_text('{"pre_head":"x","base":"master","commits":[],'
                      f'"staged":[{bad}],"expected_drop":0,"token":"tok0"}}')
    _run(repo, "commit", "-q", "--allow-empty", "-m", par.PARK_SUBJECT)
    assert par.cmd_restore(_args(repo)) == 0
    assert "NOT re-staged" in capsys.readouterr().err


def test_restage_does_not_raise_on_paths_execve_refuses(repo):
    done, failed = par._restage(str(repo), ["a\x00b", "\ud800"])
    assert done == [] and failed == ["a\x00b", "\ud800"]


def test_restore_names_the_usable_paths_before_deleting_the_record(repo, capsys):
    """Refusing the list must not also destroy it.

    This branch is the only place the module holds path strings it has decided not to
    re-stage, and `cmd_restore` unlinks the record a few lines below. Before the type
    guard existed the TypeError aborted above that unlink, so the operator could still
    read the list off disk; a guard that returns 0 and stays quiet about the names is a
    regression on exactly the recovery path the guard was meant to protect.
    """
    (repo / "keep.txt").write_text("survives\n")
    _run(repo, "add", "keep.txt")
    record = par.git_dir(str(repo)) / par.RECORD_NAME
    record.write_text('{"pre_head":"x","base":"master","commits":[],'
                      '"staged":["keep.txt","secret_new.txt",5],'
                      '"expected_drop":0,"token":"tok0"}')
    _run(repo, "commit", "-q", "--allow-empty", "-m", par.PARK_SUBJECT)
    assert par.cmd_restore(_args(repo)) == 0
    err = capsys.readouterr().err
    assert "a list holding int" in err
    assert "keep.txt" in err and "secret_new.txt" in err   # the names survive the refusal
    assert not record.exists()                             # and the record really is gone


def test_restore_stays_quiet_when_nothing_was_staged(repo, capsys):
    """The other edge of the guard: an empty `staged` is legitimate, not corruption.

    Pins the `isinstance(...) and staged` / `not isinstance(...)` split against a guard
    written `not (isinstance(staged, list) and staged)`, which reports every ordinary
    restore that had nothing in the index as a bad record -- measured green across the
    rest of the suite.
    """
    (repo / "w.txt").write_text("pending-unstaged\n")
    assert par.cmd_park(_args(repo)) == 0
    assert par.cmd_restore(_args(repo)) == 0
    assert "not a list" not in capsys.readouterr().err


def test_restore_rewinds_mtimes_even_when_staged_is_corrupt(repo, capsys):
    """The `elif` must not swallow the mtime rewind that follows it.

    The two blocks were written on separate branches and met here at a rebase: the `elif`
    binds to the `staged` guard and sits directly above the unconditional `parked_times`
    rewind. Bind it one level wrong -- as an `else` around the rest of the function, or by
    folding the rewind into the `if` -- and a corrupt `staged` silently costs the mtime
    rewind too, which is the thing that keeps an untouched file from reading as edited to
    lib/review_marker.py. Neither branch's own tests cover the pair.
    """
    (repo / "w.txt").write_text("pending-unstaged\n")
    assert par.cmd_park(_args(repo)) == 0

    record_path = par.git_dir(str(repo)) / par.RECORD_NAME
    record = json.loads(record_path.read_text())
    assert isinstance(record["parked_times"], list) and record["parked_times"]
    record["staged"] = 5                      # the corrupt shape, reached via the elif
    record_path.write_text(json.dumps(record))

    assert par.cmd_restore(_args(repo)) == 0
    err = capsys.readouterr().err
    assert "not a list" in err                # the elif fired
    assert "mtime restored" in err            # and the rewind still ran after it


@pytest.mark.parametrize("bad", ["3", "{}"])
def test_restore_refuses_a_commits_field_that_is_not_a_list(repo, capsys, bad):
    """Both halves of the hole reach `cmd_restore` as one refusal, and neither moves HEAD.

    The two rows failed in opposite directions before the guard, which is why one row
    cannot stand for the other: `3` raised a TypeError out of `restore_verdict`, so a skill
    branching on the exit code read rc 1 as something other than a refusal; `{}` raised
    nothing, `len({})` made `expected` 0, and the reset landed with the dropped-commit guard
    silently off (both measured 2026-08-22).
    """
    (repo / "p.txt").write_text("pending\n")
    record = par.git_dir(str(repo)) / par.RECORD_NAME
    record.write_text('{"pre_head":"x","base":"master","commits":' + bad +
                      ',"staged":[],"expected_drop":0,"token":"tok0"}')
    _run(repo, "commit", "-q", "--allow-empty", "-m", par.PARK_SUBJECT)
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout
    assert par.cmd_restore(_args(repo)) == 2
    err = capsys.readouterr().err
    assert "commits" in err            # the field is named, not just "unusable"
    assert "unusable" in err
    # `unusable` alone stopped discriminating once read_record started printing it too;
    # this fragment exists only in restore_verdict's operator message
    assert "Do not reset blind" in err
    # "the tree was not touched" is the documented promise of rc 2 -- assert it, do not
    # infer it from the return value
    assert subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout == head
    assert record.exists()             # a refusal must not strand the park by deleting it


def test_restore_refuses_an_expected_drop_above_the_commit_count(repo, capsys):
    """Not a wrong type at all, and it used to get past rc 2 in the worst direction.

    Here at the `cmd_restore` level and not only as a unit row because what it broke is the
    module's rc-2 promise: `expected` went negative, `kept < expected` was false for every
    count, and the reset happened with the dropped-commit guard off (measured 2026-08-22).
    """
    (repo / "p.txt").write_text("pending\n")
    record = par.git_dir(str(repo)) / par.RECORD_NAME
    record.write_text('{"pre_head":"x","base":"master","commits":["a1","a2","a3"],'
                      '"staged":[],"expected_drop":99,"token":"tok0"}')
    _run(repo, "commit", "-q", "--allow-empty", "-m", par.PARK_SUBJECT)
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout
    assert par.cmd_restore(_args(repo)) == 2
    err = capsys.readouterr().err
    assert "expected_drop" in err       # the field is named, not just "unusable"
    assert "unusable" in err
    assert "Do not reset blind" in err
    assert subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout == head
    assert record.exists()


def test_restore_does_not_report_the_record_while_a_rebase_is_in_progress(repo, capsys):
    """`busy` is read before the record, and that ordering is the whole point.

    `read_record` prints why a record is unusable. Reading it first put that line above a
    verdict that talks only about the rebase -- an orphan the operator reads as a second,
    unexplained problem, and `restore_verdict`'s "The line above names which" never runs to
    explain it. Asserted as a PAIR and not as an absence alone: a test that only checks the
    record line is gone passes just as well when `cmd_restore` prints nothing at all.
    """
    record = par.git_dir(str(repo)) / par.RECORD_NAME
    record.write_text('{"pre_head":"x","base":"master","commits":3,'
                      '"staged":[],"expected_drop":0,"token":"tok0"}')
    (par.git_dir(str(repo)) / "rebase-merge").mkdir()
    assert par.cmd_restore(_args(repo)) == 2
    err = capsys.readouterr().err
    assert "rebase-merge still in progress" in err   # the verdict the operator needs
    assert "is unusable" not in err                  # and nothing orphaned above it


def test_park_then_restore_round_trips_a_branch_git_cherry_calls_upstream(repo):
    """The one shape where `cmd_park` writes a NON-ZERO `expected_drop`, end to end.

    Every other `cmd_park` test in this file produces `expected_drop: 0`, so the new range
    check `0 <= expected_drop <= len(commits)` was only ever exercised by real `git cherry`
    output at its degenerate end, and its upper bound only by hand-written dicts. That is
    the one way this change could strand a park: if the invariant did not hold, `park` would
    write a record `restore` then refuses at rc 2, with the work sitting in the park commit.

    Here the branch's only commit is already on the base by patch-id, so `git cherry` marks
    it `-`: `commits` has one entry and `expected_drop` is 1, which is the upper bound
    exactly. The rebase then drops that commit, `kept` is 0, and `restore` must still return
    0 -- the case `test_count_check_tolerates_the_drops_git_predicted` covers as a unit.
    """
    (repo / "shared.txt").write_text("both sides\n")
    _run(repo, "add", "shared.txt")
    _run(repo, "commit", "-qm", "feat: lands on both sides")
    picked = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    _run(repo, "checkout", "-q", "master")
    _run(repo, "cherry-pick", picked)
    _run(repo, "update-ref", "refs/remotes/origin/master", "refs/heads/master")
    _run(repo, "checkout", "-q", "feat")

    # what `_snapshot` will record -- asserted, so the test fails loudly if this repo shape
    # stops producing the drop rather than silently going back to testing `expected_drop: 0`
    commits, _, expected_drop = par._snapshot(str(repo), "master")
    assert len(commits) == 1 and expected_drop == 1

    (repo / "pending.txt").write_text("work in progress\n")
    assert par.cmd_park(_args(repo)) == 0
    record = json.loads((par.git_dir(str(repo)) / par.RECORD_NAME).read_text())
    assert record["expected_drop"] == 1 and len(record["commits"]) == 1
    assert par.cmd_restore(_args(repo)) == 0
    assert (repo / "pending.txt").read_text() == "work in progress\n"
    assert par.read_record(str(repo)) is None


def test_park_then_restore_round_trips_the_working_tree(repo):
    # the happy path end to end: the base advanced, the branch has pending work on other
    # paths, so the rebase applies the park without conflict
    (repo / "w.txt").write_text("pending-unstaged\n")
    (repo / "new.txt").write_text("staged-new\n")
    _run(repo, "add", "new.txt")
    before = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                            capture_output=True, text=True).stdout
    assert par.cmd_park(_args(repo)) == 0
    assert par.cmd_restore(_args(repo)) == 0
    after = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                           capture_output=True, text=True).stdout
    assert after == before
    assert (repo / "w.txt").read_text() == "pending-unstaged\n"
    assert (repo / "new.txt").read_text() == "staged-new\n"
    assert (repo / "c.txt").read_text() == "L1-base\n"   # the fresh base really landed
    # the branch really moved onto the fresh base, and no park commit survived
    log = subprocess.run(["git", "-C", str(repo), "log", "--oneline"],
                         capture_output=True, text=True).stdout
    assert par.PARK_SUBJECT not in log and "advance" in log
    assert par.read_record(str(repo)) is None


def test_park_refuses_a_non_ascii_skip_worktree_path(repo):
    # holds the core.quotePath=false fix: `ls-files -v` C-quotes `café.yml` by default,
    # which then matches nothing on disk and drops out of `hidden`, so the refusal that
    # keeps the rebase from clobbering it silently never fires
    odd = "café.yml"
    _run(repo, "checkout", "-q", "master")
    (repo / odd).write_text("v1\n")
    _run(repo, "add", "-A")
    _run(repo, "commit", "-qm", "add odd path")
    _run(repo, "checkout", "-q", "feat")
    _run(repo, "merge", "-q", "master")            # feat now tracks it too
    _run(repo, "checkout", "-q", "master")
    (repo / odd).write_text("base version\n")
    _run(repo, "commit", "-qam", "base touches the odd path")
    _run(repo, "update-ref", "refs/remotes/origin/master", "refs/heads/master")
    _run(repo, "checkout", "-q", "feat")
    _run(repo, "update-index", "--skip-worktree", "--", odd)
    (repo / "p.txt").write_text("pending\n")
    assert par.cmd_park(_args(repo)) == 2
    assert par.read_record(str(repo)) is None


def test_conflicting_rebase_round_trips_through_continue(repo, capsys):
    # rc 1 fires precisely when the base advanced on files the branch also changes, so the
    # park's rebase CONFLICTING is the normal case, not the exception. The whole
    # rc 3 -> --continue -> restore sequence had no test.
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "prepare-commit-msg"
    hook.write_text(HOOK_PREFIX_SUBJECT)
    hook.chmod(0o755)
    _run(repo, "commit", "-qm", "branch work", "--allow-empty")
    (repo / "c.txt").write_text("L1-branch-pending\n")      # base has L1-base -> conflict
    assert par.cmd_park(_args(repo)) == 3
    msg = capsys.readouterr().err
    # the message must hand over the suppressed form; a bare --continue re-runs the hook
    assert "core.hooksPath=/dev/null rebase --continue" in msg
    assert "--skip" in msg

    (repo / "c.txt").write_text("L1-branch-pending\n")      # resolve, keeping the park side
    _run(repo, "add", "c.txt")
    subprocess.run(["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null",
                    "rebase", "--continue"],
                   check=True, capture_output=True, env={**os.environ, "GIT_EDITOR": "true"})
    subject = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%s"],
                             capture_output=True, text=True).stdout.strip()
    assert subject == par.PARK_SUBJECT                      # survived the replay
    assert par.cmd_restore(_args(repo)) == 0
    assert (repo / "c.txt").read_text() == "L1-branch-pending\n"
    log = subprocess.run(["git", "-C", str(repo), "log", "--oneline"],
                         capture_output=True, text=True).stdout
    assert par.PARK_SUBJECT not in log and "advance" in log
    assert par.read_record(str(repo)) is None


def test_park_survives_a_prepare_commit_msg_hook(repo):
    # `--no-verify` covers pre-commit and commit-msg, NOT prepare-commit-msg. A hook that
    # prefixes the subject used to make park report rc 4 "nothing to restore" while the work
    # sat in an unrestorable commit headed for `git push`.
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "prepare-commit-msg"
    hook.write_text(HOOK_PREFIX_SUBJECT)
    hook.chmod(0o755)
    (repo / "w.txt").write_text("pending\n")
    assert par.cmd_park(_args(repo)) == 0
    subject = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%s"],
                             capture_output=True, text=True).stdout.strip()
    assert subject == par.PARK_SUBJECT
    assert par.cmd_restore(_args(repo)) == 0
    assert (repo / "w.txt").read_text() == "pending\n"
    assert par.read_record(str(repo)) is None


def test_park_undoes_itself_if_the_subject_did_not_survive(repo, capsys, monkeypatch):
    # defence in depth behind NO_HOOKS: whatever else might rewrite a message, the subject is
    # the invariant every later check keys on, so park verifies it rather than trusting the
    # hook suppression to be complete. Driven directly, since NO_HOOKS stops the real hooks.
    real_git, seen = par._git, []

    def fake_git(r, args):
        out = real_git(r, args)
        if args[:3] == ["log", "-1", "--format=%s"]:
            seen.append(out)
            if len(seen) > 1:                     # the post-commit read
                return "[TICKET-1] " + out
        return out

    monkeypatch.setattr(par, "_git", fake_git)
    (repo / "w.txt").write_text("pending\n")
    (repo / "s.txt").write_text("staged\n")
    _run(repo, "add", "s.txt")
    assert par.cmd_park(_args(repo)) == 2
    assert "rewrote the park commit's message" in capsys.readouterr().err
    # undone: no park commit left, the work is back, and the staged path is staged again
    log = subprocess.run(["git", "-C", str(repo), "log", "--oneline"],
                         capture_output=True, text=True).stdout
    assert par.PARK_SUBJECT not in log
    assert (repo / "w.txt").read_text() == "pending\n"
    status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                            capture_output=True, text=True).stdout
    assert "A  s.txt" in status
    assert par.read_record(str(repo)) is None


def test_park_escalates_to_5_when_the_undo_leaves_the_index_short(repo, capsys, monkeypatch):
    # rc 5 is the only code meaning "the repo IS changed", and the only one the skill tells the
    # agent to branch on; none of its seven sites had a test.
    real_git, seen = par._git, []

    def fake_git(r, args):
        out = real_git(r, args)
        if args[:3] == ["log", "-1", "--format=%s"]:
            seen.append(out)
            if len(seen) > 1:
                return "[TICKET-1] " + out
        return out

    real_restage = par._restage
    monkeypatch.setattr(par, "_git", fake_git)
    monkeypatch.setattr(par, "_restage", lambda r, paths: (real_restage(r, paths)[0], ["ghost.txt"]))
    (repo / "w.txt").write_text("pending\n")
    assert par.cmd_park(_args(repo)) == 5
    err = capsys.readouterr().err
    assert "ghost.txt" in err and "Stage them by hand" in err


def test_park_stops_when_a_rewritten_subject_survived_the_rebase(repo, capsys, monkeypatch):
    # the rc 4 patch-id branch and a park that survived under a rewritten subject share one
    # signal (HEAD subject != PARK_SUBJECT); reading the second as the first deletes the record
    # over work still at HEAD and headed for the push
    real_git_rc = par._git_rc
    monkeypatch.setattr(
        par, "_git_rc",
        lambda r, args: ((0, "[TICKET-1] " + par.PARK_SUBJECT)
                         if args[:3] == ["log", "-1", "--format=%s"] else real_git_rc(r, args)))
    (repo / "w.txt").write_text("pending\n")
    assert par.cmd_park(_args(repo)) == 5
    err = capsys.readouterr().err
    assert "still HEAD but its subject was rewritten" in err
    assert "core.hooksPath=/dev/null commit --amend" in err
    # a dict, not merely non-None: CORRUPT is also non-None and is the state to avoid
    assert isinstance(par.read_record(str(repo)), dict)


def test_the_two_subject_mismatch_states_are_told_apart_with_branch_commits_present(repo, capsys):
    # Both states show "HEAD subject != PARK_SUBJECT" and need opposite handling. A commit
    # count cannot separate them -- it leans on `git cherry`, which under-predicts drops, and
    # one unpredicted drop makes the counts match. These two cases run with branch commits
    # present, where a count-based discriminator gets it wrong.
    _run(repo, "commit", "-qm", "branch work", "--allow-empty")
    _run(repo, "commit", "-qm", "more branch work", "--allow-empty")

    # (a) the park survived under a rewritten subject -> STOP, keep the record
    real_git_rc = par._git_rc
    import unittest.mock as mock
    with mock.patch.object(
            par, "_git_rc",
            lambda r, args: ((0, "[TICKET-1] " + par.PARK_SUBJECT)
                             if args[:3] == ["log", "-1", "--format=%s"] else real_git_rc(r, args))):
        (repo / "w.txt").write_text("pending\n")
        assert par.cmd_park(_args(repo)) == 5
    err = capsys.readouterr().err
    assert "still HEAD but its subject was rewritten" in err
    # a dict, not merely non-None: CORRUPT is also non-None and is the state to avoid
    assert isinstance(par.read_record(str(repo)), dict)

    # clean up that park so the second case starts fresh
    _run(repo, "reset", "-q", "--hard", "HEAD~1")
    _run(repo, "clean", "-qfd")
    (par.git_dir(str(repo)) / par.RECORD_NAME).unlink()

    # (b) a genuine patch-id drop, same branch shape -> rc 4, record removed
    _run(repo, "checkout", "-q", "master")
    (repo / "dup.txt").write_text("same content\n")
    _run(repo, "add", "-A")
    _run(repo, "commit", "-qm", "base adds dup.txt")
    _run(repo, "update-ref", "refs/remotes/origin/master", "refs/heads/master")
    _run(repo, "checkout", "-q", "feat")
    (repo / "dup.txt").write_text("same content\n")
    assert par.cmd_park(_args(repo)) == 4
    assert "already contained this work" in capsys.readouterr().err
    assert par.read_record(str(repo)) is None


def test_park_returns_nothing_to_restore_when_the_base_already_has_the_work(repo, capsys):
    # the rc 4 patch-id branch -- the one that unlinks the record and tells the operator
    # nothing is owed, so the only branch that can strand work if it fires wrongly
    _run(repo, "checkout", "-q", "master")
    (repo / "dup.txt").write_text("same content\n")
    _run(repo, "add", "-A")
    _run(repo, "commit", "-qm", "base adds dup.txt")
    _run(repo, "update-ref", "refs/remotes/origin/master", "refs/heads/master")
    _run(repo, "checkout", "-q", "feat")
    (repo / "dup.txt").write_text("same content\n")     # identical patch, still pending here
    assert par.cmd_park(_args(repo)) == 4
    assert "already contained this work" in capsys.readouterr().err
    assert par.read_record(str(repo)) is None
    assert (repo / "dup.txt").read_text() == "same content\n"
    log = subprocess.run(["git", "-C", str(repo), "log", "--oneline"],
                         capture_output=True, text=True).stdout
    assert par.PARK_SUBJECT not in log


def test_park_uses_the_repo_root_not_the_given_subdirectory(repo):
    # `ls-files` is cwd-relative AND subtree-limited while `diff --name-only` is root-
    # relative, so a --repo below the root made both intersections in `refusals` empty
    sub = repo / "sub"
    sub.mkdir()
    (sub / "keep.txt").write_text("x\n")
    _run(repo, "add", "-A")
    _run(repo, "commit", "-qm", "add subdir")
    (repo / ".gitignore").write_text("sub/secret.env\n")
    _run(repo, "add", "-A")
    _run(repo, "commit", "-qm", "ignore it")
    _run(repo, "checkout", "-q", "master")
    _run(repo, "merge", "-q", "feat")
    (sub / "secret.env").write_text("UPSTREAM\n")
    _run(repo, "add", "-f", "--", "sub/secret.env")
    _run(repo, "commit", "-qm", "base adds the ignored path")
    _run(repo, "update-ref", "refs/remotes/origin/master", "refs/heads/master")
    _run(repo, "checkout", "-q", "feat")
    (sub / "secret.env").write_text("MY LOCAL SECRET\n")
    (repo / "p.txt").write_text("pending\n")
    assert par.cmd_park(_args(sub)) == 2          # --repo points at the subdirectory
    assert (sub / "secret.env").read_text() == "MY LOCAL SECRET\n"


def test_park_records_the_pre_park_times_of_every_parked_path(repo):
    (repo / "w.txt").write_text("pending-unstaged\n")
    (repo / "new.txt").write_text("staged-new\n")
    _run(repo, "add", "new.txt")
    before = {p: (repo / p).stat().st_mtime_ns for p in ("w.txt", "new.txt")}
    assert par.cmd_park(_args(repo)) == 0
    record = par.read_record(str(repo))
    times = {e["path"]: e for e in record["parked_times"]}
    assert set(times) == {"w.txt", "new.txt"}
    for path, entry in times.items():
        assert entry["mtime_ns"] == before[path]
        assert len(entry["sha256"]) == 64


def test_restore_gives_back_the_pre_park_mtime_at_identical_content(repo):
    """A rebase rewrites every parked file, so the pre-flight read an untouched file as edited.

    lib/review_marker.py decides coverage by comparing each file's mtime against the review
    record's epoch. Before this, the rc 1 recovery of the base-freshness guard cost a full
    review loop that only reconfirmed byte-identical content (observed 2026-08-18).
    """
    (repo / "w.txt").write_text("pending-unstaged\n")
    before = (repo / "w.txt").stat().st_mtime_ns
    assert par.cmd_park(_args(repo)) == 0
    assert (repo / "w.txt").stat().st_mtime_ns != before   # the rebase really did rewrite it
    assert par.cmd_restore(_args(repo)) == 0
    assert (repo / "w.txt").stat().st_mtime_ns == before
    assert (repo / "w.txt").read_text() == "pending-unstaged\n"


def test_rewind_leaves_a_path_the_rebase_changed_alone(repo):
    (repo / "w.txt").write_text("after the park\n")
    stale = [{"path": "w.txt", "mtime_ns": 1, "sha256": "0" * 64}]
    fresh = (repo / "w.txt").stat().st_mtime_ns
    assert par._rewind_times(str(repo), stale) == (0, 1, 0)
    assert (repo / "w.txt").stat().st_mtime_ns == fresh


def test_rewind_counts_what_it_cannot_read(repo):
    gone = [{"path": "gone.txt", "mtime_ns": 1, "sha256": "0" * 64}]
    assert par._rewind_times(str(repo), gone) == (0, 0, 1)


def test_rewind_survives_a_record_field_that_is_not_there(repo):
    (repo / "w.txt").write_text("x\n")
    assert par._rewind_times(str(repo), [{"path": "w.txt"}]) == (0, 0, 1)


def test_restore_reports_the_rewind(repo, capsys):
    (repo / "w.txt").write_text("pending-unstaged\n")
    assert par.cmd_park(_args(repo)) == 0
    assert par.cmd_restore(_args(repo)) == 0
    assert "mtime restored on 1 of 1 parked paths" in capsys.readouterr().err


@pytest.mark.parametrize("bad", ["5", "true", "3.4"])
def test_restore_survives_a_parked_times_that_is_not_a_list(repo, capsys, bad):
    """Pins the isinstance guard: without it this raises past `git reset HEAD~1`.

    `record.get("parked_times") or []` passes any truthy value through, and read_record
    types the fields in `RECORD_TYPES` and not this one. Two reasons it cannot simply be
    added there, and the second is the sharper one: it is optional, so requiring it would
    reject every older record; and `RECORD_TYPES` must stay a subset of `RECORD_KEYS`,
    because the loop reads `data[field]` -- a typed key that is not required raises an
    uncaught KeyError out of `read_record` (measured 2026-08-22). A non-list escapes both
    `for entry in parked_times` and the summary line's len() as an uncaught TypeError --
    measured for int, bool and float; dict and str iterate and are absorbed per entry.
    Nothing may return non-zero past the reset, so the guard is load-bearing and a
    consistency cleanup back to the `or []` form would silently restore the crash.

    Three values because three is the whole set, not because three is careful. Sweeping
    all 13 JSON value kinds through the unguarded call site (`or []` -> `for` -> `len()`),
    exactly int, float and true raise: object and string iterate and are absorbed per
    entry, and false, null, 0, 0.0, "", [] and {} are falsy and never enter. Trimming the
    list is therefore a real loss of coverage -- a mutant reading
    `not isinstance(parked_times, int)` excludes int and bool, still admits 3.4, and
    passed while a single-valued version of this test was green.
    """
    (repo / "keep.txt").write_text("survives\n")
    _run(repo, "add", "keep.txt")
    record = par.git_dir(str(repo)) / par.RECORD_NAME
    record.write_text('{"pre_head":"x","base":"master","commits":[],'
                      '"staged":["keep.txt"],"expected_drop":0,"token":"tok0",'
                      f'"parked_times":{bad}}}')
    _run(repo, "commit", "-q", "--allow-empty", "-m", par.PARK_SUBJECT)
    assert par.cmd_restore(_args(repo)) == 0
    assert "mtime restored" not in capsys.readouterr().err


def test_restore_survives_an_mtime_the_platform_cannot_represent(repo, capsys):
    """Pins OverflowError in the per-entry net: without it this raises past `git reset HEAD~1`.

    `os.utime(ns=)` divmods by 10**9 and the seconds part must fit time_t, so an mtime_ns of
    2**63 * 10**9 raises OverflowError -- which derives from ArithmeticError, not OSError, and
    so escaped the original (OSError, KeyError, TypeError) net. The record is a file on disk a
    human may edit, so the value is reachable without a bug anywhere else. Measured end-to-end
    on the unfixed net: the reset had already landed, the record was still there, and no
    restore message had been printed, at rc 1.

    The sha256 is computed, not hardcoded: a mismatch counts `changed` and returns before
    os.utime, which would make this test pass against the unfixed net.
    """
    (repo / "keep.txt").write_text("survives\n")
    _run(repo, "add", "keep.txt")
    sha = par._sha256(repo / "keep.txt")
    record = par.git_dir(str(repo)) / par.RECORD_NAME
    record.write_text('{"pre_head":"x","base":"master","commits":[],'
                      '"staged":["keep.txt"],"expected_drop":0,"token":"tok0",'
                      f'"parked_times":[{{"path":"keep.txt","mtime_ns":{2 ** 63 * 10 ** 9},'
                      f'"sha256":"{sha}"}}]}}')
    _run(repo, "commit", "-q", "--allow-empty", "-m", par.PARK_SUBJECT)
    assert par.cmd_restore(_args(repo)) == 0
    err = capsys.readouterr().err
    assert ("mtime restored on 0 of 1 parked paths (content identical); 0 changed since the "
            "park; 1 skipped") in err


def test_restore_survives_a_record_written_before_this_key_existed(repo, capsys):
    """A park from an older version has no parked_times; the restore must not notice."""
    (repo / "keep.txt").write_text("survives\n")
    _run(repo, "add", "keep.txt")
    record = par.git_dir(str(repo)) / par.RECORD_NAME
    record.write_text('{"pre_head":"x","base":"master","commits":[],'
                      '"staged":["keep.txt"],"expected_drop":0,"token":"tok0"}')
    _run(repo, "commit", "-q", "--allow-empty", "-m", par.PARK_SUBJECT)
    assert par.cmd_restore(_args(repo)) == 0
    assert "mtime restored" not in capsys.readouterr().err


def test_restore_still_succeeds_when_utime_fails(repo, capsys, monkeypatch):
    """Past the reset the work IS back in the tree; nothing below it may return non-zero."""
    (repo / "w.txt").write_text("pending-unstaged\n")
    assert par.cmd_park(_args(repo)) == 0
    monkeypatch.setattr(par.os, "utime", lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    assert par.cmd_restore(_args(repo)) == 0
    assert "1 skipped" in capsys.readouterr().err
