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
    monkeypatch.setattr(cdt, "default_base", lambda repo: "master")
    monkeypatch.setattr(cdt, "_git", lambda repo, args: "sha" if args[0] == "rev-parse" else "")
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
    # No branch ref left for the ticket: only a commit message still names
    # it, so the lookup falls through to the log grep.
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
    monkeypatch.setattr(cdt, "merged_in_base", lambda repo, key, base: "")
    monkeypatch.setattr(cdt, "resolve_diff", lambda repo, key, base: "diff --git a/x b/x\n+new shape\n")
    rc = cdt.main(["PROJ-1766", "--repo", "/x"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.splitlines()[0] == "resolved-with-diff"
    assert "new shape" in out


def test_main_resolved_no_diff_found(monkeypatch, capsys):
    monkeypatch.setattr(cdt, "_jira_fields", lambda key: {"status": {"name": "Closed"}})
    monkeypatch.setattr(cdt, "default_base", lambda repo: "master")
    monkeypatch.setattr(cdt, "merged_in_base", lambda repo, key, base: "")
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


def test_merged_in_base_finds_squash_commit_on_base(tmp_path):
    clone = _make_repo(tmp_path)
    assert cdt.merged_in_base(str(clone), "PROJ-1766", "master") != ""


def test_merged_in_base_finds_key_at_line_start(tmp_path):
    # The most common commit convention: the subject opens with the key.
    clone = _make_repo(tmp_path)
    (clone / "lead.py").write_text("lead\n")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "PROJ-5555 collapse search")
    _git(clone, "push", "-q", "origin", "master")
    _git(clone, "fetch", "-q", "origin")
    assert cdt.merged_in_base(str(clone), "PROJ-5555", "master") != ""


def test_main_done_ticket_uses_base_hit_over_chain(tmp_path, monkeypatch, capsys):
    """A Done ticket with a key-naming commit on the base shows that commit's
    diff, never the resolve_diff chain, and prints no merged-into note."""
    clone = _make_repo(tmp_path)
    monkeypatch.setattr(cdt, "_jira_fields", lambda key: {"status": {"name": "Done"}})
    monkeypatch.setattr(cdt, "resolve_diff", lambda repo, key, base: "diff --git a/chain b/chain\n")
    rc = cdt.main(["PROJ-1766", "--repo", str(clone)])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.splitlines()[0] == "resolved-with-diff"
    assert "merged into" not in out
    assert "feature.py" in out
    assert "a/chain" not in out


def test_merged_in_base_respects_numeric_boundary(tmp_path):
    # PROJ-176 is a prefix of PROJ-1766: a plain substring grep would match.
    clone = _make_repo(tmp_path)
    assert cdt.merged_in_base(str(clone), "PROJ-176", "master") == ""


def test_merged_in_base_respects_left_boundary(tmp_path):
    # SUBPROJ-2020 ends with PROJ-2020: a right-boundary-only grep would match.
    clone = _make_repo(tmp_path)
    (clone / "sub.py").write_text("sub\n")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "chore: SUBPROJ-2020 unrelated")
    _git(clone, "push", "-q", "origin", "master")
    _git(clone, "fetch", "-q", "origin")
    assert cdt.merged_in_base(str(clone), "PROJ-2020", "master") == ""


def test_merged_in_base_left_boundary_excludes_underscore(tmp_path):
    # Project keys may carry `_`: SUB_PROJ-2121 is a different ticket.
    clone = _make_repo(tmp_path)
    (clone / "us.py").write_text("us\n")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "chore: SUB_PROJ-2121 unrelated")
    _git(clone, "push", "-q", "origin", "master")
    _git(clone, "fetch", "-q", "origin")
    assert cdt.merged_in_base(str(clone), "PROJ-2121", "master") == ""


def test_merged_in_base_ignores_unmerged_branch(tmp_path):
    """A commit naming the key that only lives on a pushed, unmerged branch is
    not "merged" -- unlike find_log_diff's --all grep."""
    clone = _make_repo(tmp_path)
    _git(clone, "checkout", "-q", "-b", "feat/proj-3333-wip")
    (clone / "wip.py").write_text("wip\n")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "feat: PROJ-3333 wip")
    _git(clone, "push", "-q", "origin", "feat/proj-3333-wip")
    _git(clone, "fetch", "-q", "origin")
    assert cdt.merged_in_base(str(clone), "PROJ-3333", "master") == ""


