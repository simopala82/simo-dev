"""lib/mr_followup.py: simo-dev:mr-followup step 1 (detect), step 6's base guard + round-1 skip check
(review-gate) and step 7 item 6's pre-push MR-state guard (push).

Every case and stop of mr-followup SKILL.md step 1 at aa3afaf8 is ported here before the script
existed, plus the round-1 skip block's executed tests that lived in test_review_marker.py. Each test
builds real git repos in tmp_path (bare origin + clone) and runs the script as the skill does; glab/gh
are stub binaries on PATH that log argv and cwd; a sibling script is stubbed only by copying lib/
into tmp_path and replacing it.
"""
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent
PLUGIN = LIB.parent
SCRIPT = LIB / "mr_followup.py"
DETECT = PLUGIN / "skills" / "mr-followup" / "lib" / "detect_remote.py"
FORGES = {"gitlab": "git@gitlab.example.com:g/r.git", "github": "git@github.com:o/r.git"}
GITLAB_URL = "https://gitlab.example.com/g/r/-/merge_requests/7"
GITHUB_URL = "https://github.com/o/r/pull/7"
MULTI = ("note", "warning", "ahead", "drops", "candidate")

NEXT_TOKEN = r"""
next_tok() {
  f="$S/seq"
  if [ -s "$f" ]; then t=$(head -n 1 "$f"); tail -n +2 "$f" > "$f.tmp"; mv "$f.tmp" "$f"; echo "$t"; else echo ok; fi
}
"""

GLAB_STUB = r"""#!/bin/sh
{ printf glab; printf ' [%s]' "$@"; printf ' @%s\n' "$(pwd -P)"; } >> "$STUB_LOG"
S="$STUB_DIR"
""" + NEXT_TOKEN + r"""
case "$1 $2" in
"mr view")
  case "$(next_tok)" in
  fail) echo "net/http: TLS handshake timeout" >&2; exit 1;;
  none) echo "no open merge request available for \"$3\"" >&2; exit 1;;
  esac
  ref=$3; case "$3" in ''|*[!0-9]*) ;; *) ref=$(cat "$S/head_branch");; esac
  src=$(cat "$S/source_branch" 2>/dev/null || printf '%s' "$ref")
  st=$(cat "$S/state" 2>/dev/null || echo opened)
  d=$(cat "$S/is_draft" 2>/dev/null || echo false)
  tb=$(cat "$S/target" 2>/dev/null || echo master)
  printf '{"source_branch": "%s", "target_branch": "%s", "iid": 7, "state": "%s", "draft": %s, "web_url": "%s"}\n' \
    "$src" "$tb" "$st" "$d" "https://gitlab.example.com/g/r/-/merge_requests/7";;
esac
exit 0
"""

GH_STUB = r"""#!/bin/sh
{ printf gh; printf ' [%s]' "$@"; printf ' @%s\n' "$(pwd -P)"; } >> "$STUB_LOG"
S="$STUB_DIR"
""" + NEXT_TOKEN + r"""
case "$1 $2" in
"pr view")
  case "$(next_tok)" in
  fail) echo "HTTP 502: Bad Gateway" >&2; exit 1;;
  none) echo "no pull requests found for branch \"$3\"" >&2; exit 1;;
  esac
  ref=$3; case "$3" in ''|*[!0-9]*) ;; *) ref=$(cat "$S/head_branch");; esac
  src=$(cat "$S/source_branch" 2>/dev/null || printf '%s' "$ref")
  st=$(cat "$S/state" 2>/dev/null || echo OPEN)
  d=$(cat "$S/is_draft" 2>/dev/null || echo false)
  n=7; case "$3" in ''|*[!0-9]*) ;; *) n=$3;; esac
  printf '{"number": %s, "state": "%s", "isDraft": %s, "baseRefName": "main", "headRefName": "%s", "url": "%s"}\n' \
    "$n" "$st" "$d" "$src" "https://github.com/o/r/pull/$n";;
"pr list")
  if [ -f "$S/list_fails" ]; then echo "HTTP 502" >&2; exit 1; fi
  cat "$S/pr_list" 2>/dev/null || echo '[{"number": 7, "url": "https://github.com/o/r/pull/7"}]';;
esac
exit 0
"""


def write_exe(path, body):
    path.write_text(body)
    path.chmod(0o755)


