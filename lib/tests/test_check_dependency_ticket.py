"""Tests for lib/check_dependency_ticket.py (dependency-ticket freshness data collector)."""
import importlib.util
import json
import subprocess
from pathlib import Path

LIB = Path(__file__).parent.parent / "check_dependency_ticket.py"

spec = importlib.util.spec_from_file_location("check_dependency_ticket", LIB)
cdt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cdt)


def test_ticket_status_reads_jira_field(monkeypatch):
    monkeypatch.setattr(cdt, "_jira_fields", lambda key: {"status": {"name": "In Progress"}})
    assert cdt.ticket_status("PROJ-1766") == "In Progress"


def test_ticket_status_none_on_cli_error(monkeypatch):
    def boom(key):
        raise subprocess.CalledProcessError(1, ["jira"], stderr="not found")
    monkeypatch.setattr(cdt, "_jira_fields", boom)
    assert cdt.ticket_status("PROJ-1766") is None


def test_ticket_status_none_on_missing_binary(monkeypatch):
    def boom(key):
        raise FileNotFoundError("jira")
    monkeypatch.setattr(cdt, "_jira_fields", boom)
    assert cdt.ticket_status("PROJ-1766") is None


def test_ticket_status_none_on_non_dict_payload(monkeypatch):
    # A jira-cli error path can exit 0 with e.g. `null` or a bare array --
    # cdt._jira_fields' own `.get("fields", {})` only defaults when the
    # key is ABSENT, so a present-but-non-dict "fields" value (or the field
    # holding a non-dict itself) reaches here verbatim. Must degrade to
    # unverifiable, not crash with AttributeError.
    monkeypatch.setattr(cdt, "_jira_fields", lambda key: None)
    assert cdt.ticket_status("PROJ-1766") is None


