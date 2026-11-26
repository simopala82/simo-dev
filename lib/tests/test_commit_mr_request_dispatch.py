"""Pins commit steps 7 and 9 to lib/mr_create.py, and step 7's MR-presentation dispatch.

The main session never Reads `mr_request.json`: it carries the whole diff (~11k tokens), and a Read in
the main session stays in context to the end of the task. `mr_create.py prepare` prints `agent_type`,
`model` and the `request:` path; the agent gets a short prompt naming the file and reads it itself, and
the scratchpad line is still appended at dispatch. What the script decides is tested in
test_mr_create.py; this file pins the body text around it and runs the prepare fence as written,
under bash and zsh (the Bash tool's shell).
"""
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
COMMIT = (ROOT / "skills/commit/SKILL.md").read_text()
STEP7 = COMMIT[COMMIT.index("### 7."):COMMIT.index("### 8.")]
STEP9 = COMMIT[COMMIT.index("### 9."):COMMIT.index("### 10.")]
SPAN = COMMIT[COMMIT.index("### 7."):COMMIT.index("### 10.")]
W_LINE = 'W="<absolute path of the task worktree>"'
TARGET_LINE = 'TARGET_ARG="<the --target skill arg, or empty>"'
DRAFT_LINE = 'DRAFT_ARG="<1 if the --draft skill arg was given, 0 if --no-draft was given, else empty>"'
SHELLS = [
    pytest.param(["bash", "-c"], id="bash"),
    pytest.param(["zsh", "-f", "-c"], id="zsh",
                 marks=pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh not installed")),
]


def _fence(section, needle):
    blocks = [b for b in re.findall(r"```bash\n(.*?)```", section, re.S) if needle in b]
    assert len(blocks) == 1, (needle, len(blocks))
    return blocks[0]


def test_main_session_does_not_read_the_request():
    assert "Read `$MRTMP/mr_request.json`" not in COMMIT
    assert "Never Read `mr_request.json` in this session" in STEP7


def test_step7_runs_prepare_then_dispatch_then_body():
    prepare = STEP7.index(_fence(STEP7, 'lib/mr_create.py" prepare --repo "$W"'))
    dispatch = STEP7.index("`subagent_type` = the `agent_type:` value")
    body = STEP7.index(_fence(STEP7, 'lib/mr_create.py" body --repo "$W"'))
    assert prepare < dispatch < body


def test_step9_runs_create_and_keeps_the_verification_notes():
    _fence(STEP9, 'lib/mr_create.py" create --repo "$W"')
    assert "verification-notes.md" in STEP9
    assert "MR created:" in STEP9


def test_steps_7_to_9_issue_no_forge_call_and_bind_no_mrtmp():
    for needle in ("glab mr create", "gh pr create", "glab api user", "glab mr view", "gh pr view",
                   "MRTMP=", '"$MRTMP/'):
        assert needle not in SPAN, needle


def test_dispatch_prompt_and_scratchpad_line():
    for part in ("`subagent_type` = the `agent_type:` value", "`model` = the `model:` value",
                 "Read `<request path>` with the Read tool, the whole file.", "Its `schema` field",
                 "never write into or overwrite files in the Scratchpad directory your environment lists",
                 "No `request:` line", "`<mrtmp>/mr_pres.json`"):
        assert part in STEP7, part


@pytest.mark.parametrize("shell", SHELLS)
def test_prepare_fence_runs_as_written(tmp_path, shell):
    tmp = tmp_path.resolve()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "SIMO_DEV_", "DEV_FLOW_", "CLAUDE_"))}
    env.update(GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t", DEV_FLOW_NO_TRACKER="1", HOME=str(tmp),
               SIMO_DEV_TRACKER_CONFIG=str(tmp / "tracker.json"), CLAUDE_PLUGIN_ROOT=str(ROOT))

    def git(*args):
        subprocess.run(["git", *args], check=True, capture_output=True, env=env)

    git("init", "-q", "--bare", "-b", "master", str(tmp / "o.git"))
    git("clone", "-q", str(tmp / "o.git"), str(tmp / "seed"))
    (tmp / "seed" / "a.txt").write_text("a\n")
    git("-C", str(tmp / "seed"), "add", "a.txt")
    git("-C", str(tmp / "seed"), "commit", "-qm", "init")
    git("-C", str(tmp / "seed"), "push", "-q", "origin", "master")
    git("clone", "-q", str(tmp / "o.git"), str(tmp / "w"))
    branch = f"feat/fence-{uuid.uuid4().hex[:12]}"
    git("-C", str(tmp / "w"), "switch", "-q", "-c", branch)
    (tmp / "w" / "b.txt").write_text("b\n")
    git("-C", str(tmp / "w"), "add", "b.txt")
    git("-C", str(tmp / "w"), "commit", "-qm", "feat: b")
    block = _fence(STEP7, 'lib/mr_create.py" prepare --repo "$W"')
    for line in (W_LINE, TARGET_LINE, DRAFT_LINE):
        assert line in block, line
    block = (block.replace(W_LINE, f'W="{tmp / "w"}"').replace(TARGET_LINE, 'TARGET_ARG=""')
             .replace(DRAFT_LINE, 'DRAFT_ARG=""'))
    mrtmp = Path(f"/tmp/claude_mr_w_{branch.replace('/', '-')}")
    try:
        res = subprocess.run([*shell, block], capture_output=True, text=True, env=env, cwd=tmp)
        assert res.returncode == 0, res.stdout + res.stderr
        assert f"mrtmp: {mrtmp}" in res.stdout and "request: " in res.stdout
    finally:
        shutil.rmtree(mrtmp, ignore_errors=True)
