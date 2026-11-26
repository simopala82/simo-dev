"""lib/mr_create.py: simo-dev:commit step 7 (prepare, body) and step 9 (create, transition), plus mrtmp.

Every case and stop of commit SKILL.md steps 7 and 9 at 64fb8699 is ported here before the script
existed. It replaces test_commit_mr_assignee.py, test_commit_mr_draft_decision.py and
test_commit_mr_draft_verify.py, which executed the old SKILL.md fences under bash and zsh, and the
executed half of test_commit_mr_request_dispatch.py. Each test builds real git repos in tmp_path
(bare origin + clone) and runs the script as the skill does; glab/gh are stub binaries on PATH that
log argv and cwd; a sibling script is stubbed only by copying lib/ into tmp_path and replacing it.
"""
import json
import os
import shlex
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent
PLUGIN = LIB.parent
SCRIPT = LIB / "mr_create.py"
DETECT = PLUGIN / "skills" / "mr-followup" / "lib" / "detect_remote.py"
FORGES = {"gitlab": "git@gitlab.example.com:g/r.git", "github": "git@github.com:o/r.git"}
GITLAB_URL = "https://gitlab.example.com/g/r/-/merge_requests/7"
GITHUB_URL = "https://github.com/o/r/pull/7"
MULTI = ("note", "warning", "ahead")
SHELL_MRTMP = ('W="$1"; printf %s "/tmp/claude_mr_$(basename "$(dirname "$(git -C "$W" rev-parse '
               '--path-format=absolute --git-common-dir)")")_$(git -C "$W" branch --show-current | tr \'/\' \'-\')"')
PRES = {"title": "feat: x", "impact_paragraph": "It does x.", "why_it_matters": ["a"],
        "mermaid": "flowchart LR\n  A-->B", "diagram_type": "flowchart"}

GLAB_STUB = r"""#!/bin/sh
{ printf glab; printf ' [%s]' "$@"; printf ' @%s\n' "$(pwd -P)"; } >> "$STUB_LOG"
S="$STUB_DIR"
case "$1 $2" in
"api user")
  n=$(cat "$S/user_fails" 2>/dev/null || echo 0)
  if [ "$n" -gt 0 ]; then echo $((n - 1)) > "$S/user_fails"; echo "net/http: TLS handshake timeout" >&2; exit 1; fi
  if [ -f "$S/no_username" ]; then echo '{"message": "401 Unauthorized"}'; exit 0; fi
  echo '{"username": "alice"}';;
"mr create")
  if [ -f "$S/create_fails" ]; then echo "409 Conflict: merge request already exists" >&2; exit 1; fi
  if [ ! -f "$S/ignore_draft" ]; then for a in "$@"; do [ "$a" = --draft ] && echo true > "$S/is_draft"; done; fi
  echo "https://gitlab.example.com/g/r/-/merge_requests/7";;
"mr view")
  if [ -f "$S/no_mr" ]; then echo "no open merge request available for \"$3\"" >&2; exit 1; fi
  src=$(cat "$S/source_branch" 2>/dev/null || printf '%s' "$3")
  d=$(cat "$S/is_draft" 2>/dev/null || echo false)
  k=$(cat "$S/draft_key" 2>/dev/null || echo draft)
  printf '{"source_branch": "%s", "%s": %s, "web_url": "https://gitlab.example.com/g/r/-/merge_requests/7"}\n' "$src" "$k" "$d";;
"mr update")
  [ -f "$S/mark_fails" ] || echo true > "$S/is_draft";;
esac
exit 0
"""