def test_main_ticket_not_done_but_merged_reports_diff(tmp_path, monkeypatch, capsys):
    """Ticket still in a post-merge, non-terminal status (e.g. Testing) while its
    code already sits on the base: the shape check must run, so this is
    resolved-with-diff, not open."""
    clone = _make_repo(tmp_path)
    monkeypatch.setattr(cdt, "_jira_fields", lambda key: {"status": {"name": "Testing"}})
    rc = cdt.main(["PROJ-1766", "--repo", str(clone)])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.splitlines()[0] == "resolved-with-diff"
    assert out.splitlines()[1] == "status: Testing (merged into master)"
    assert "feature.py" in out


def test_main_ticket_not_done_and_unmerged_stays_open(tmp_path, monkeypatch, capsys):
    clone = _make_repo(tmp_path)
    monkeypatch.setattr(cdt, "_jira_fields", lambda key: {"status": {"name": "In Progress"}})
    rc = cdt.main(["PROJ-4444", "--repo", str(clone)])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.splitlines() == ["open", "status: In Progress"]


def test_main_merged_diff_ignores_newer_unmerged_branch(tmp_path, monkeypatch, capsys):
    """After the squash-merge, a NEWER unmerged task-intake-named branch
    (find_branch_diff's pick): the diff must be the merged commit's
    (feature.py), never the unmerged work (fix.py). The future committer date
    makes "newer" deterministic: both refs would otherwise share one second
    and the alphabetical tie-break would pick feat/ by luck."""
    clone = _make_repo(tmp_path)
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2099-01-01T00:00:00")
    _git(clone, "checkout", "-q", "-b", "fix/proj-1766-followup")
    (clone / "fix.py").write_text("fix\n")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "address QA feedback")
    _git(clone, "push", "-q", "origin", "fix/proj-1766-followup")
    _git(clone, "checkout", "-q", "master")
    monkeypatch.setattr(cdt, "_jira_fields", lambda key: {"status": {"name": "Testing"}})
    rc = cdt.main(["PROJ-1766", "--repo", str(clone)])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.splitlines()[0] == "resolved-with-diff"
    assert "feature.py" in out
    assert "fix.py" not in out


def test_main_merged_diff_ignores_unmerged_key_commit(tmp_path, monkeypatch, capsys):
    """Source branch deleted (so find_branch_diff finds nothing) plus a NEWER
    key-naming commit on an unmerged branch (find_log_diff's --all pick): the
    diff must be the merged commit's (feature.py), never wip.py."""
    clone = _make_repo(tmp_path)
    _git(clone, "push", "-q", "origin", "--delete", "feat/proj-1766-collapse-search")
    _git(clone, "branch", "-D", "feat/proj-1766-collapse-search")
    _git(clone, "fetch", "-q", "--prune", "origin")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2099-01-01T00:00:00")
    _git(clone, "checkout", "-q", "-b", "wip/other")
    (clone / "wip.py").write_text("wip\n")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "PROJ-1766 follow-up wip")
    _git(clone, "push", "-q", "origin", "wip/other")
    _git(clone, "checkout", "-q", "master")
    monkeypatch.setattr(cdt, "_jira_fields", lambda key: {"status": {"name": "Testing"}})
    rc = cdt.main(["PROJ-1766", "--repo", str(clone)])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.splitlines()[0] == "resolved-with-diff"
    assert "feature.py" in out
    assert "wip.py" not in out


def test_main_merged_via_no_ff_merge_commit_lowercase_branch_name(tmp_path, monkeypatch, capsys):
    """GitLab/GitHub "merge commit" strategy: the key appears only lowercased in
    the merge message's branch name, none of the branch commits names it. The
    -i grep must hit the merge commit, whose first-parent diff is the whole MR."""
    clone = _make_repo(tmp_path)
    _git(clone, "checkout", "-q", "-b", "feat/proj-2222-y")
    (clone / "other.py").write_text("y\n")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "add other")
    _git(clone, "checkout", "-q", "master")
    _git(clone, "merge", "-q", "--no-ff", "feat/proj-2222-y", "-m", "Merge branch 'feat/proj-2222-y' into 'master'")
    _git(clone, "push", "-q", "origin", "master")
    monkeypatch.setattr(cdt, "_jira_fields", lambda key: {"status": {"name": "Testing"}})
    rc = cdt.main(["PROJ-2222", "--repo", str(clone)])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.splitlines()[:2] == ["resolved-with-diff", "status: Testing (merged into master)"]
    assert "other.py" in out