def test_main_open_ticket_prints_status(monkeypatch, capsys):
    monkeypatch.setattr(cdt, "_jira_fields", lambda key: {"status": {"name": "In Progress"}})
    rc = cdt.main(["PROJ-1766", "--repo", "/x"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.splitlines()[0] == "open"
    assert "In Progress" in out


def test_main_unverifiable_on_jira_failure(monkeypatch, capsys):
    def boom(key):
        raise subprocess.CalledProcessError(1, ["jira"], stderr="401")
    monkeypatch.setattr(cdt, "_jira_fields", boom)
    rc = cdt.main(["PROJ-1766", "--repo", "/x"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "unverifiable"


def test_done_statuses_contains_expected_values():
    # The terminal JIRA status names this script's blocker check relies on.
    assert cdt._DONE_STATUSES is not None
    assert "done" in cdt._DONE_STATUSES


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _make_repo(tmp_path):
    """origin (bare) + clone on master, with a feat/proj-1766-x branch
    squash-merged into master (GitLab's default MR merge mode) -- the
    branch ref is left in place and is NOT an ancestor of master's new
    tip, same as a real squash-merge."""
    up, clone = tmp_path / "up", tmp_path / "clone"
    subprocess.run(["git", "init", "-q", "--bare", str(up)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "master", str(clone)], check=True)
    _git(clone, "config", "user.email", "t@t")
    _git(clone, "config", "user.name", "t")
    (clone / "base.py").write_text("a\n")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "init")
    _git(clone, "remote", "add", "origin", str(up))
    _git(clone, "push", "-q", "origin", "master")
    _git(clone, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/master")
    _git(clone, "checkout", "-q", "-b", "feat/proj-1766-collapse-search")
    (clone / "feature.py").write_text("new shape\n")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "feat: PROJ-1766 collapse SearchAsync overload")
    _git(clone, "push", "-q", "origin", "feat/proj-1766-collapse-search")
    _git(clone, "checkout", "-q", "master")
    _git(clone, "merge", "-q", "--squash", "feat/proj-1766-collapse-search")
    _git(clone, "commit", "-qm", "squash-merge PROJ-1766")
    _git(clone, "push", "-q", "origin", "master")
    _git(clone, "update-ref", "refs/remotes/origin/master", "refs/heads/master")
    return clone


def test_find_branch_diff_matches_task_intake_naming(tmp_path):
    clone = _make_repo(tmp_path)
    diff = cdt.find_branch_diff(str(clone), "PROJ-1766", "master")
    assert "feature.py" in diff
    assert "new shape" in diff


def test_find_branch_diff_no_match_returns_empty(tmp_path):
    clone = _make_repo(tmp_path)
    assert cdt.find_branch_diff(str(clone), "PROJ-9999", "master") == ""


def test_find_log_diff_falls_back_to_commit_grep(tmp_path):
    clone = _make_repo(tmp_path)
    # Simulate GitLab's force_remove_source_branch: the branch ref is gone,
    # only the merge commit's message still names the ticket.
    _git(clone, "push", "-q", "origin", "--delete", "feat/proj-1766-collapse-search")
    _git(clone, "branch", "-D", "feat/proj-1766-collapse-search")
    _git(clone, "fetch", "-q", "--prune", "origin")
    assert cdt.find_branch_diff(str(clone), "PROJ-1766", "master") == ""
    diff = cdt.find_log_diff(str(clone), "PROJ-1766")
    assert "feature.py" in diff


def test_find_log_diff_handles_merge_commit(tmp_path):
    """A clean 2-parent merge commit (non-squash --no-ff, or GitLab's "merge
    commit" strategy) is the realistic shape once a real merge (not a squash)
    is used -- `git show` prints no diff for it (git suppresses the combined
    diff when there are no conflicts), so find_log_diff must diff against the
    first parent instead."""
    clone = _make_repo(tmp_path)
    _git(clone, "checkout", "-q", "-b", "feat/proj-2222-y")
    (clone / "other.py").write_text("y\n")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "feat: PROJ-2222 add other")
    _git(clone, "checkout", "-q", "master")
    _git(clone, "merge", "-q", "--no-ff", "feat/proj-2222-y", "-m", "Merge branch 'feat/proj-2222-y' PROJ-2222")
    diff = cdt.find_log_diff(str(clone), "PROJ-2222")
    assert "other.py" in diff
    assert "+y" in diff


def test_find_forge_diff_tries_glab_then_gh(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append((args[0], kwargs.get("cwd")))
        if args[0] == "glab" and args[1:4] == ["mr", "list", "--search"]:
            raise subprocess.CalledProcessError(1, args, stderr="no MR")
        if args[0] == "gh" and args[1:3] == ["pr", "list"]:
            return subprocess.CompletedProcess(
                args, 0, stdout=json.dumps([{"number": 7, "title": "fix: PROJ-1766 thing", "body": ""}]), stderr=""
            )
        if args[0] == "gh" and args[1:3] == ["pr", "diff"]:
            return subprocess.CompletedProcess(args, 0, stdout="diff --git a/x b/x\n", stderr="")
        raise AssertionError(f"unexpected call: {args}")

    monkeypatch.setattr(subprocess, "run", run)
    diff = cdt.find_forge_diff("/repo", "PROJ-1766")
    assert diff == "diff --git a/x b/x\n"
    assert [c[0] for c in calls] == ["glab", "gh", "gh"]
    assert all(c[1] == "/repo" for c in calls)  # every forge call runs against the passed repo, never the ambient cwd


def test_find_forge_diff_resolves_mr_by_search_then_diffs_by_iid(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[:3] == ["glab", "mr", "list"]:
            return subprocess.CompletedProcess(
                args, 0, stdout=json.dumps([{"iid": 42, "title": "feat: PROJ-1766 collapse search", "description": ""}]), stderr=""
            )
        if args[:3] == ["glab", "mr", "diff"]:
            return subprocess.CompletedProcess(args, 0, stdout="diff --git a/y b/y\n", stderr="")
        raise AssertionError(f"unexpected call: {args}")

    monkeypatch.setattr(subprocess, "run", run)
    diff = cdt.find_forge_diff("/repo", "PROJ-1766")
    assert diff == "diff --git a/y b/y\n"
    assert calls[1] == ["glab", "mr", "diff", "42", "--raw"]


def test_find_forge_diff_no_mr_found_returns_empty(monkeypatch):
    def run(args, **kwargs):
        if "list" in args:
            return subprocess.CompletedProcess(args, 0, stdout="[]", stderr="")
        raise AssertionError(f"unexpected call: {args}")

    monkeypatch.setattr(subprocess, "run", run)
    assert cdt.find_forge_diff("/repo", "PROJ-1766") == ""


def test_find_forge_diff_skips_unrelated_search_hit(monkeypatch):
    """glab/gh --search is a fuzzy full-text search -- a hit whose title and
    description never actually mention the ticket must not be treated as its
    diff, even if it's the only search result."""

    def run(args, **kwargs):
        if args[:3] == ["glab", "mr", "list"]:
            return subprocess.CompletedProcess(
                args, 0, stdout=json.dumps([{"iid": 99, "title": "unrelated MR", "description": "no mention here"}]), stderr=""
            )
        if args[:3] == ["gh", "pr", "list"]:
            return subprocess.CompletedProcess(
                args, 0, stdout=json.dumps([{"number": 5, "title": "also unrelated", "body": ""}]), stderr=""
            )
        raise AssertionError(f"unexpected call: {args}")

    monkeypatch.setattr(subprocess, "run", run)
    assert cdt.find_forge_diff("/repo", "PROJ-1766") == ""


def test_main_resolved_with_diff(monkeypatch, capsys):
    monkeypatch.setattr(cdt, "_jira_fields", lambda key: {"status": {"name": "Done"}})
    monkeypatch.setattr(cdt, "default_base", lambda repo: "master")
    monkeypatch.setattr(cdt, "resolve_diff", lambda repo, key, base: "diff --git a/x b/x\n+new shape\n")
    rc = cdt.main(["PROJ-1766", "--repo", "/x"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.splitlines()[0] == "resolved-with-diff"
    assert "new shape" in out


def test_main_resolved_no_diff_found(monkeypatch, capsys):
    monkeypatch.setattr(cdt, "_jira_fields", lambda key: {"status": {"name": "Closed"}})
    monkeypatch.setattr(cdt, "default_base", lambda repo: "master")
    monkeypatch.setattr(cdt, "resolve_diff", lambda repo, key, base: "")
    rc = cdt.main(["PROJ-1766", "--repo", "/x"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "resolved-no-diff-found"


def test_main_fetches_before_resolving_diff(monkeypatch):
    """A worktree resumed after time passed is exactly the case this script
    exists for -- stale local refs must not be mistaken for "no diff found"."""
    calls = []
    monkeypatch.setattr(cdt, "_jira_fields", lambda key: {"status": {"name": "Done"}})
    monkeypatch.setattr(cdt, "default_base", lambda repo: "master")
    monkeypatch.setattr(cdt, "_git", lambda repo, args: (calls.append(args), "")[1])
    monkeypatch.setattr(cdt, "resolve_diff", lambda repo, key, base: (calls.append("resolve_diff"), "")[1])
    cdt.main(["PROJ-1766", "--repo", "/x"])
    assert calls.index(["fetch", "origin", "--quiet"]) < calls.index("resolve_diff")
