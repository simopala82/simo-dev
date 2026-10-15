#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Tests for hooks/review_loop_warn.py, ported to pytest from the retired shell suite."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / "review_loop_warn.py"
REVIEW_MARKER = Path(__file__).resolve().parent.parent.parent / "lib" / "review_marker.py"


def _mkrepo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "-C", str(r), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(r), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(r), "config", "user.name", "t"], check=True)
    (r / "a.txt").write_text("base\n")
    subprocess.run(["git", "-C", str(r), "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", str(r), "commit", "-qm", "base"], check=True)
    (r / "a.txt").write_text("changed\n")   # left UNSTAGED -- PreToolUse sees an empty index
    return r


def _record(repo, state, session="sess-x"):
    subprocess.run(
        [sys.executable, str(REVIEW_MARKER), "record", "--repo", str(repo), "--session", session],
        env={"REVIEW_MARKER_STATE_FILE": str(state)}, check=True, capture_output=True,
    )


def _warns(cmd, cwd, state, session="sess-x", extra_env=None):
    env = {"REVIEW_MARKER_STATE_FILE": str(state)}
    if extra_env:
        env.update(extra_env)
    payload = {"cwd": str(cwd), "tool_input": {"command": cmd}}
    if session is not None:
        payload["session_id"] = session
    result = subprocess.run(
        [sys.executable, str(HOOK)], input=json.dumps(payload),
        text=True, env=env, capture_output=True,
    )
    return result.stdout


def test_plain_commit_triggers(tmp_path):
    repo = _mkrepo(tmp_path)
    assert "additionalContext" in _warns("git commit -m x", repo, tmp_path / "state")


def test_dash_c_commit_triggers(tmp_path):
    repo = _mkrepo(tmp_path)
    assert "additionalContext" in _warns(f"git -C {repo} commit -m x", repo, tmp_path / "state")


def test_rtk_wrapped_commit_triggers(tmp_path):
    repo = _mkrepo(tmp_path)
    assert "additionalContext" in _warns("rtk git commit -m x", repo, tmp_path / "state")


def test_dash_c_config_commit_triggers(tmp_path):
    repo = _mkrepo(tmp_path)
    assert "additionalContext" in _warns("git -c user.name=x commit -m y", repo, tmp_path / "state")


def test_git_status_stays_silent(tmp_path):
    repo = _mkrepo(tmp_path)
    assert "additionalContext" not in _warns("git status", repo, tmp_path / "state")


def test_git_log_stays_silent(tmp_path):
    repo = _mkrepo(tmp_path)
    assert "additionalContext" not in _warns("git log --grep commit", repo, tmp_path / "state")


def test_prose_mentioning_commit_stays_silent(tmp_path):
    repo = _mkrepo(tmp_path)
    assert "additionalContext" not in _warns("echo run git commit later >> notes.txt", repo, tmp_path / "state")


def test_grep_for_commit_stays_silent(tmp_path):
    repo = _mkrepo(tmp_path)
    assert "additionalContext" not in _warns("rg git commit docs/", repo, tmp_path / "state")


def test_dash_c_inside_message_cannot_hijack(tmp_path):
    repo = _mkrepo(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "-C", str(other), "init", "-q"], check=True)
    out = _warns(f"git commit -m 'explain the -C {other} form'", repo, tmp_path / "state")
    assert "additionalContext" in out


def test_chained_add_and_commit_triggers(tmp_path):
    repo = _mkrepo(tmp_path)
    out = _warns(f"git -C {repo} add a.txt && git -C {repo} commit -m x", repo, tmp_path / "state")
    assert "additionalContext" in out


def test_untracked_only_work_triggers(tmp_path):
    repo = _mkrepo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "--", "a.txt"], check=True)
    (repo / "b.txt").write_text("new\n")
    out = _warns(f"git -C {repo} add b.txt && git -C {repo} commit -m x", repo, tmp_path / "state")
    assert "additionalContext" in out


def test_dash_c_overrides_payload_cwd(tmp_path):
    repo = _mkrepo(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "-C", str(other), "init", "-q"], check=True)
    out = _warns(f"git -C {repo} commit -m x", other, tmp_path / "state")
    assert "additionalContext" in out