def py_stub(rc, out="", err="", log=None):
    lines = ["import sys"]
    if log:
        lines.append(f"with open({str(log)!r}, 'a') as fh: fh.write(' '.join(sys.argv[1:]) + '\\n')")
    lines.append(f"sys.stdout.write({out!r}); sys.stderr.write({err!r}); sys.exit({rc})")
    return "\n".join(lines) + "\n"


def parse(stdout):
    out = {k: [] for k in MULTI}
    for line in stdout.splitlines():
        key, sep, value = line.partition(": ")
        assert sep, f"not a `key: value` line: {line!r}"
        if key in MULTI:
            out[key].append(value)
        else:
            assert key not in out, f"duplicate key {key}: {stdout}"
            out[key] = value
    return out


class Repo:
    def __init__(self, tmp):
        self.tmp = tmp
        self.script = SCRIPT
        (tmp / "home").mkdir()
        self.stubs = tmp / "stubs"
        self.stubs.mkdir()
        bindir = tmp / "bin"
        bindir.mkdir()
        write_exe(bindir / "glab", GLAB_STUB)
        write_exe(bindir / "gh", GH_STUB)
        self.log = tmp / "stub.log"
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("GIT_", "SIMO_DEV_", "DEV_FLOW_", "DETECT_REMOTE", "CLAUDE_", "REVIEW_MARKER"))}
        env.update(GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t",
                   GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t",
                   HOME=str(tmp / "home"), PATH=f"{bindir}:{os.environ['PATH']}",
                   STUB_LOG=str(self.log), STUB_DIR=str(self.stubs),
                   SIMO_DEV_TRACKER_CONFIG=str(tmp / "tracker.json"),
                   SIMO_DEV_TELEMETRY_DIR=str(tmp / "telemetry"),
                   REVIEW_MARKER_STATE_FILE=str(tmp / "review-state"),
                   DEV_FLOW_NO_TRACKER="1", DETECT_REMOTE_URL=FORGES["gitlab"])
        self.env = env
        self.origin = tmp / "origin.git"
        self.git("init", "-q", "--bare", "-b", "master", str(self.origin))
        self.seed = tmp / "seed"
        self.git("clone", "-q", str(self.origin), str(self.seed))
        self.commit("a.txt", "a\n", "init", cwd=self.seed)
        self.git("push", "-q", "origin", "master", cwd=self.seed)
        self.work = tmp / "work"
        self.git("clone", "-q", str(self.origin), str(self.work))
        self.branch = f"feat/followup-{uuid.uuid4().hex[:12]}"
        self.wgit("switch", "-q", "-c", self.branch)
        self.commit("b.txt", "b\n", "feat: add b")

    def git(self, *args, cwd=None, check=True):
        res = subprocess.run(["git", "-c", "protocol.file.allow=always", *args], cwd=cwd, check=check,
                             capture_output=True, text=True, env=self.env)
        return res.stdout.strip() if check else res

    def wgit(self, *args):
        return self.git("-C", str(self.work), *args)

    def commit(self, name, text, msg, cwd=None):
        cwd = cwd or self.work
        (cwd / name).parent.mkdir(parents=True, exist_ok=True)
        (cwd / name).write_text(text)
        self.git("add", name, cwd=cwd)
        self.git("commit", "-qm", msg, cwd=cwd)

    def run(self, cmd, *args, repo=None):
        target = self.work if repo is None else repo
        (self.stubs / "head_branch").write_text(self.branch)
        proc = subprocess.run([sys.executable, str(self.script), cmd, "--repo", str(target), *map(str, args)],
                              capture_output=True, text=True, env=self.env)
        return proc.returncode, parse(proc.stdout), proc

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def flag(self, name, text="1"):
        (self.stubs / name).write_text(text)

    def seq(self, *tokens):
        (self.stubs / "seq").write_text("".join(f"{t}\n" for t in tokens))

    def use(self, forge):
        self.env["DETECT_REMOTE_URL"] = FORGES[forge]

    def stub_lib(self, name, body):
        root = self.tmp / "plugin"
        if not root.exists():
            shutil.copytree(LIB, root / "lib", ignore=shutil.ignore_patterns("tests", "__pycache__", ".pytest_cache"))
            (root / "skills" / "mr-followup" / "lib").mkdir(parents=True)
            shutil.copy2(DETECT, root / "skills" / "mr-followup" / "lib" / "detect_remote.py")
        if body is None:
            (root / "lib" / name).unlink()
        else:
            (root / "lib" / name).write_text(body)
        self.script = root / "lib" / "mr_followup.py"

    def use_key(self):
        self.branch = f"feat/proj-12-{uuid.uuid4().hex[:8]}"
        self.wgit("branch", "-m", self.branch)
        self.env.pop("DEV_FLOW_NO_TRACKER", None)

    def transition_stub(self, rc=0):
        log = self.tmp / "transition.log"
        stub = self.tmp / "transition.py"
        stub.write_text(py_stub(rc, log=log))
        subprocess.run([sys.executable, str(LIB / "tracker_config.py"), "set", "PROJ", "transition_cmd",
                        f"{shlex.quote(sys.executable)} {shlex.quote(str(stub))}"],
                       env=self.env, check=True, capture_output=True)
        return log

    def on_origin(self, branch=None):
        res = self.git("--git-dir", str(self.origin), "rev-parse", "--verify", "--quiet",
                       f"refs/heads/{branch or self.branch}", check=False)
        return res.returncode == 0


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path.resolve())