def test_main_merged_via_merge_commit_picks_whole_mr(tmp_path, monkeypatch, capsys):
    """Merge-commit strategy where the branch commits name the key too: the
    newest hit must be the merge commit, whose first-parent diff is the whole
    MR (both files), not one branch commit."""
    clone = _make_repo(tmp_path)
    _git(clone, "checkout", "-q", "-b", "feat/proj-2323-z")
    for name in ("a.py", "b.py"):
        (clone / name).write_text(f"{name}\n")
        _git(clone, "add", "-A")
        _git(clone, "commit", "-qm", f"feat: PROJ-2323 add {name}")
    _git(clone, "checkout", "-q", "master")
    _git(clone, "merge", "-q", "--no-ff", "feat/proj-2323-z", "-m", "Merge branch 'feat/proj-2323-z' into 'master'")
    _git(clone, "push", "-q", "origin", "master")
    monkeypatch.setattr(cdt, "_jira_fields", lambda key: {"status": {"name": "Testing"}})
    rc = cdt.main(["PROJ-2323", "--repo", str(clone)])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.splitlines()[:2] == ["resolved-with-diff", "status: Testing (merged into master)"]
    assert "a.py" in out and "b.py" in out


def test_main_merged_branch_side_hit_is_resolved(tmp_path, monkeypatch, capsys):
    """Merge commit whose own message omits the key, branch commit names it:
    the grep walks the branch side (no --first-parent), so the ticket is still
    reported as merged (with a partial diff) instead of silently open."""
    clone = _make_repo(tmp_path)
    _git(clone, "checkout", "-q", "-b", "topic")
    (clone / "side.py").write_text("side\n")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "PROJ-8888 side change")
    _git(clone, "checkout", "-q", "master")
    _git(clone, "merge", "-q", "--no-ff", "topic", "-m", "Merge topic")
    _git(clone, "push", "-q", "origin", "master")
    monkeypatch.setattr(cdt, "_jira_fields", lambda key: {"status": {"name": "Testing"}})
    rc = cdt.main(["PROJ-8888", "--repo", str(clone)])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.splitlines()[:2] == ["resolved-with-diff", "status: Testing (merged into master)"]
    assert "side.py" in out