def test_cd_overrides_payload_cwd(tmp_path):
    repo = _mkrepo(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "-C", str(other), "init", "-q"], check=True)
    out = _warns(f"cd {repo} && git commit -m x", other, tmp_path / "state")
    assert "additionalContext" in out


def test_covered_commit_stays_silent(tmp_path):
    repo = _mkrepo(tmp_path)
    state = tmp_path / "state"
    _record(repo, state)
    subprocess.run(["touch", "-t", "200001010000", str(repo / "a.txt")], check=True)
    assert "additionalContext" not in _warns("git commit -m x", repo, state)


def test_post_review_edit_warns_again(tmp_path):
    repo = _mkrepo(tmp_path)
    state = tmp_path / "state"
    _record(repo, state)
    subprocess.run(["touch", "-t", "200001010000", str(repo / "a.txt")], check=True)
    (repo / "a.txt").write_text("changed again\n")
    assert "additionalContext" in _warns("git commit -m x", repo, state)


def test_missing_session_id_fails_closed(tmp_path):
    repo = _mkrepo(tmp_path)
    state = tmp_path / "state"
    _record(repo, state)
    subprocess.run(["touch", "-t", "200001010000", str(repo / "a.txt")], check=True)
    out = _warns("git commit -m x", repo, state, session=None)
    assert "review record could not be read" in out


def test_unresolvable_cd_falls_back_to_payload_cwd(tmp_path):
    repo = _mkrepo(tmp_path)
    out = _warns("cd /tmp/definitely-not-a-repo-xyz && git commit -m x", repo, tmp_path / "state")
    assert "additionalContext" in out


def test_every_stale_file_reaches_the_reminder(tmp_path):
    repo = _mkrepo(tmp_path)
    state = tmp_path / "state"
    (repo / "b.txt").write_text("second\n")
    _record(repo, state)
    subprocess.run(["touch", str(repo / "a.txt"), str(repo / "b.txt")], check=True)
    out = _warns("git commit -m x", repo, state)
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "a.txt" in ctx and "b.txt" in ctx
    assert "\n" not in ctx   # the reminder stays one line


def test_long_stale_list_is_capped_at_ten(tmp_path):
    repo = _mkrepo(tmp_path)
    state = tmp_path / "state"
    for i in range(1, 21):
        (repo / f"f{i}.txt").write_text("x\n")
    _record(repo, state)
    subprocess.run(["touch", *[str(repo / f"f{i}.txt") for i in range(1, 21)],
                    str(repo / "a.txt")], check=True)
    out = _warns("git commit -m x", repo, state)
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "and 11 more" in ctx or "and 10 more" in ctx   # 21 stale, capped list -> "and N more"


def test_carriage_return_path_stays_silent_when_covered(tmp_path):
    """Without -z, git C-quotes a path holding a carriage return (e.g. "we\\rird.txt"), which
    matches nothing on disk -- check() would then call the list unusable and warn on every
    commit regardless of review coverage. -z lets the raw byte through instead."""
    repo = _mkrepo(tmp_path)
    state = tmp_path / "state"
    cr_name = "we\rird.txt"
    (repo / cr_name).write_text("x\n")
    subprocess.run(["git", "-C", str(repo), "add", cr_name], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "carriage-return path"], check=True)
    (repo / cr_name).write_text("changed\n")
    _record(repo, state)
    # every candidate must predate the record, or an unrelated stale file supplies the warning
    subprocess.run(["touch", "-t", "200001010000", str(repo / "a.txt"), str(repo / cr_name)], check=True)
    assert "additionalContext" not in _warns("git commit -m x", repo, state)