def no_dash_r(calls):
    return all(" [-R] " not in c and " [--repo] " not in c for c in calls)


# --- shared: --repo and branch ----------------------------------------------------------------

SUBS = [("detect",), ("review-gate", "--base", "master"), ("push", "--forge", "gitlab", "--iteration", "2")]


@pytest.mark.parametrize("sub", SUBS, ids=[s[0] for s in SUBS])
@pytest.mark.parametrize("bad", ["<worktree-abs>", "relative/dir", "SUBDIR"])
def test_repo_must_be_an_absolute_toplevel(repo, sub, bad):
    if bad == "SUBDIR":
        (repo.work / "sub").mkdir()
        bad = str(repo.work / "sub")
    rc, out, _ = repo.run(sub[0], *sub[1:], repo=bad)
    assert rc == 1
    assert out["STOP"].startswith("--repo must be the absolute path of a worktree toplevel")
    assert repo.calls() == []


@pytest.mark.parametrize("sub", SUBS, ids=[s[0] for s in SUBS])
def test_detached_head_stops(repo, sub):
    repo.wgit("switch", "-q", "--detach")
    rc, out, _ = repo.run(sub[0], *sub[1:])
    assert rc == 1 and out["STOP"].startswith("detached HEAD")
    assert repo.calls() == []


def test_unparseable_args_exit_2(repo):
    proc = subprocess.run([sys.executable, str(SCRIPT), "push", "--repo", str(repo.work), "--forge", "bitbucket",
                           "--iteration", "2"], capture_output=True, text=True, env=repo.env)
    assert proc.returncode == 2


# --- detect: forge ----------------------------------------------------------------------------

def test_detect_unknown_forge_stops(repo):
    repo.env["DETECT_REMOTE_URL"] = "git@bitbucket.org:o/r.git"
    rc, out, proc = repo.run("detect")
    assert rc == 1 and out["STOP"].startswith("could not detect the forge")
    assert "unknown forge" in proc.stderr
    assert repo.calls() == []


# --- detect: GitLab ---------------------------------------------------------------------------

def test_gitlab_open_mr(repo):
    rc, out, _ = repo.run("detect")
    assert rc == 0, out
    assert (out["forge"], out["slug"], out["api_host"]) == ("gitlab", "g/r", "gitlab.example.com")
    assert (out["branch"], out["base"], out["iid"], out["url"]) == (repo.branch, "master", "7", GITLAB_URL)
    assert (out["draft"], out["state"], out["open"]) == ("no", "opened", "yes")
    assert out["transition"] == "skipped (no ticket key)"
    assert out["stack"] == "ok"
    assert "pr" not in out and out["warning"] == []
    calls = repo.calls()
    assert calls and all(c.endswith(f"@{repo.work}") for c in calls) and no_dash_r(calls)
    assert calls[0].startswith(f"glab [mr] [view] [{repo.branch}] [-F] [json]")


def test_gitlab_draft_is_flagged(repo):
    repo.flag("is_draft", "true")
    rc, out, _ = repo.run("detect")
    assert rc == 0 and out["draft"] == "yes"