GH_STUB = r"""#!/bin/sh
{ printf gh; printf ' [%s]' "$@"; printf ' @%s\n' "$(pwd -P)"; } >> "$STUB_LOG"
S="$STUB_DIR"
case "$1 $2" in
"pr create")
  if [ -f "$S/create_fails" ]; then echo "a pull request for branch already exists" >&2; exit 1; fi
  if [ ! -f "$S/ignore_draft" ]; then for a in "$@"; do [ "$a" = --draft ] && echo true > "$S/is_draft"; done; fi
  echo "https://github.com/o/r/pull/7";;
"pr view")
  if [ -f "$S/no_mr" ]; then echo "no pull requests found for branch \"$3\"" >&2; exit 1; fi
  src=$(cat "$S/source_branch" 2>/dev/null || printf '%s' "$3")
  d=$(cat "$S/is_draft" 2>/dev/null || echo false)
  printf '{"headRefName": "%s", "isDraft": %s, "url": "https://github.com/o/r/pull/7"}\n' "$src" "$d";;
"pr ready")
  [ -f "$S/mark_fails" ] || echo true > "$S/is_draft";;
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


def shell_mrtmp(w, env):
    return subprocess.run(["bash", "-c", SHELL_MRTMP, "_", str(w)], capture_output=True, text=True,
                          env=env, check=True).stdout


def creates(calls):
    return [c for c in calls if c.startswith(("glab [mr] [create]", "gh [pr] [create]"))]


class Repo:
    def __init__(self, tmp):
        self.tmp = tmp
        self.mrtmps = set()
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
               if not k.startswith(("GIT_", "SIMO_DEV_", "DEV_FLOW_", "DETECT_REMOTE", "CLAUDE_"))}
        env.update(GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t",
                   GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t",
                   HOME=str(tmp / "home"), PATH=f"{bindir}:{os.environ['PATH']}",
                   STUB_LOG=str(self.log), STUB_DIR=str(self.stubs),
                   SIMO_DEV_TRACKER_CONFIG=str(tmp / "tracker.json"),
                   SIMO_DEV_TELEMETRY_DIR=str(tmp / "telemetry"),
                   DEV_FLOW_NO_TRACKER="1", DETECT_REMOTE_URL=FORGES["gitlab"])
        self.env = env
        origin = tmp / "origin.git"
        self.git("init", "-q", "--bare", "-b", "master", str(origin))
        self.seed = tmp / "seed"
        self.git("clone", "-q", str(origin), str(self.seed))
        self.commit("a.txt", "a\n", "init", cwd=self.seed)
        self.git("push", "-q", "origin", "master", cwd=self.seed)
        self.work = tmp / "work"
        self.git("clone", "-q", str(origin), str(self.work))
        self.branch = f"feat/mr-create-{uuid.uuid4().hex[:12]}"
        self.wgit("switch", "-q", "-c", self.branch)
        self.commit("b.txt", "b\n", "feat: add b")

    def git(self, *args, cwd=None):
        return subprocess.run(["git", "-c", "protocol.file.allow=always", *args], cwd=cwd, check=True,
                              capture_output=True, text=True, env=self.env).stdout.strip()

    def wgit(self, *args):
        return self.git("-C", str(self.work), *args)

    def commit(self, name, text, msg, cwd=None):
        cwd = cwd or self.work
        (cwd / name).parent.mkdir(parents=True, exist_ok=True)
        (cwd / name).write_text(text)
        self.git("add", name, cwd=cwd)
        self.git("commit", "-qm", msg, cwd=cwd)

    def use_key(self):
        self.branch = f"feat/proj-12-{uuid.uuid4().hex[:8]}"
        self.wgit("branch", "-m", self.branch)
        self.env.pop("DEV_FLOW_NO_TRACKER", None)

    @property
    def mrtmp(self):
        path = Path(shell_mrtmp(self.work, self.env))
        self.mrtmps.add(path)
        return path

    def run(self, cmd, *args, repo=None):
        target = self.work if repo is None else repo
        proc = subprocess.run([sys.executable, str(self.script), cmd, "--repo", str(target), *map(str, args)],
                              capture_output=True, text=True, env=self.env)
        return proc.returncode, parse(proc.stdout), proc

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def flag(self, name, text="1"):
        (self.stubs / name).write_text(text)

    def stub_lib(self, name, body):
        root = self.tmp / "plugin"
        if not root.exists():
            shutil.copytree(LIB, root / "lib", ignore=shutil.ignore_patterns("tests", "__pycache__", ".pytest_cache"))
            (root / "skills" / "mr-followup" / "lib").mkdir(parents=True)
            shutil.copy2(DETECT, root / "skills" / "mr-followup" / "lib" / "detect_remote.py")
        (root / "lib" / name).write_text(body)
        self.script = root / "lib" / "mr_create.py"

    def tracker(self, prefix, field, value):
        subprocess.run([sys.executable, str(LIB / "tracker_config.py"), "set", prefix, field, value],
                       env=self.env, check=True, capture_output=True)

    def ledger(self, cmd, *args):
        return subprocess.run([sys.executable, str(LIB / "also_fixed_ledger.py"), cmd, "--repo", str(self.work), *args],
                              env=self.env, check=True, capture_output=True, text=True).stdout

    def transition_stub(self, rc=0):
        log = self.tmp / "transition.log"
        stub = self.tmp / "transition.py"
        stub.write_text(py_stub(rc, log=log))
        self.tracker("PROJ", "transition_cmd", f"{shlex.quote(sys.executable)} {shlex.quote(str(stub))}")
        return log

    def write_mrtmp(self, title="t", target="master", body="b", draft=None, key=""):
        m = self.mrtmp
        m.mkdir(parents=True, exist_ok=True)
        if title is not None:
            (m / "mr_pres.json").write_text(json.dumps({"title": title}))
        if body is not None:
            (m / "mr_body.md").write_text(body)
        (m / "mr_ctx.json").write_text(json.dumps({
            "commit_log": "abc1234 feat: add b", "file_stat": " b.txt | 1 +\n 1 file changed",
            "branch_meta": {"source": self.branch, "target": target, "target_source": "default",
                            "head_sha": "x", "jira_key": key}}))
        if draft is not None:
            (m / "mr_draft").write_text(draft)
        return m

    def cleanup(self):
        try:
            self.mrtmps.add(Path(shell_mrtmp(self.work, self.env)))
        except subprocess.CalledProcessError:
            pass
        for path in self.mrtmps:
            shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def repo(tmp_path):
    r = Repo(tmp_path.resolve())
    yield r
    r.cleanup()


def use_forge(repo, forge):
    repo.env["DETECT_REMOTE_URL"] = FORGES[forge]


# --- shared: --repo, branch, scratch dir ------------------------------------------------------

SUBS = ["prepare", "body", "create", "transition", "mrtmp"]


@pytest.mark.parametrize("sub", SUBS)
@pytest.mark.parametrize("bad", ["<absolute path of the task worktree>", "relative/dir", "SUBDIR"])
def test_repo_must_be_an_absolute_toplevel(repo, sub, bad):
    if bad == "SUBDIR":
        (repo.work / "sub").mkdir()
        bad = str(repo.work / "sub")
    rc, out, _ = repo.run(sub, repo=bad)
    assert rc == 1
    assert out["STOP"].startswith("--repo must be the absolute path of a worktree toplevel")
    assert repo.calls() == []


@pytest.mark.parametrize("sub", SUBS)
def test_detached_head_stops(repo, sub):
    repo.wgit("switch", "-q", "--detach")
    rc, out, _ = repo.run(sub)
    assert rc == 1 and out["STOP"].startswith("detached HEAD")


def test_mrtmp_matches_the_shell_form_in_the_main_checkout(repo):
    rc, out, _ = repo.run("mrtmp")
    assert rc == 0 and out["mrtmp"] == str(repo.mrtmp) and repo.mrtmp.is_dir()


def test_mrtmp_matches_the_shell_form_in_a_linked_worktree(repo):
    wt = repo.tmp / "wt"
    repo.wgit("worktree", "add", "-q", "-b", f"fix/x-{uuid.uuid4().hex[:8]}", str(wt))
    rc, out, _ = repo.run("mrtmp", repo=wt)
    expected = shell_mrtmp(wt, repo.env)
    repo.mrtmps.add(Path(expected))
    assert rc == 0 and out["mrtmp"] == expected and Path(expected).is_dir()
    assert "/tmp/claude_mr_work_" in expected  # the repo name, not the worktree dir name


# --- prepare ------------------------------------------------------------------------------------

def test_prepare_prints_target_draft_and_request(repo):
    rc, out, p = repo.run("prepare")
    assert rc == 0, p.stdout + p.stderr
    m = repo.mrtmp
    assert out["mrtmp"] == str(m)
    assert out["target"] == "master" and out["target_source"] == "default"
    assert out["draft"] == "0" and (m / "mr_draft").read_text() == "0"
    assert out["agent_type"] == "general-purpose" and out["model"]
    assert out["request"] == f"{m}/mr_request.json"
    assert [ln.split(": ", 1)[0] for ln in p.stdout.splitlines()][-3:] == ["agent_type", "model", "request"]
    request = json.loads((m / "mr_request.json").read_text())
    assert {"prompt", "schema", "agent_type", "model"} <= request.keys()
    assert json.loads((m / "mr_ctx.json").read_text())["branch_meta"]["target"] == "master"
    assert "+b" in (m / "mr_diff.txt").read_text()


def test_prepare_asks_for_scope_fetch_cmd_and_writes_nothing(repo):
    repo.use_key()
    rc, out, _ = repo.run("prepare")
    assert rc == 3
    assert out["ASK"] == "scope_fetch_cmd prefix=PROJ key=PROJ-12"
    assert not repo.mrtmp.exists()


def test_prepare_proceeds_once_scope_fetch_cmd_is_set(repo):
    repo.use_key()
    repo.tracker("PROJ", "scope_fetch_cmd", "-")
    rc, out, p = repo.run("prepare")
    assert rc == 0, p.stdout + p.stderr
    assert "ASK" not in out and "request" in out


def test_prepare_target_not_on_origin_stops(repo):
    rc, out, _ = repo.run("prepare", "--target", "nosuch")
    assert rc == 1 and out["STOP"].startswith("target: ") and "does not resolve" in out["STOP"]
    assert not (repo.mrtmp / "mr_diff.txt").exists()


def test_prepare_relays_a_stacked_parent_tie_as_target_stop(repo):
    repo.stub_lib("build_mr_ctx.py", py_stub(3, err="STOP: HEAD is stacked on more than one remote branch "
                                                    "at the same distance: a, b — re-run with --target <branch>.\n"))
    rc, out, _ = repo.run("prepare")
    assert rc == 1 and out["STOP"].startswith("target: HEAD is stacked on more than one remote branch")


def test_prepare_other_build_mr_ctx_failure_stops(repo):
    repo.stub_lib("build_mr_ctx.py", py_stub(1, err="Traceback: boom\n"))
    rc, out, _ = repo.run("prepare")
    assert rc == 1 and out["STOP"].startswith("build_mr_ctx.py failed (rc 1")


def test_prepare_relays_note_lines(repo):
    ctx = {"ticket_summary": "", "commit_log": "x", "file_stat": "y", "changed_files": "b.txt",
           "branch_meta": {"source": repo.branch, "target": "master", "target_source": "default",
                           "head_sha": "h", "jira_key": ""}}
    repo.stub_lib("build_mr_ctx.py", py_stub(0, out=json.dumps(ctx),
                                             err="note: origin/p share commits with HEAD but diverged from it\n"))
    rc, out, p = repo.run("prepare")
    assert rc == 0, p.stdout + p.stderr
    assert out["note"] == ["origin/p share commits with HEAD but diverged from it"]


def test_prepare_empty_target_stops(repo):
    repo.wgit("config", "remote.origin.followRemoteHEAD", "never")
    repo.wgit("remote", "set-head", "origin", "-d")
    rc, out, _ = repo.run("prepare")
    assert rc == 1 and out["STOP"].startswith("could not resolve the MR target branch")


def test_prepare_fetch_failure_only_warns(repo):
    repo.wgit("remote", "set-url", "origin", str(repo.tmp / "missing.git"))
    rc, out, p = repo.run("prepare")  # the local origin/* refs from the clone still resolve
    assert rc == 0, p.stdout + p.stderr
    assert any(w.startswith("git fetch origin failed") for w in out["warning"])
    assert "request" in out


def test_prepare_records_an_explicit_stacked_target(repo):
    repo.git("push", "-q", "origin", "master:develop", cwd=repo.seed)
    rc, out, p = repo.run("prepare", "--target", "develop")  # no local develop: no false local-base STOP
    assert rc == 0, p.stdout + p.stderr
    assert out["target"] == "develop" and out["target_source"] == "explicit"
    assert repo.wgit("config", f"branch.{repo.branch}.simoDevStackParent") == "develop"


def test_prepare_stack_record_failure_only_warns(repo):
    repo.stub_lib("stack_record.py", py_stub(2, err="git error\n"))
    rc, out, p = repo.run("prepare")
    assert rc == 0, p.stdout + p.stderr
    assert any(w.startswith("stack_record.py record failed (rc 2)") for w in out["warning"])


def test_prepare_stops_when_local_base_is_ahead_in_ancestry(repo):
    repo.wgit("switch", "-q", "master")
    repo.commit("local.txt", "l\n", "local only on master")
    repo.wgit("switch", "-q", repo.branch)
    repo.wgit("rebase", "-q", "master")
    rc, out, _ = repo.run("prepare")
    assert rc == 1
    assert out["STOP"].startswith("local base ahead: local 'master' is ahead of origin/master")
    assert any("local only on master" in line for line in out["ahead"])
    m = repo.mrtmp
    assert not (m / "mr_diff.txt").exists() and not (m / "mr_draft").exists()


def test_prepare_ignores_local_base_work_outside_the_branch(repo):
    repo.wgit("switch", "-q", "master")
    repo.commit("local.txt", "l\n", "local only")
    repo.wgit("switch", "-q", repo.branch)
    rc, out, p = repo.run("prepare")
    assert rc == 0, p.stdout + p.stderr


def test_prepare_diff_is_three_dot_against_the_fetched_origin(repo):
    repo.commit("c.txt", "c\n", "advance base", cwd=repo.seed)
    repo.git("push", "-q", "origin", "master", cwd=repo.seed)
    rc, out, p = repo.run("prepare")
    assert rc == 0, p.stdout + p.stderr
    diff = (repo.mrtmp / "mr_diff.txt").read_text()
    assert "b.txt" in diff and "c.txt" not in diff
    assert repo.wgit("rev-parse", "origin/master") == repo.git("-C", str(repo.seed), "rev-parse", "HEAD")


def test_prepare_prunes_a_deleted_remote_branch(repo):
    repo.git("push", "-q", "origin", "master:gone", cwd=repo.seed)
    repo.wgit("fetch", "-q", "origin")
    repo.git("push", "-q", "origin", "--delete", "gone", cwd=repo.seed)
    rc, _, p = repo.run("prepare")
    assert rc == 0, p.stdout + p.stderr
    probe = subprocess.run(["git", "-C", str(repo.work), "rev-parse", "--verify", "--quiet", "refs/remotes/origin/gone"],
                           env=repo.env, capture_output=True)
    assert probe.returncode != 0


def test_prepare_clears_stale_outputs_of_an_earlier_run(repo):
    m = repo.write_mrtmp(title="stale", draft="1")
    (m / "mr_request.json").write_text("{}")
    (m / "mr_diff.txt").write_text("stale")
    rc, *_ = repo.run("prepare", "--target", "nosuch")
    assert rc == 1
    for name in ("mr_pres.json", "mr_body.md", "mr_draft", "mr_request.json", "mr_ctx.json", "mr_diff.txt"):
        assert not (m / name).exists(), name


def test_prepare_without_request_line_when_presentation_fails(repo):
    repo.wgit("reset", "-q", "--hard", "origin/master")  # empty diff: mr_presentation.py refuses it
    rc, out, p = repo.run("prepare")
    assert rc == 0, p.stdout + p.stderr
    assert "request" not in out
    assert out["fallback"].startswith("mr_presentation.py --mode create failed")
    assert not (repo.mrtmp / "mr_request.json").exists()


@pytest.mark.parametrize("arg", ["1", "0"])
def test_prepare_draft_arg_skips_the_detector(repo, arg):
    log = repo.tmp / "draft.log"
    repo.stub_lib("check_mr_draft.py", py_stub(3, out="1", err="WARNING: x\n", log=log))
    rc, out, p = repo.run("prepare", "--draft", arg)
    assert rc == 0, p.stdout + p.stderr
    assert out["draft"] == arg and (repo.mrtmp / "mr_draft").read_text() == arg
    assert not log.exists()


@pytest.mark.parametrize("plan,expected", [("**MR draft:** yes\n", "1"), ("**MR draft:** no\n", "0"), (None, "0")])
def test_prepare_draft_from_committed_plan(repo, plan, expected):
    if plan:
        repo.commit("docs/plans/2026-01-01-x.md", "# P\n\n" + plan, "plan")
    rc, out, p = repo.run("prepare")
    assert rc == 0, p.stdout + p.stderr
    assert out["draft"] == expected and (repo.mrtmp / "mr_draft").read_text() == expected


def test_prepare_draft_non_bare_value_stops(repo):
    m = repo.mrtmp
    m.mkdir(parents=True, exist_ok=True)
    (m / "mr_draft").write_text("1")
    repo.commit("docs/plans/2026-01-01-x.md", "# P\n\n**MR draft:** maybe\n", "plan")
    rc, out, p = repo.run("prepare")
    assert rc == 1
    assert out["STOP"].startswith("a plan's MR draft field is uncommitted or not a bare yes/no")
    assert "WARNING" in p.stderr
    assert not (m / "mr_draft").exists()


def test_prepare_draft_detector_failure_stops(repo):
    repo.stub_lib("check_mr_draft.py", py_stub(1, err="check_mr_draft: boom\n"))
    rc, out, _ = repo.run("prepare")
    assert rc == 1 and out["STOP"].startswith("check_mr_draft.py failed (rc 1")
    assert not (repo.mrtmp / "mr_draft").exists()


# --- body ---------------------------------------------------------------------------------------

def test_body_assembles_the_presentation(repo):
    m = repo.write_mrtmp(title=None, body=None)
    (m / "mr_pres.json").write_text(json.dumps({**PRES, "impact_paragraph": "Adds &lt;b&gt; support."}))
    rc, out, p = repo.run("body")
    assert rc == 0, p.stdout + p.stderr
    assert out["body"] == "presentation" and out["title"] == "feat: x"
    text = (m / "mr_body.md").read_text()
    assert text.startswith("### Impact & value") and "Adds <b> support." in text


@pytest.mark.parametrize("pres", [None, "not json", json.dumps({"title": "t"}),
                                  json.dumps({**PRES, "why_it_matters": 5})],
                         ids=["missing", "invalid", "keys-missing", "assemble-crash"])
def test_body_falls_back_to_the_minimal_form(repo, pres):
    m = repo.write_mrtmp(title=None, body=None)
    if pres is not None:
        (m / "mr_pres.json").write_text(pres)
    rc, out, p = repo.run("body")
    assert rc == 0, p.stdout + p.stderr
    assert out["body"] == "fallback"
    assert out["warning"][0].startswith("MR body fell back to the minimal form")
    subject = repo.wgit("log", "-1", "--format=%s")
    assert json.loads((m / "mr_pres.json").read_text()) == {"title": subject}
    assert out["title"] == subject
    text = (m / "mr_body.md").read_text()
    assert "abc1234 feat: add b" in text and "b.txt | 1 +" in text


@pytest.mark.parametrize("title", [None, ""], ids=["missing", "empty"])
def test_body_fills_a_missing_title_from_head(repo, title):
    m = repo.write_mrtmp(title=None, body=None)
    pres = {k: v for k, v in PRES.items() if k != "title"}
    if title is not None:
        pres["title"] = title
    (m / "mr_pres.json").write_text(json.dumps(pres))
    rc, out, p = repo.run("body")
    assert rc == 0, p.stdout + p.stderr
    subject = repo.wgit("log", "-1", "--format=%s")
    assert out["body"] == "presentation" and out["title"] == subject
    assert json.loads((m / "mr_pres.json").read_text())["title"] == subject
    assert any(w.startswith("the presentation had no title") for w in out["warning"])
    assert (m / "mr_body.md").read_text().startswith("### Impact & value")


def test_body_appends_the_also_fixed_section(repo):
    repo.ledger("add", "lib/x.py: tidy")
    m = repo.write_mrtmp(title=None, body=None)
    rc, _, p = repo.run("body")
    assert rc == 0, p.stdout + p.stderr
    assert "### Also fixed" in (m / "mr_body.md").read_text()


def test_body_ledger_failure_warns(repo):
    repo.stub_lib("also_fixed_ledger.py", py_stub(1, err="boom\n"))
    m = repo.write_mrtmp(title=None, body=None)
    rc, out, _ = repo.run("body")
    assert rc == 0 and any(w.startswith("LEDGER_FAILED rc=1") for w in out["warning"])
    assert (m / "mr_body.md").exists()


def test_body_appends_the_ticket_line(repo):
    repo.tracker("PROJ", "pr_body_ticket_line", "Closes #{number}")
    m = repo.write_mrtmp(title=None, body=None, key="PROJ-12")
    rc, _, p = repo.run("body")
    assert rc == 0, p.stdout + p.stderr
    assert (m / "mr_body.md").read_text().rstrip().endswith("Closes #12")


def test_body_ticket_line_failure_warns(repo):
    repo.stub_lib("tracker_config.py", py_stub(1, err="boom\n"))
    repo.write_mrtmp(title=None, body=None, key="PROJ-12")
    rc, out, _ = repo.run("body")
    assert rc == 0 and any(w.startswith("TICKET_LINE_FAILED rc=1") for w in out["warning"])


def test_body_without_context_stops(repo):
    rc, out, _ = repo.run("body")
    assert rc == 1 and "mr_ctx.json" in out["STOP"]


# --- create -------------------------------------------------------------------------------------

def test_gitlab_create_passes_resolved_fields(repo):
    repo.write_mrtmp(title="my title", target="develop", body="the body")
    rc, out, p = repo.run("create")
    assert rc == 0, p.stdout + p.stderr
    calls = repo.calls()
    assert sum(c.startswith("glab [api] [user]") for c in calls) == 1
    [c] = creates(calls)
    for part in ("[--title] [my title]", "[--description] [the body]", "[--assignee] [alice]",
                 f"[--source-branch] [{repo.branch}]", "[--squash-before-merge]", "[--remove-source-branch]",
                 "[--target-branch] [develop]", "[--yes]"):
        assert part in c, part
    assert "--draft" not in c
    assert out["forge"] == "gitlab" and out["url"] == GITLAB_URL


def test_github_create_passes_resolved_fields(repo):
    use_forge(repo, "github")
    m = repo.write_mrtmp(title="my title", target="develop")
    rc, out, p = repo.run("create")
    assert rc == 0, p.stdout + p.stderr
    [c] = creates(repo.calls())
    for part in ("[--title] [my title]", f"[--body-file] [{m}/mr_body.md]", "[--base] [develop]",
                 f"[--head] [{repo.branch}]", "[--assignee] [@me]"):
        assert part in c, part
    assert not any(c.startswith("glab") for c in repo.calls())
    assert out["forge"] == "github" and out["url"] == GITHUB_URL


@pytest.mark.parametrize("forge", ["gitlab", "github"])
def test_forge_calls_run_in_the_worktree(repo, forge):
    use_forge(repo, forge)
    repo.write_mrtmp()
    rc, _, p = repo.run("create")
    assert rc == 0, p.stdout + p.stderr
    calls = repo.calls()
    assert calls and all(c.endswith(f" @{repo.work}") for c in calls)
    assert not any("[-R]" in c for c in calls)


def test_username_persistent_failure_stops_before_create(repo):
    repo.write_mrtmp()
    repo.flag("user_fails", "99")
    rc, out, _ = repo.run("create")
    assert rc == 1 and out["STOP"].startswith("could not resolve the GitLab username after 3 tries")
    assert creates(repo.calls()) == []
    assert sum(c.startswith("glab [api] [user]") for c in repo.calls()) == 3


def test_username_missing_in_answer_stops(repo):
    repo.write_mrtmp()
    repo.flag("no_username")
    rc, out, _ = repo.run("create")
    assert rc == 1 and out["STOP"].startswith("could not resolve the GitLab username after 3 tries")
    assert creates(repo.calls()) == []
    assert sum(c.startswith("glab [api] [user]") for c in repo.calls()) == 3


def test_username_transient_failure_is_retried(repo):
    repo.write_mrtmp()
    repo.flag("user_fails", "2")
    rc, _, p = repo.run("create")
    assert rc == 0, p.stdout + p.stderr
    [c] = creates(repo.calls())
    assert "[--assignee] [alice]" in c
    assert sum(c.startswith("glab [api] [user]") for c in repo.calls()) == 3


@pytest.mark.parametrize("forge", ["gitlab", "github"])
@pytest.mark.parametrize("missing,stop", [
    ("title", "could not resolve the MR title"),
    ("pres_file", "could not resolve the MR title"),
    ("target", "could not resolve the MR target branch"),
    ("body", "could not read the MR body"),
])
def test_missing_inputs_stop_before_any_forge_call(repo, forge, missing, stop):
    use_forge(repo, forge)
    m = repo.write_mrtmp(title="" if missing == "title" else "t",
                         target="" if missing == "target" else "master",
                         body=None if missing == "body" else "b")
    if missing == "pres_file":
        (m / "mr_pres.json").unlink()
    rc, out, _ = repo.run("create")
    assert rc == 1 and out["STOP"].startswith(stop)
    assert repo.calls() == []


def test_unknown_forge_stops(repo):
    repo.env["DETECT_REMOTE_URL"] = "git@bitbucket.org:o/r.git"
    repo.write_mrtmp()
    rc, out, _ = repo.run("create")
    assert rc == 1 and out["STOP"].startswith("could not detect the forge")
    assert repo.calls() == []


def test_forge_flag_overrides_detection(repo):
    repo.env["DETECT_REMOTE_URL"] = "git@bitbucket.org:o/r.git"
    repo.write_mrtmp()
    rc, out, p = repo.run("create", "--forge", "github")
    assert rc == 0, p.stdout + p.stderr
    [c] = creates(repo.calls())
    assert c.startswith("gh [pr] [create]")


@pytest.mark.parametrize("forge", ["gitlab", "github"])
def test_draft_requested_passes_flag(repo, forge):
    use_forge(repo, forge)
    repo.write_mrtmp(draft="1")
    rc, out, p = repo.run("create")
    assert rc == 0, p.stdout + p.stderr
    [c] = creates(repo.calls())
    assert "[--draft]" in c
    assert "draft_marked" not in out


@pytest.mark.parametrize("draft", [None, "0", ""])
@pytest.mark.parametrize("forge", ["gitlab", "github"])
def test_no_draft_omits_flag(repo, forge, draft):
    use_forge(repo, forge)
    repo.write_mrtmp(draft=draft)
    rc, _, p = repo.run("create")
    assert rc == 0, p.stdout + p.stderr
    [c] = creates(repo.calls())
    assert "--draft" not in c and "[]" not in c


MARKS = {"gitlab": "glab [mr] [update]", "github": "gh [pr] [ready]"}


@pytest.mark.parametrize("forge", ["gitlab", "github"])
def test_draft_ignored_by_create_is_marked_once(repo, forge):
    use_forge(repo, forge)
    repo.write_mrtmp(draft="1")
    repo.flag("ignore_draft")
    rc, out, p = repo.run("create")
    assert rc == 0, p.stdout + p.stderr
    assert out["draft_marked"] == "yes"
    marks = [c for c in repo.calls() if c.startswith(MARKS[forge])]
    assert len(marks) == 1
    assert (f"[{repo.branch}] [--draft]" if forge == "gitlab" else f"[{repo.branch}] [--undo]") in marks[0]


@pytest.mark.parametrize("forge", ["gitlab", "github"])
def test_draft_still_missing_after_one_mark_stops(repo, forge):
    use_forge(repo, forge)
    repo.write_mrtmp(draft="1")
    repo.flag("ignore_draft")
    repo.flag("mark_fails")
    rc, out, _ = repo.run("create")
    assert rc == 5 and out["STOP"].startswith("Draft was requested but the MR for")
    assert "url" not in out
    assert len([c for c in repo.calls() if c.startswith(MARKS[forge])]) == 1


def test_gitlab_work_in_progress_counts_as_draft(repo):
    repo.write_mrtmp(draft="1")
    repo.flag("draft_key", "work_in_progress")
    rc, out, p = repo.run("create")
    assert rc == 0, p.stdout + p.stderr
    assert not any(c.startswith(MARKS["gitlab"]) for c in repo.calls())


def test_gitlab_work_in_progress_false_stops(repo):
    repo.write_mrtmp(draft="1")
    repo.flag("draft_key", "work_in_progress")
    repo.flag("ignore_draft")
    repo.flag("mark_fails")
    rc, out, _ = repo.run("create")
    assert rc == 5 and out["STOP"].startswith("Draft was requested")


@pytest.mark.parametrize("forge", ["gitlab", "github"])
def test_source_branch_mismatch_stops_without_url(repo, forge):
    use_forge(repo, forge)
    repo.write_mrtmp()
    repo.flag("source_branch", "other")
    rc, out, _ = repo.run("create")
    assert rc == 5 and out["STOP"].startswith("MR source_branch mismatch or no MR found for branch")
    assert "url" not in out and "transition" not in out


@pytest.mark.parametrize("forge", ["gitlab", "github"])
def test_no_mr_after_create_stops(repo, forge):
    use_forge(repo, forge)
    repo.write_mrtmp()
    repo.flag("no_mr")
    rc, out, _ = repo.run("create")
    assert rc == 5 and "url" not in out


@pytest.mark.parametrize("forge", ["gitlab", "github"])
def test_failed_create_call_never_retries_or_verifies(repo, forge):
    use_forge(repo, forge)
    log = repo.transition_stub()
    repo.write_mrtmp(key="PROJ-12")
    repo.flag("create_fails")
    rc, out, p = repo.run("create")
    assert rc == 4
    assert out["STOP"].startswith("create call failed") and "may exist anyway" in out["STOP"]
    assert "already exists" in p.stderr
    calls = repo.calls()
    assert len(creates(calls)) == 1
    assert not any(c.startswith(("glab [mr] [view]", "gh [pr] [view]")) for c in calls)
    assert not log.exists()


def test_success_records_event_clears_ledger_and_transitions(repo):
    tlog = repo.tmp / "telemetry.log"
    repo.stub_lib("flow_telemetry.py", py_stub(0, out="event: gate -> x\n", log=tlog))
    log = repo.transition_stub()
    repo.ledger("add", "lib/x.py: tidy")
    repo.write_mrtmp(key="PROJ-12")
    rc, out, p = repo.run("create")
    assert rc == 0, p.stdout + p.stderr
    assert tlog.read_text().split() == ["event", "--type", "gate", "--cwd", str(repo.work),
                                        "--field", "skill=simo-dev:commit", "--field", "gate=mr_created",
                                        "--field", "answer=created"]
    assert repo.ledger("section") == ""
    assert log.read_text().split() == ["PROJ-12", "CodeReview", "--leaf-only", "--sync-parent", "--clear-blocked"]
    assert out["transition"] == "PROJ-12 -> CodeReview"


def test_transition_failure_only_warns(repo):
    repo.transition_stub(rc=7)
    repo.write_mrtmp(key="PROJ-12")
    rc, out, p = repo.run("create")
    assert rc == 0, p.stdout + p.stderr
    assert any(w.startswith("transition_cmd failed for PROJ-12 -> CodeReview (rc 7") for w in out["warning"])


def test_no_ticket_key_skips_transition(repo):
    log = repo.transition_stub()
    repo.write_mrtmp(key="")
    rc, out, _ = repo.run("create")
    assert rc == 0 and out["transition"] == "skipped (no ticket key)" and not log.exists()


def test_telemetry_failure_only_warns(repo):
    repo.stub_lib("flow_telemetry.py", py_stub(2, err="flow_telemetry: boom\n"))
    repo.write_mrtmp()
    rc, out, _ = repo.run("create")
    assert rc == 0 and any(w.startswith("flow_telemetry.py event failed (rc 2") for w in out["warning"])


def test_ledger_clear_failure_only_warns(repo):
    repo.stub_lib("also_fixed_ledger.py", py_stub(1, err="boom\n"))
    repo.write_mrtmp()
    rc, out, _ = repo.run("create")
    assert rc == 0 and any(w.startswith("LEDGER_FAILED rc=1") for w in out["warning"])


# --- transition ---------------------------------------------------------------------------------

def test_transition_subcommand_runs_codereview_alone(repo):
    log = repo.transition_stub()
    repo.write_mrtmp(key="PROJ-12")
    rc, out, p = repo.run("transition")
    assert rc == 0, p.stdout + p.stderr
    assert log.read_text().split() == ["PROJ-12", "CodeReview", "--leaf-only", "--sync-parent", "--clear-blocked"]
    assert out["transition"] == "PROJ-12 -> CodeReview"
    assert repo.calls() == []


def test_transition_without_context_warns(repo):
    rc, out, _ = repo.run("transition")
    assert rc == 0 and out["warning"][0].startswith("transition skipped")