@pytest.mark.skipif(
    sys.platform == "darwin",
    reason="APFS rejects a filename with an invalid UTF-8 byte outright (OSError EILSEQ) -- "
           "the scenario this test targets can only occur on a filesystem that allows it "
           "(most Linux filesystems); nothing to reproduce on macOS.",
)
def test_invalid_utf8_path_stays_silent_when_covered(tmp_path):
    """A filename with a genuinely invalid UTF-8 byte must decode the SAME way here and in
    lib/review_marker.py's _tracked_deletions (surrogateescape on both sides) -- decoding with
    errors="replace" instead collapses the byte to U+FFFD, a different string than the
    deletion set's, and check() would then call the file list unusable on every commit."""
    repo = _mkrepo(tmp_path)
    state = tmp_path / "state"
    raw_name = b"bad\xffname.txt"
    bad_path = repo / os.fsdecode(raw_name)
    bad_path.write_bytes(b"x\n")
    subprocess.run(["git", "-C", str(repo), "add", "--", os.fsdecode(raw_name)], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "invalid-utf8 path"], check=True)
    bad_path.write_bytes(b"changed\n")
    _record(repo, state)
    subprocess.run(["touch", "-t", "200001010000", str(repo / "a.txt"), str(bad_path)], check=True)
    assert "additionalContext" not in _warns("git commit -m x", repo, state)