def test_gitlab_other_source_branch_asks(repo):
    repo.flag("source_branch", "someone-else")
    rc, out, _ = repo.run("detect")
    assert rc == 3
    assert out["ASK"].startswith("glab resolved MR !7 with source branch 'someone-else'")
    assert "transition" not in out and "stack" not in out


def test_gitlab_no_mr_exits_with_the_create_hint(repo):
    repo.seq("none", "none")
    rc, out, _ = repo.run("detect")
    assert rc == 4
    assert out["STOP"] == f"No MR for branch `{repo.branch}` on `g/r`. To open one: `glab mr create`, then re-invoke this skill."
    assert "transition" not in out


def test_gitlab_view_and_state_both_unverified_stop(repo):
    repo.seq("fail", "fail")
    rc, out, proc = repo.run("detect")
    assert rc == 1
    assert out["STOP"].startswith(f"MR state NOT verified for '{repo.branch}'")
    assert "TLS handshake timeout" in proc.stderr


def test_gitlab_transient_view_failure_is_retried_once(repo):
    repo.seq("fail", "ok", "ok")
    rc, out, _ = repo.run("detect")
    assert rc == 0, out
    assert out["iid"] == "7" and out["open"] == "yes"
    assert any("retried" in n for n in out["note"])


def test_gitlab_view_failing_again_after_the_state_answered_stops(repo):
    repo.seq("fail", "ok", "fail")
    rc, out, _ = repo.run("detect")
    assert rc == 1 and out["STOP"].startswith("glab mr view failed twice")


def test_gitlab_merged_mr_is_not_open_and_skips_the_stack_check(repo, tmp_path):
    repo.flag("state", "merged")
    log = tmp_path / "stack.log"
    repo.stub_lib("stack_record.py", py_stub(0, log=log))
    rc, out, _ = repo.run("detect")
    assert rc == 0
    assert (out["state"], out["open"]) == ("merged", "no")
    assert any("is in state 'merged' (expected 'opened')" in w for w in out["warning"])
    assert out["stack"] == "skipped (MR not open)"
    assert not log.exists()


def test_gitlab_state_guard_printing_nothing_is_not_verified(repo):
    repo.stub_lib("check_mr_state.py", py_stub(0))
    rc, out, _ = repo.run("detect")
    assert rc == 0 and out["open"] == "unknown"
    assert any("NOT verified" in w and "no output" in w for w in out["warning"])


def test_gitlab_state_guard_missing_is_not_verified(repo):
    repo.stub_lib("check_mr_state.py", None)
    rc, out, _ = repo.run("detect")
    assert rc == 0 and out["open"] == "unknown"
    assert any("guard unreachable" in w for w in out["warning"])


def test_gitlab_state_guard_finding_no_mr_after_the_view_warns(repo):
    repo.seq("ok", "none")
    rc, out, _ = repo.run("detect")
    assert rc == 0 and out["open"] == "unknown"
    assert any("no MR found" in w for w in out["warning"])


def test_gitlab_state_guard_unknown_rc_is_not_verified(repo):
    repo.stub_lib("check_mr_state.py", py_stub(9, out="weird\n"))
    rc, out, _ = repo.run("detect")
    assert rc == 0 and out["open"] == "unknown"
    assert any("rc 9, reported 'weird'" in w for w in out["warning"])


def test_view_only_skips_the_transition_and_the_stack_check(repo, tmp_path):
    repo.use_key()
    tlog = repo.transition_stub()
    slog = tmp_path / "stack.log"
    repo.stub_lib("stack_record.py", py_stub(0, log=slog))
    rc, out, _ = repo.run("detect", "--view-only")
    assert rc == 0 and out["open"] == "yes" and out["iid"] == "7"
    assert "transition" not in out and "stack" not in out
    assert not tlog.exists() and not slog.exists()


def test_gitlab_view_failure_reuses_a_not_open_verdict(repo):
    """The fallback's guard answer (rc 1) is reused: open: no without a second guard call."""
    repo.flag("state", "merged")
    repo.seq("fail", "ok", "ok")
    rc, out, _ = repo.run("detect")
    assert rc == 0 and out["open"] == "no" and out["stack"] == "skipped (MR not open)"
    assert len([c for c in repo.calls() if c.startswith("glab [mr] [view]")]) == 3


def test_gitlab_view_without_a_target_branch_stops(repo):
    repo.flag("target", "")
    rc, out, _ = repo.run("detect")
    assert rc == 1 and out["STOP"].startswith(f"the MR view of '{repo.branch}' carries no target branch")


