"""Runs commit/SKILL.md step 9's create blocks against stub glab/gh.

The assignee used to be resolved inside the create's own command substitution, so a failed
`glab api user` (observed 2026-09-30: `net/http: TLS handshake timeout`) became
`--assignee ""` and the MR was created unassigned at rc 0. Title and target branch had the
same shape: an empty `--target-branch` makes glab fall back to the project's default branch
(glab mr_create.go, `getTargetBranch`); gh without `--base` does the same per its --help, and
that `--base ""` behaves like an absent flag is derived, not measured.
Behavior, not shape: each block is extracted verbatim and executed under bash and zsh (the
Bash tool's shell), and the stubs record every call.
"""
import json
import os
import re
import shutil
import stat
import subprocess
import uuid
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[2]
SKILL = PLUGIN / "skills" / "commit" / "SKILL.md"
PLACEHOLDER = 'W="<absolute path of the task worktree>"'

GLAB_STUB = """#!/bin/sh
{ printf glab; printf ' [%s]' "$@"; echo; } >> "$STUB_LOG"
if [ "$1 $2" = "api user" ]; then
  n=$(cat "$GLAB_FAILS" 2>/dev/null || echo 0)
  if [ "$n" -gt 0 ]; then
    echo $((n - 1)) > "$GLAB_FAILS"
    echo "net/http: TLS handshake timeout" >&2
    exit 1
  fi
  if [ -n "${GLAB_NO_USERNAME:-}" ]; then
    echo '{"message": "401 Unauthorized"}'
    exit 0
  fi
  echo '{"username": "alice"}'
  exit 0
fi
exit 0
"""

GH_STUB = """#!/bin/sh
{ printf gh; printf ' [%s]' "$@"; echo; } >> "$STUB_LOG"
exit 0
"""