def test_main_base_flag_selects_integration_branch(tmp_path, monkeypatch, capsys):
    """origin/HEAD is master but the dependency merged into develop only: --base
    develop sees it, while the default base would still say open."""
    clone = _make_repo(tmp_path)
    _git(clone, "checkout", "-q", "-b", "develop")
    (clone / "dev.py").write_text("dev\n")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "squash-merge PROJ-6060")
    _git(clone, "push", "-q", "origin", "develop")
    _git(clone, "checkout", "-q", "master")
    monkeypatch.setattr(cdt, "_jira_fields", lambda key: {"status": {"name": "Testing"}})
    assert cdt.main(["PROJ-6060", "--repo", str(clone), "--base", "develop"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[:2] == ["resolved-with-diff", "status: Testing (merged into develop)"]
    assert "dev.py" in out
    assert cdt.main(["PROJ-6060", "--repo", str(clone)]) == 0
    assert capsys.readouterr().out.splitlines() == ["open", "status: Testing"]


def test_main_unknown_base_is_unverifiable_not_open(tmp_path, monkeypatch, capsys):
    clone = _make_repo(tmp_path)
    monkeypatch.setattr(cdt, "_jira_fields", lambda key: {"status": {"name": "Testing"}})
    assert cdt.main(["PROJ-6060", "--repo", str(clone), "--base", "no-such-branch"]) == 0
    assert capsys.readouterr().out.splitlines() == ["unverifiable", "status: Testing (origin/no-such-branch not found)"]


def test_main_local_only_base_is_unverifiable(tmp_path, monkeypatch, capsys):
    """A base branch that exists locally but was never pushed: the guard must
    read origin/<base>, not the local ref."""
    clone = _make_repo(tmp_path)
    _git(clone, "branch", "local-only")
    monkeypatch.setattr(cdt, "_jira_fields", lambda key: {"status": {"name": "Testing"}})
    assert cdt.main(["PROJ-6060", "--repo", str(clone), "--base", "local-only"]) == 0
    assert capsys.readouterr().out.splitlines() == ["unverifiable", "status: Testing (origin/local-only not found)"]


def test_main_merged_only_on_remote_seen_via_fetch(tmp_path, monkeypatch, capsys):
    """Stale clone: the MR merged on the forge and local master was never
    pulled. Only fetch + origin/<base> see it; the local <base> would still
    say open."""
    clone = _make_repo(tmp_path)
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", "-b", "master", str(tmp_path / "up"), str(other)], check=True)
    _git(other, "config", "user.email", "t@t")
    _git(other, "config", "user.name", "t")
    (other / "remote.py").write_text("r\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-qm", "PROJ-7777 land on forge")
    _git(other, "push", "-q", "origin", "master")
    monkeypatch.setattr(cdt, "_jira_fields", lambda key: {"status": {"name": "Testing"}})
    rc = cdt.main(["PROJ-7777", "--repo", str(clone)])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.splitlines()[:2] == ["resolved-with-diff", "status: Testing (merged into master)"]
    assert "remote.py" in out


def test_main_merged_but_empty_diff_keeps_merged_note(monkeypatch, capsys):
    monkeypatch.setattr(cdt, "_jira_fields", lambda key: {"status": {"name": "Testing"}})
    monkeypatch.setattr(cdt, "default_base", lambda repo: "master")
    monkeypatch.setattr(cdt, "_git", lambda repo, args: "")
    monkeypatch.setattr(cdt, "merged_in_base", lambda repo, key, base: "abc123")
    # An empty hit-commit diff must not fall back to the chain, which can pick unmerged work.
    monkeypatch.setattr(cdt, "resolve_diff", lambda repo, key, base: "diff --git a/unmerged b/unmerged\n")
    rc = cdt.main(["PROJ-1766", "--repo", "/x"])
    assert rc == 0
    assert capsys.readouterr().out.splitlines() == ["resolved-no-diff-found", "status: Testing (merged into master)"]


def test_main_fetches_before_merged_check_on_open_status(monkeypatch):
    """A stale clone would not see the merge: the fetch must precede
    merged_in_base on a non-terminal status too."""
    calls = []
    monkeypatch.setattr(cdt, "_jira_fields", lambda key: {"status": {"name": "Testing"}})
    monkeypatch.setattr(cdt, "default_base", lambda repo: "master")
    monkeypatch.setattr(cdt, "_git", lambda repo, args: (calls.append(args), "")[1])
    monkeypatch.setattr(cdt, "merged_in_base", lambda repo, key, base: (calls.append("merged_in_base"), "")[1])
    cdt.main(["PROJ-1766", "--repo", "/x"])
    assert calls.index(["fetch", "origin", "--quiet"]) < calls.index("merged_in_base")


def test_find_log_diff_respects_numeric_boundary(tmp_path):
    # PROJ-176 is a prefix of PROJ-1766: the --all grep must not match it.
    clone = _make_repo(tmp_path)
    assert cdt.find_log_diff(str(clone), "PROJ-176") == ""


def test_main_done_diff_ignores_newer_unmerged_branch(tmp_path, monkeypatch, capsys):
    """Done ticket, squash-merged, plus a NEWER unmerged task-intake-named
    branch (find_branch_diff's pick): the diff must be the merged commit's
    (feature.py), never fix.py."""
    clone = _make_repo(tmp_path)
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2099-01-01T00:00:00")
    _git(clone, "checkout", "-q", "-b", "fix/proj-1766-followup")
    (clone / "fix.py").write_text("fix\n")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "address QA feedback")
    _git(clone, "push", "-q", "origin", "fix/proj-1766-followup")
    _git(clone, "checkout", "-q", "master")
    monkeypatch.setattr(cdt, "_jira_fields", lambda key: {"status": {"name": "Done"}})
    rc = cdt.main(["PROJ-1766", "--repo", str(clone)])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.splitlines()[0] == "resolved-with-diff"
    assert "merged into" not in out
    assert "feature.py" in out
    assert "fix.py" not in out


def test_main_done_diff_ignores_unmerged_key_commit(tmp_path, monkeypatch, capsys):
    """Done ticket, source branch deleted, plus a NEWER key-naming commit on an
    unmerged branch (find_log_diff's --all pick): no wip.py in the diff."""
    clone = _make_repo(tmp_path)
    _git(clone, "push", "-q", "origin", "--delete", "feat/proj-1766-collapse-search")
    _git(clone, "branch", "-D", "feat/proj-1766-collapse-search")
    _git(clone, "fetch", "-q", "--prune", "origin")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2099-01-01T00:00:00")
    _git(clone, "checkout", "-q", "-b", "wip/other")
    (clone / "wip.py").write_text("wip\n")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "PROJ-1766 follow-up wip")
    _git(clone, "push", "-q", "origin", "wip/other")
    _git(clone, "checkout", "-q", "master")
    monkeypatch.setattr(cdt, "_jira_fields", lambda key: {"status": {"name": "Done"}})
    rc = cdt.main(["PROJ-1766", "--repo", str(clone)])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.splitlines()[0] == "resolved-with-diff"
    assert "feature.py" in out
    assert "wip.py" not in out