def test_gitlab_ask_resumes_with_the_picked_number(repo):
    repo.flag("source_branch", "someone-else")
    rc, out, _ = repo.run("detect")
    assert rc == 3 and "--number" in out["ASK"]
    (repo.stubs / "source_branch").unlink()
    repo.log.unlink()
    rc, out, _ = repo.run("detect", "--number", "7")
    assert rc == 0, out
    assert (out["base"], out["iid"], out["open"]) == ("master", "7", "yes")
    assert any("following MR !7 as picked" in n for n in out["note"])
    views = [c for c in repo.calls() if c.startswith("glab [mr] [view]")]
    assert len(views) == 2 and all(c.startswith("glab [mr] [view] [7] [-F] [json]") for c in views)


def test_picked_number_with_another_source_branch_stops(repo):
    repo.flag("source_branch", "someone-else")
    rc, out, _ = repo.run("detect", "--number", "7")
    assert rc == 1
    assert out["STOP"].startswith("MR !7's source branch is 'someone-else', not the local branch")
    assert "transition" not in out and "stack" not in out


def test_picked_number_that_does_not_exist_stops(repo):
    repo.seq("none", "none")
    rc, out, _ = repo.run("detect", "--number", "8")
    assert rc == 1 and out["STOP"].startswith("MR 8 not found on `g/r`")


@pytest.mark.parametrize("sub", [("detect",), ("push", "--forge", "gitlab", "--iteration", "2")], ids=["detect", "push"])
def test_digits_only_branch_stops(repo, sub):
    repo.wgit("branch", "-m", "123")
    repo.branch = "123"
    rc, out, _ = repo.run(sub[0], *sub[1:])
    assert rc == 1 and out["STOP"].startswith("branch '123' is digits only")
    assert repo.calls() == [] and not repo.on_origin()


# --- detect: GitHub ---------------------------------------------------------------------------

def test_github_open_pr(repo):
    repo.use("github")
    rc, out, _ = repo.run("detect")
    assert rc == 0, out
    assert (out["forge"], out["slug"]) == ("github", "o/r")
    assert (out["pr"], out["base"], out["url"], out["open"], out["draft"]) == ("7", "main", GITHUB_URL, "yes", "no")
    assert "iid" not in out
    calls = repo.calls()
    assert calls[0].startswith(f"gh [pr] [view] [{repo.branch}] [--json]")
    assert any(c.startswith(f"gh [pr] [list] [--head] [{repo.branch}] [--state] [open]") for c in calls)
    assert all(c.endswith(f"@{repo.work}") for c in calls) and no_dash_r(calls)


def test_github_closed_pr_is_not_open(repo):
    repo.use("github")
    repo.flag("state", "CLOSED")
    rc, out, _ = repo.run("detect")
    assert rc == 0 and out["open"] == "no" and out["stack"] == "skipped (MR not open)"
    assert any("CLOSED" in w for w in out["warning"])


def test_github_no_pr_exits_with_the_create_hint(repo):
    repo.use("github")
    repo.seq("none", "none")
    rc, out, _ = repo.run("detect")
    assert rc == 4
    assert out["STOP"] == f"No PR for branch `{repo.branch}` on `o/r`. To open one: `gh pr create`, then re-invoke this skill."


def test_github_unverified_view_stops(repo):
    repo.use("github")
    repo.seq("fail", "fail")
    rc, out, _ = repo.run("detect")
    assert rc == 1 and out["STOP"].startswith(f"PR state NOT verified for '{repo.branch}'")


def test_github_multiple_open_prs_ask(repo):
    repo.use("github")
    repo.flag("pr_list", json.dumps([{"number": 7, "url": GITHUB_URL}, {"number": 9, "url": "https://github.com/o/r/pull/9"}]))
    rc, out, _ = repo.run("detect")
    assert rc == 3 and out["ASK"].startswith("2 open PRs for") and "--number" in out["ASK"]
    assert out["candidate"] == [f"#7 {GITHUB_URL}", "#9 https://github.com/o/r/pull/9"]