SHELLS = [
    pytest.param(["bash", "-c"], id="bash"),
    pytest.param(["zsh", "-f", "-c"], id="zsh",
                 marks=pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh not installed")),
]
FORGES = {"gitlab": ("GitLab", "glab mr create"), "github": ("GitHub", "gh pr create")}


def _create_block(forge: str) -> str:
    label, create_cmd = FORGES[forge]
    section = SKILL.read_text().split("### 9. Create MR", 1)[1]
    after = section.split(f"**{label}:**", 1)[1]
    m = re.search(r"```bash\n(.*?)```", after, re.S)
    assert m, f"{label} create block not found"
    assert create_cmd in m.group(1)
    assert PLACEHOLDER in m.group(1)
    return m.group(1)


def _stub(bindir: Path, name: str, body: str):
    p = bindir / name
    p.write_text(body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


def _run(tmp_path: Path, shell, forge="gitlab", fails=0, no_username=False, title="t", target="master",
         substitute_w=True):
    wt = tmp_path / "wt"
    wt.mkdir()
    branch = f"fix/create-guard-{uuid.uuid4().hex[:12]}"
    subprocess.run(["git", "init", "-q", "-b", branch, str(wt)], check=True)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _stub(bindir, "glab", GLAB_STUB)
    _stub(bindir, "gh", GH_STUB)
    log = tmp_path / "stub.log"
    counter = tmp_path / "fails"
    counter.write_text(str(fails))
    common = subprocess.run(
        ["git", "-C", str(wt), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    mrtmp = Path(f"/tmp/claude_mr_{Path(common).parent.name}_{branch.replace('/', '-')}")
    mrtmp.mkdir()
    try:
        (mrtmp / "mr_pres.json").write_text(json.dumps({"title": title}))
        (mrtmp / "mr_body.md").write_text("b")
        (mrtmp / "mr_ctx.json").write_text(json.dumps({"branch_meta": {"target": target}}))
        block = _create_block(forge)
        if substitute_w:
            block = block.replace(PLACEHOLDER, f'W="{wt}"')
        env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "CLAUDE_PLUGIN_ROOT": str(PLUGIN),
               "STUB_LOG": str(log), "GLAB_FAILS": str(counter)}
        if no_username:
            env["GLAB_NO_USERNAME"] = "1"
        res = subprocess.run([*shell, block], capture_output=True, text=True, env=env, cwd=tmp_path)
    finally:
        shutil.rmtree(mrtmp, ignore_errors=True)
    calls = log.read_text().splitlines() if log.exists() else []
    return res, calls


def _creates(calls):
    return [c for c in calls if c.startswith(("glab [mr] [create]", "gh [pr] [create]"))]


@pytest.mark.parametrize("shell", SHELLS)
def test_persistent_failure_stops_before_create(tmp_path, shell):
    res, calls = _run(tmp_path, shell, fails=99)
    assert res.returncode == 1
    assert "STOP: could not resolve the GitLab username" in res.stderr
    assert _creates(calls) == []
    assert sum(c == "glab [api] [user]" for c in calls) == 3


@pytest.mark.parametrize("shell", SHELLS)
def test_rc0_without_username_stops_before_create(tmp_path, shell):
    res, calls = _run(tmp_path, shell, no_username=True)
    assert res.returncode == 1
    assert "STOP: could not resolve the GitLab username" in res.stderr
    assert _creates(calls) == []
    assert sum(c == "glab [api] [user]" for c in calls) == 3


@pytest.mark.parametrize("shell", SHELLS)
def test_transient_failure_retried_then_assigned(tmp_path, shell):
    res, calls = _run(tmp_path, shell, fails=2)
    assert res.returncode == 0, res.stderr
    creates = _creates(calls)
    assert len(creates) == 1
    assert "[--assignee] [alice]" in creates[0]


@pytest.mark.parametrize("shell", SHELLS)
def test_gitlab_first_try_passes_resolved_fields(tmp_path, shell):
    res, calls = _run(tmp_path, shell, title="my title", target="develop")
    assert res.returncode == 0, res.stderr
    assert sum(c == "glab [api] [user]" for c in calls) == 1
    creates = _creates(calls)
    assert len(creates) == 1
    assert "[--title] [my title]" in creates[0]
    assert "[--target-branch] [develop]" in creates[0]
    assert "[--assignee] [alice]" in creates[0]


@pytest.mark.parametrize("shell", SHELLS)
def test_github_passes_resolved_fields(tmp_path, shell):
    res, calls = _run(tmp_path, shell, forge="github", title="my title", target="develop")
    assert res.returncode == 0, res.stderr
    creates = _creates(calls)
    assert len(creates) == 1
    assert "[--title] [my title]" in creates[0]
    assert "[--base] [develop]" in creates[0]


@pytest.mark.parametrize("forge", ["gitlab", "github"])
@pytest.mark.parametrize("shell", SHELLS)
def test_empty_target_stops_before_create(tmp_path, shell, forge):
    res, calls = _run(tmp_path, shell, forge=forge, target="")
    assert res.returncode == 1
    assert "STOP: could not resolve the MR target branch" in res.stderr
    assert _creates(calls) == []


@pytest.mark.parametrize("forge", ["gitlab", "github"])
@pytest.mark.parametrize("shell", SHELLS)
def test_empty_title_stops_before_create(tmp_path, shell, forge):
    res, calls = _run(tmp_path, shell, forge=forge, title="")
    assert res.returncode == 1
    assert "STOP: could not resolve the MR title" in res.stderr
    assert _creates(calls) == []


@pytest.mark.parametrize("forge", ["gitlab", "github"])
@pytest.mark.parametrize("shell", SHELLS)
def test_unsubstituted_w_stops_before_create(tmp_path, shell, forge):
    if Path("/tmp/claude_mr_._").exists():
        pytest.skip("/tmp/claude_mr_._ left behind by an earlier unsubstituted-$W step 7: its files can resolve title/target")
    res, calls = _run(tmp_path, shell, forge=forge, substitute_w=False)
    assert res.returncode == 1
    assert "STOP: could not resolve" in res.stderr
    assert "STOP: could not resolve the MR title" in res.stderr
    assert "/tmp/claude_mr_._/mr_pres.json" in res.stderr
    assert _creates(calls) == []
    assert not any(c.startswith("glab [api] [user]") for c in calls)