def test_main_done_without_base_hit_uses_chain(tmp_path, monkeypatch, capsys):
    """No key-naming commit on the base (e.g. the local clone never saw the
    merging branch): a Done ticket still falls back to the resolve_diff chain."""
    clone = _make_repo(tmp_path)
    monkeypatch.setattr(cdt, "_jira_fields", lambda key: {"status": {"name": "Done"}})
    monkeypatch.setattr(cdt, "resolve_diff", lambda repo, key, base: "diff --git a/chain b/chain\n")
    rc = cdt.main(["PROJ-9191", "--repo", str(clone)])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.splitlines()[0] == "resolved-with-diff"
    assert "a/chain" in out


def test_main_done_unknown_base_uses_chain(tmp_path, monkeypatch, capsys):
    """A Done ticket with no origin/<base> keeps today's chain, never unverifiable."""
    clone = _make_repo(tmp_path)
    monkeypatch.setattr(cdt, "_jira_fields", lambda key: {"status": {"name": "Done"}})
    monkeypatch.setattr(cdt, "resolve_diff", lambda repo, key, base: "diff --git a/chain b/chain\n")
    rc = cdt.main(["PROJ-1766", "--repo", str(clone), "--base", "no-such-branch"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.splitlines()[0] == "resolved-with-diff"
    assert "a/chain" in out


def test_main_done_hit_with_empty_diff_does_not_fall_back(monkeypatch, capsys):
    monkeypatch.setattr(cdt, "_jira_fields", lambda key: {"status": {"name": "Done"}})
    monkeypatch.setattr(cdt, "default_base", lambda repo: "master")
    monkeypatch.setattr(cdt, "_git", lambda repo, args: "")
    monkeypatch.setattr(cdt, "merged_in_base", lambda repo, key, base: "abc123")
    monkeypatch.setattr(cdt, "resolve_diff", lambda repo, key, base: "diff --git a/unmerged b/unmerged\n")
    rc = cdt.main(["PROJ-1766", "--repo", "/x"])
    assert rc == 0
    assert capsys.readouterr().out.splitlines() == ["resolved-no-diff-found"]


def test_find_forge_diff_respects_numeric_boundary(monkeypatch):
    """PROJ-176 is a prefix of PROJ-1766: an MR titled with PROJ-1766 is not
    PROJ-176's diff."""

    def run(args, **kwargs):
        if args[:3] == ["glab", "mr", "list"]:
            return subprocess.CompletedProcess(
                args, 0, stdout=json.dumps([{"iid": 1, "title": "feat: PROJ-1766 collapse", "description": ""}]), stderr=""
            )
        if args[:3] == ["glab", "mr", "diff"]:
            return subprocess.CompletedProcess(args, 0, stdout="diff --git a/wrong b/wrong\n", stderr="")
        if args[:3] == ["gh", "pr", "list"]:
            return subprocess.CompletedProcess(args, 0, stdout="[]", stderr="")
        raise AssertionError(f"unexpected call: {args}")

    monkeypatch.setattr(subprocess, "run", run)
    assert cdt.find_forge_diff("/repo", "PROJ-176") == ""