def test_github_ask_resumes_with_the_picked_number(repo):
    repo.use("github")
    repo.flag("pr_list", json.dumps([{"number": 7, "url": GITHUB_URL}, {"number": 9, "url": "https://github.com/o/r/pull/9"}]))
    rc, out, _ = repo.run("detect", "--number", "9")
    assert rc == 0, out
    assert out["base"] == "main" and out["open"] == "yes" and out["pr"] == "9"
    calls = repo.calls()
    assert any(c.startswith("gh [pr] [view] [9] [--json]") for c in calls)
    assert not any(c.startswith("gh [pr] [list]") for c in calls)


def test_github_cross_repository_prs_are_not_a_second_pr(repo):
    """`gh pr list --head` matches by branch name, so a fork's same-named branch shows up too."""
    repo.use("github")
    repo.flag("pr_list", json.dumps([{"number": 7, "url": GITHUB_URL, "isCrossRepository": False},
                                     {"number": 9, "url": "https://github.com/f/r/pull/9", "isCrossRepository": True}]))
    rc, out, _ = repo.run("detect")
    assert rc == 0 and out["pr"] == "7" and out["candidate"] == []
    lists = [c for c in repo.calls() if c.startswith("gh [pr] [list]")]
    assert lists and "isCrossRepository" in lists[0]


def test_number_must_be_an_integer(repo):
    proc = subprocess.run([sys.executable, str(SCRIPT), "detect", "--repo", str(repo.work), "--number", "<it>"],
                          capture_output=True, text=True, env=repo.env)
    assert proc.returncode == 2 and repo.calls() == []


def test_github_pr_list_failure_only_warns(repo):
    repo.use("github")
    repo.flag("list_fails")
    rc, out, _ = repo.run("detect")
    assert rc == 0 and any("gh pr list" in w for w in out["warning"])


def test_github_other_head_branch_asks(repo):
    repo.use("github")
    repo.flag("source_branch", "someone-else")
    rc, out, _ = repo.run("detect")
    assert rc == 3 and "someone-else" in out["ASK"]


# --- detect: CodeReview transition -----------------------------------------------------------

def test_transition_runs_with_the_codereview_flags(repo):
    repo.use_key()
    log = repo.transition_stub()
    rc, out, _ = repo.run("detect")
    assert rc == 0
    key = repo.branch.split("/")[1].upper()[:7]
    assert out["transition"] == f"{key} -> CodeReview"
    assert log.read_text().split() == [key, "CodeReview", "--leaf-only", "--sync-parent", "--clear-blocked"]


def test_transition_failure_only_warns(repo):
    repo.use_key()
    repo.transition_stub(rc=7)
    rc, out, _ = repo.run("detect")
    assert rc == 0 and "transition" not in out
    assert any("transition_cmd failed" in w and "rc 7" in w for w in out["warning"])


# --- detect: parent merged rewritten ----------------------------------------------------------

def test_stack_parent_merged_passes_the_rebase_through(repo, tmp_path):
    log = tmp_path / "stack.log"
    repo.stub_lib("stack_record.py", py_stub(1, out="rebase: git -C /w rebase --onto origin/master abc\ndrops: abc parent work\n", log=log))
    rc, out, _ = repo.run("detect")
    assert rc == 0
    assert out["stack"] == "parent-merged"
    assert out["rebase"] == "git -C /w rebase --onto origin/master abc"
    assert out["drops"] == ["abc parent work"]
    assert log.read_text().splitlines() == [f"check --repo {repo.work} --base master"]


def test_stack_check_error_only_warns(repo, tmp_path):
    log = tmp_path / "stack.log"
    repo.stub_lib("stack_record.py", py_stub(2, log=log))
    rc, out, _ = repo.run("detect")
    assert rc == 0 and out["stack"] == "unverified"
    assert any("stack_record.py check" in w for w in out["warning"])
    assert len(log.read_text().splitlines()) == 1


def test_stack_ok_refreshes_the_record(repo, tmp_path):
    log = tmp_path / "stack.log"
    repo.stub_lib("stack_record.py", py_stub(0, log=log))
    rc, out, _ = repo.run("detect")
    assert rc == 0 and out["stack"] == "ok"
    assert log.read_text().splitlines()[1] == f"record --repo {repo.work} --target master"


def test_stack_record_refresh_failure_only_warns(repo, tmp_path):
    stub = "import sys\nsys.exit(0 if sys.argv[1] == 'check' else 5)\n"
    repo.stub_lib("stack_record.py", stub)
    rc, out, _ = repo.run("detect")
    assert rc == 0 and out["stack"] == "ok"
    assert any("stack_record.py record failed (rc 5)" in w for w in out["warning"])