def test_hook_leaves_git_index_untouched(tmp_path):
    """Porcelain `git diff` refreshes a stat-dirty entry and writes .git/index back through
    index.lock, even under GIT_OPTIONAL_LOCKS=0 -- in a shared checkout that collides with
    another session's `git add`/`commit`. The hook must only read."""
    repo = _mkrepo(tmp_path)
    (repo / "b.txt").write_text("b\n")
    subprocess.run(["git", "-C", str(repo), "add", "b.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "b"], check=True)
    later = time.time() + 100
    os.utime(repo / "b.txt", (later, later))   # stat-dirty, content unchanged
    idx = repo / ".git" / "index"
    before = (idx.stat().st_ino, idx.stat().st_mtime_ns)
    # PATH passed through: _warns' bare env would pin /usr/bin/git via os.defpath
    out = _warns("git commit -m x", repo, tmp_path / "state", extra_env={"PATH": os.environ["PATH"]})
    after = (idx.stat().st_ino, idx.stat().st_mtime_ns)
    assert "additionalContext" in out   # the hook really reached the listing
    assert before == after


def _clean_repo(tmp_path, name="clean"):
    r = tmp_path / name
    r.mkdir()
    subprocess.run(["git", "-C", str(r), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(r), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(r), "config", "user.name", "t"], check=True)
    (r / "c.txt").write_text("c\n")
    subprocess.run(["git", "-C", str(r), "add", "c.txt"], check=True)
    subprocess.run(["git", "-C", str(r), "commit", "-qm", "c"], check=True)
    return r


def test_commit_after_assignment_line_triggers(tmp_path):
    """simo-dev:commit puts `W=...` on its own first line; the commit sits on line 3.
    Silent until fixed: on 2026-10-12 an unreviewed edit was committed with no reminder."""
    repo = _mkrepo(tmp_path)
    clean = _clean_repo(tmp_path)
    cmd = f'W="{repo}"\ngit -C "$W" add -- a.txt\ngit -C "$W" commit -q -m x'
    assert "additionalContext" in _warns(cmd, clean, tmp_path / "state")


def test_same_line_assignment_with_chained_add_triggers(tmp_path):
    repo = _mkrepo(tmp_path)
    clean = _clean_repo(tmp_path)
    cmd = f'W="{repo}"; R=/x; git -C "$W" add a.txt && git -C "$W" commit -m x'
    assert "additionalContext" in _warns(cmd, clean, tmp_path / "state")


def test_dollar_var_resolves_to_assignment_not_payload_cwd(tmp_path):
    repo = _mkrepo(tmp_path)          # dirty, would warn
    clean = _clean_repo(tmp_path)     # nothing to commit, stays silent
    cmd = f'W="{clean}"; git -C "$W" commit -m x'
    assert "additionalContext" not in _warns(cmd, repo, tmp_path / "state")


def test_braced_var_resolves(tmp_path):
    repo = _mkrepo(tmp_path)
    clean = _clean_repo(tmp_path)
    cmd = f'W="{repo}"; git -C "${{W}}" commit -m x'
    assert "additionalContext" in _warns(cmd, clean, tmp_path / "state")


def test_cd_var_on_previous_line_resolves(tmp_path):
    repo = _mkrepo(tmp_path)
    clean = _clean_repo(tmp_path)
    cmd = f'W="{repo}"\ncd "$W"\ngit commit -m x'
    assert "additionalContext" in _warns(cmd, clean, tmp_path / "state")


def test_unresolvable_var_falls_back_to_payload_cwd(tmp_path):
    repo = _mkrepo(tmp_path)
    assert "additionalContext" in _warns('git -C "$NOPE" commit -m x', repo, tmp_path / "state")


def test_heredoc_body_decoy_cannot_retarget(tmp_path):
    repo = _mkrepo(tmp_path)
    clean = _clean_repo(tmp_path)
    cmd = f"cat <<'EOF' >/dev/null\ngit -C {clean} commit\nEOF\ngit -C {repo} commit -m x"
    assert "additionalContext" in _warns(cmd, clean, tmp_path / "state")


def test_multiline_quoted_decoy_cannot_retarget(tmp_path):
    repo = _mkrepo(tmp_path)
    clean = _clean_repo(tmp_path)
    cmd = f'echo "note\ngit -C {clean} commit\n" >/dev/null; git -C {repo} commit -m x'
    assert "additionalContext" in _warns(cmd, clean, tmp_path / "state")


def test_commit_only_inside_heredoc_stays_silent(tmp_path):
    repo = _mkrepo(tmp_path)
    cmd = "cat <<'EOF' > notes.txt\ngit commit -m x\nEOF"
    assert "additionalContext" not in _warns(cmd, repo, tmp_path / "state")


def test_heredoc_commit_message_body_is_ignored(tmp_path):
    repo = _mkrepo(tmp_path)
    clean = _clean_repo(tmp_path)
    cmd = f"git -C {repo} commit -q -F - <<'EOF'\nsubject\n\ngit -C {clean} commit\nEOF"
    assert "additionalContext" in _warns(cmd, clean, tmp_path / "state")


def test_nested_quote_in_command_substitution_still_triggers(tmp_path):
    """`_quoted_at` does not track quotes nested in "$(...)": the apostrophe opens a
    phantom quote. The newline-blind matcher is the fallback, so this stays loud."""
    repo = _mkrepo(tmp_path)
    cmd = 'X="$(echo "it\'s")"; git commit -m x'
    assert "additionalContext" in _warns(cmd, repo, tmp_path / "state")


def test_fallback_path_still_resolves_cd_after_phantom_quote(tmp_path):
    repo = _mkrepo(tmp_path)
    clean = _clean_repo(tmp_path)
    cmd = f'X="$(echo "it\'s")"; cd {repo} && git commit -m x'
    assert "additionalContext" in _warns(cmd, clean, tmp_path / "state")


def test_fallback_path_ignores_later_decoy_cd(tmp_path):
    repo = _mkrepo(tmp_path)
    clean = _clean_repo(tmp_path)
    cmd = f'cd {repo} && cat <<EOF\ncd {clean}\nEOF\nX="$(echo "it\'s")"; git commit -m x'
    assert "additionalContext" in _warns(cmd, clean, tmp_path / "state")


def test_fallback_path_ignores_assignment_inside_heredoc_body(tmp_path):
    repo = _mkrepo(tmp_path)
    clean = _clean_repo(tmp_path)
    cmd = f'W={repo}\ncat <<EOF\nW={clean}\nEOF\nX="$(echo "it\'s")"; git -C "$W" commit -m x'
    assert "additionalContext" in _warns(cmd, clean, tmp_path / "state")


def test_cd_hidden_by_closed_phantom_quote_still_resolves(tmp_path):
    repo = _mkrepo(tmp_path)
    clean = _clean_repo(tmp_path)
    cmd = f'X="$(echo "it\'s")"; cd {repo} # don\'t\ntrue; git commit -m x'
    assert "additionalContext" in _warns(cmd, clean, tmp_path / "state")


def test_reminder_names_mr_followup_exception(tmp_path):
    repo = _mkrepo(tmp_path)
    out = _warns("git commit -m x", repo, tmp_path / "state")
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "simo-dev:mr-followup" in ctx
    assert "own commits" in ctx