# --- review-gate: local base ahead ------------------------------------------------------------

def test_review_gate_without_a_local_base_branch_is_safe(repo):
    """The case check_local_base_ahead.py turned into a STOP (rc 2: `merge-base` on a missing ref)."""
    repo.wgit("branch", "-D", "master")
    rc, out, _ = repo.run("review-gate", "--base", "master")
    assert rc == 0, out
    assert out["local_review"] == "dispatch"


def test_review_gate_local_base_ahead_in_the_ancestry_stops(repo):
    repo.wgit("switch", "-q", "master")
    repo.commit("c.txt", "c\n", "local only")
    repo.wgit("switch", "-q", repo.branch)
    repo.wgit("rebase", "-q", "master")
    rc, out, _ = repo.run("review-gate", "--base", "master")
    assert rc == 1
    assert out["STOP"].startswith("local base ahead: local 'master' is ahead of origin/master")
    assert len(out["ahead"]) == 1 and out["ahead"][0].endswith("local only")
    assert "local_review" not in out


def test_review_gate_local_base_ahead_outside_the_ancestry_is_safe(repo):
    repo.wgit("switch", "-q", "master")
    repo.commit("c.txt", "c\n", "local only")
    repo.wgit("switch", "-q", repo.branch)
    rc, out, _ = repo.run("review-gate", "--base", "master")
    assert rc == 0 and out["ahead"] == []


def test_review_gate_fetches_the_base_first(repo):
    repo.commit("d.txt", "d\n", "remote advance", cwd=repo.seed)
    repo.git("push", "-q", "origin", "master", cwd=repo.seed)
    rc, _, _ = repo.run("review-gate", "--base", "master")
    assert rc == 0
    assert repo.wgit("rev-parse", "origin/master") == repo.git("-C", str(repo.seed), "rev-parse", "HEAD")


def test_review_gate_fetch_failure_only_warns(repo):
    repo.wgit("remote", "set-url", "origin", str(repo.tmp / "missing.git"))
    rc, out, _ = repo.run("review-gate", "--base", "master")
    assert rc == 0 and any("git fetch origin master failed" in w for w in out["warning"])


# --- review-gate: round-1 skip (ported from test_review_marker.py) ---------------------------

@pytest.fixture
def pushed(repo):
    """The branch two commits past origin/master: one edit to a path zsh would split, one deletion.
    Every file's mtime is in the past, so a record taken now covers them all."""
    repo.commit("two words.txt", "base\n", "base two")
    repo.commit("gone.txt", "base\n", "base gone")
    repo.wgit("push", "-q", "origin", f"HEAD:master")
    repo.wgit("fetch", "-q", "origin")
    repo.wgit("branch", "-f", "master", "origin/master")
    (repo.work / "two words.txt").write_text("changed\n")
    repo.wgit("rm", "-q", "gone.txt")
    repo.wgit("commit", "-qam", "work")
    past = time.time() - 100
    os.utime(repo.work / "two words.txt", (past, past))
    return repo


def record(repo, session="s"):
    subprocess.run([sys.executable, str(LIB / "review_marker.py"), "record", "--repo", str(repo.work),
                    "--session", session], env=repo.env, check=True, capture_output=True)


def gate(repo, session="s"):
    return repo.run("review-gate", "--base", "master", "--round1", "--session", session)


def test_round1_skips_when_review_loop_covered_the_diff(pushed):
    record(pushed)
    rc, out, _ = gate(pushed)
    assert rc == 0 and out["local_review"] == "skip (covered by review-loop)", out


def test_round1_dispatches_when_a_file_changed_after_the_review(pushed):
    record(pushed)
    later = time.time() + 100
    os.utime(pushed.work / "two words.txt", (later, later))
    rc, out, _ = gate(pushed)
    assert any("two words.txt changed after the last review" in n for n in out["note"])
    assert rc == 0 and out["local_review"] == "dispatch (review_marker rc=1)"


def test_round1_dispatches_from_another_session(pushed):
    record(pushed, session="review-loop-session")
    rc, out, _ = gate(pushed, session="other")
    assert out["local_review"] == "dispatch (review_marker rc=1)"


def test_round1_dispatches_without_a_session_id(pushed):
    record(pushed)
    rc, out, _ = gate(pushed, session="")
    assert out["local_review"] == "dispatch (review_marker rc=1)"


def test_round1_dispatches_on_a_deletion_only_diff(pushed):
    """Nothing left to compare must not read as covered: check() returns 0 on an empty list."""
    pushed.wgit("push", "-q", "origin", "HEAD:master")
    pushed.wgit("fetch", "-q", "origin")
    pushed.wgit("branch", "-f", "master", "origin/master")
    pushed.wgit("rm", "-q", "two words.txt")
    pushed.wgit("commit", "-qm", "drop")
    record(pushed)
    rc, out, _ = gate(pushed)
    assert out["local_review"] == "dispatch (no file left to check)"


def test_round1_dispatches_when_the_listing_fails(pushed):
    """An unknown base ref: git fails, and an empty list must not reach check()."""
    record(pushed)
    pushed.wgit("remote", "set-url", "origin", str(pushed.tmp / "missing.git"))
    pushed.wgit("update-ref", "-d", "refs/remotes/origin/master")
    pushed.wgit("branch", "-D", "master")
    rc, out, _ = gate(pushed)
    assert out["local_review"].startswith("dispatch (listing failed rc=")


def test_review_gate_without_round1_always_dispatches(pushed):
    record(pushed)
    rc, out, _ = pushed.run("review-gate", "--base", "master")
    assert rc == 0 and out["local_review"] == "dispatch"


# --- push: pre-push MR-state guard ------------------------------------------------------------

def push(repo, forge="gitlab", iteration=2):
    return repo.run("push", "--forge", forge, "--iteration", iteration)


def test_push_when_the_mr_is_open(repo):
    rc, out, _ = push(repo)
    assert rc == 0, out
    assert out["pushed"] == repo.branch and out["retry"] == "2"
    assert repo.on_origin()
    assert repo.wgit("rev-parse", "--abbrev-ref", "@{u}") == f"origin/{repo.branch}"
    guard = [c for c in repo.calls() if c.startswith("glab [mr] [view]")]
    assert guard and guard[0].endswith(f"@{repo.work}")


def test_push_on_github_asks_gh(repo):
    rc, out, _ = push(repo, forge="github")
    assert rc == 0 and repo.on_origin()
    assert any(c.startswith(f"gh [pr] [view] [{repo.branch}] [--json] [state]") for c in repo.calls())


def test_push_when_no_mr_is_found_warns_and_pushes(repo):
    repo.seq("none")
    rc, out, _ = push(repo)
    assert rc == 0 and repo.on_origin()
    assert any("no MR found" in w and "mid-loop" in w for w in out["warning"])


def test_push_refused_when_the_mr_is_merged(repo):
    repo.flag("state", "merged")
    rc, out, _ = push(repo)
    assert rc == 1
    assert out["STOP"].startswith(f"MR/PR for '{repo.branch}' is now 'merged' (not open)")
    assert not repo.on_origin() and "retry" not in out


def test_push_refused_when_the_state_is_unverifiable(repo):
    repo.seq("fail")
    rc, out, _ = push(repo)
    assert rc == 3
    assert out["STOP"].startswith(f"could not verify MR state for '{repo.branch}'")
    assert not repo.on_origin()


def test_push_refused_when_the_guard_prints_nothing(repo):
    repo.stub_lib("check_mr_state.py", py_stub(0))
    rc, out, _ = push(repo)
    assert rc == 2 and "printed nothing" in out["STOP"]
    assert not repo.on_origin()


def test_push_refused_when_the_guard_is_missing(repo):
    repo.stub_lib("check_mr_state.py", None)
    rc, out, _ = push(repo)
    assert rc == 2 and "the guard is not at" in out["STOP"]
    assert not repo.on_origin()


def test_rejected_push_is_its_own_code(repo):
    repo.git("switch", "-q", "-c", repo.branch, cwd=repo.seed)
    repo.commit("z.txt", "z\n", "diverged", cwd=repo.seed)
    repo.git("push", "-q", "origin", repo.branch, cwd=repo.seed)
    rc, out, proc = push(repo)
    assert rc == 4
    assert out["STOP"].startswith("git push rejected")
    assert "retry" not in out
    assert "rejected" in proc.stderr or "fetch first" in proc.stderr or "non-fast-forward" in proc.stderr
