"""lib/plan_execute_preflight.py: plan-execute step 1's deterministic facts in one call.

Cases ported from plan-execute SKILL.md step 1 at 64fb8699 before the script existed: the **Base**
block's `<ledger-base>` resolution (given base with an origin ref, given base without one, origin/HEAD,
main/master fallback, none), the checkbox step count (`grep -cE '^[[:space:]]*- \\[ \\]'`), the
`Depends on:` header field, the `plan_execute` model label, and the gate's first-match order.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "plan_execute_preflight.py"
ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
       "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
                          env=ENV).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "wt"
    r.mkdir()
    git(r, "init", "-q", "-b", "work")
    (r / "a.txt").write_text("a\n")
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "init")
    return r


def remote_ref(repo, name):
    git(repo, "update-ref", f"refs/remotes/origin/{name}", "HEAD")


def run(repo, plan, *extra, config=None, tmp=None):
    env = dict(ENV)
    env["SIMO_DEV_MODEL_STRATEGY_CONFIG"] = str(config or (Path(tmp or repo) / "no-model-strategy.json"))
    proc = subprocess.run([sys.executable, str(SCRIPT), str(plan), "--repo", str(repo), *extra],
                          capture_output=True, text=True, env=env)
    return proc.returncode, proc.stdout, proc.stderr


def field(out, key):
    for line in out.splitlines():
        if line.startswith(f"{key}: ") or line.startswith(f"{key}="):
            return line.split(": ", 1)[1] if line.startswith(f"{key}: ") else line.split("=", 1)[1]
    return None


def plan_with(tmp_path, tasks, header=""):
    """tasks: list of (files, n_steps)."""
    parts = ["# Plan\n", "**Goal:** x\n", header]
    for i, (files, steps) in enumerate(tasks, 1):
        parts.append(f"\n### Task {i}: t{i}\n\n**Files:**\n")
        parts += [f"- Modify: `{f}`\n" for f in files]
        parts.append("\n")
        parts += [f"- [ ] **Step {s}: do**\n" for s in range(1, steps + 1)]
    p = tmp_path / "plan.md"
    p.write_text("".join(parts))
    return p


# --- <ledger-base> ---------------------------------------------------------------------------

def test_given_base_with_origin_ref(repo, tmp_path):
    remote_ref(repo, "develop")
    rc, out, _ = run(repo, plan_with(tmp_path, [(["a"], 1)]), "--base", "develop")
    assert rc == 0
    assert "LEDGER_BASE=develop" in out.splitlines()
    assert "LEDGER_BASE_FALLBACK" not in out


def test_given_base_without_origin_ref_falls_back_to_origin_head(repo, tmp_path):
    remote_ref(repo, "trunk")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    rc, out, _ = run(repo, plan_with(tmp_path, [(["a"], 1)]), "--base", "parent-branch")
    assert rc == 0
    assert "LEDGER_BASE_FALLBACK: no origin/parent-branch" in out.splitlines()
    assert "LEDGER_BASE=trunk" in out.splitlines()


def test_unknown_base_uses_origin_head_silently(repo, tmp_path):
    remote_ref(repo, "trunk")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    rc, out, _ = run(repo, plan_with(tmp_path, [(["a"], 1)]))
    assert "LEDGER_BASE=trunk" in out.splitlines()
    assert "LEDGER_BASE_FALLBACK" not in out


@pytest.mark.parametrize("refs, expected", [(["main", "master"], "main"), (["master"], "master")])
def test_no_origin_head_falls_back_to_main_then_master(repo, tmp_path, refs, expected):
    for r in refs:
        remote_ref(repo, r)
    rc, out, _ = run(repo, plan_with(tmp_path, [(["a"], 1)]))
    assert f"LEDGER_BASE={expected}" in out.splitlines()


def test_nothing_resolves_prints_empty_ledger_base(repo, tmp_path):
    rc, out, _ = run(repo, plan_with(tmp_path, [(["a"], 1)]), "--base", "gone")
    assert rc == 0
    assert "LEDGER_BASE_FALLBACK: no origin/gone" in out.splitlines()
    assert "LEDGER_BASE=" in out.splitlines()


# --- step count ------------------------------------------------------------------------------

def test_step_count_matches_the_grep_it_replaced(repo, tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text(
        "### Task 1: A\n"
        "**Files:**\n- Modify: `a.py`\n\n"
        "- [ ] **Step 1: Write the failing test**\n"
        "  - [ ] nested checkbox\n"
        "\t- [ ] tab-indented\n"
        "- [x] **Step 2: done already**\n"
        "text - [ ] not at line start\n"
    )
    _, out, _ = run(repo, plan)
    assert field(out, "steps") == "3"
    grep = subprocess.run(["grep", "-cE", r"^[[:space:]]*- \[ \]", str(plan)], capture_output=True, text=True)
    assert grep.stdout.strip() == "3"


def test_bare_plan_counts_zero_and_runs_direct(repo, tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text("**Goal:** x\n\n### Task 1: do it\n**Files:**\n- Modify: `a.py`\n")
    _, out, _ = run(repo, plan)
    assert field(out, "steps") == "0"
    assert field(out, "execution").startswith("direct")


def test_plan_without_task_headings_prints_no_batches(repo, tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text("**Goal:** x\n- [ ] one\n")
    rc, out, _ = run(repo, plan)
    assert rc == 0
    assert field(out, "batches") == "none"
    assert field(out, "execution").startswith("direct")


def test_missing_plan_exits_2(repo, tmp_path):
    rc, out, err = run(repo, tmp_path / "nope.md")
    assert rc == 2
    assert "not found" in err


# --- Depends on ------------------------------------------------------------------------------

def test_depends_on_absent(repo, tmp_path):
    _, out, _ = run(repo, plan_with(tmp_path, [(["a"], 1)]))
    assert field(out, "depends-on") == "none"


def test_depends_on_key(repo, tmp_path):
    header = "**Depends on:** PROJ-42 — assumed shape: `foo()` returns a list\n"
    _, out, _ = run(repo, plan_with(tmp_path, [(["a"], 1)], header=header))
    assert field(out, "depends-on") == "PROJ-42"


def test_depends_on_backticked_key(repo, tmp_path):
    header = "**Depends on:** `PROJ-42` — assumed shape: x\n"
    _, out, _ = run(repo, plan_with(tmp_path, [(["a"], 1)], header=header))
    assert field(out, "depends-on") == "PROJ-42"


def test_depends_on_without_a_key_is_not_silently_none(repo, tmp_path):
    header = "**Depends on:** the auth sub-task — assumed shape: x\n"
    _, out, _ = run(repo, plan_with(tmp_path, [(["a"], 1)], header=header))
    assert field(out, "depends-on").startswith("unparsed: ")


@pytest.mark.parametrize("value", ["none.", "none — no prerequisite", "None"])
def test_depends_on_none_values_are_none(repo, tmp_path, value):
    _, out, _ = run(repo, plan_with(tmp_path, [(["a"], 1)], header=f"**Depends on:** {value}\n"))
    assert field(out, "depends-on") == "none"


@pytest.mark.parametrize("header", ["Depends on: PROJ-9 — x\n", "- Depends on: PROJ-9\n", "> **Depends on:** PROJ-9\n",
                                    "**Depends on**: PROJ-9 — x\n", "**Depends On:** PROJ-9\n"])
def test_depends_on_plain_and_prefixed_forms(repo, tmp_path, header):
    _, out, _ = run(repo, plan_with(tmp_path, [(["a"], 1)], header=header))
    assert field(out, "depends-on") == "PROJ-9"


def test_depends_on_outside_the_header_is_ignored(repo, tmp_path):
    plan = plan_with(tmp_path, [(["a"], 1)])
    plan.write_text(plan.read_text() + "\n- Depends on: Task 2\n\n```markdown\n  **Depends on:** [optional]\n```\n")
    _, out, _ = run(repo, plan)
    assert field(out, "depends-on") == "none"


def test_depends_on_in_a_fenced_block_inside_the_header_is_ignored(repo, tmp_path):
    header = "```markdown\n**Depends on:** PROJ-1\n```\n"
    _, out, _ = run(repo, plan_with(tmp_path, [(["a"], 1)], header=header))
    assert field(out, "depends-on") == "none"


# --- model -----------------------------------------------------------------------------------

def test_model_empty_prints_the_inherit_label(repo, tmp_path):
    _, out, _ = run(repo, plan_with(tmp_path, [(["a"], 1)]), tmp=tmp_path)
    assert field(out, "model") == "inherit caller's tier"


def test_model_configured(repo, tmp_path):
    cfg = tmp_path / "ms.json"
    cfg.write_text('{"plan_execute": "sonnet"}\n')
    _, out, _ = run(repo, plan_with(tmp_path, [(["a"], 1)]), config=cfg)
    assert field(out, "model") == "sonnet"


@pytest.mark.parametrize("content", [b"{not json", b"[]", b'"a string"', b"\xff\xfe"])
def test_invalid_model_config_keeps_the_other_lines(repo, tmp_path, content):
    cfg = tmp_path / "ms.json"
    cfg.write_bytes(content)
    rc, out, err = run(repo, plan_with(tmp_path, [(["a"], 1)]), config=cfg)
    assert rc == 0
    assert field(out, "model") == "inherit caller's tier"
    assert field(out, "steps") == "1" and field(out, "execution").startswith("direct")
    assert "model-strategy" in err


# --- execution gate, first match wins -------------------------------------------------------

def test_twelve_steps_is_direct(repo, tmp_path):
    _, out, _ = run(repo, plan_with(tmp_path, [(["a"], 6), (["b"], 6)]))
    assert field(out, "steps") == "12"
    assert field(out, "execution").startswith("direct — ")


def test_over_twelve_with_a_parallel_batch_is_agent_dispatch_candidate(repo, tmp_path):
    _, out, _ = run(repo, plan_with(tmp_path, [(["a"], 7), (["b"], 6)]))
    assert field(out, "batches") == "1 2"
    assert field(out, "execution").startswith("agent-dispatch? — ")


def test_over_twelve_all_sequential_is_subagent(repo, tmp_path):
    _, out, _ = run(repo, plan_with(tmp_path, [(["a"], 7), (["a"], 6)]))
    assert field(out, "batches") == "1 | 2"
    assert field(out, "execution").startswith("subagent — ")


def test_output_key_order(repo, tmp_path):
    _, out, _ = run(repo, plan_with(tmp_path, [(["a"], 1)]))
    keys = [ln.split("=")[0] if ln.startswith("LEDGER_BASE=") else ln.split(": ")[0] for ln in out.splitlines()]
    assert keys == ["LEDGER_BASE", "steps", "batches", "depends-on", "model", "execution"]


def test_without_a_plan_prints_only_the_ledger_base(repo, tmp_path):
    # docs/canonical-flow.md "Ad-hoc edit check" needs <ledger-base> with no plan at hand.
    remote_ref(repo, "develop")
    env = {**ENV, "SIMO_DEV_MODEL_STRATEGY_CONFIG": str(tmp_path / "none.json")}
    proc = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(repo), "--base", "gone"],
                          capture_output=True, text=True, env=env)
    assert proc.returncode == 0
    assert proc.stdout.splitlines() == ["LEDGER_BASE_FALLBACK: no origin/gone", "LEDGER_BASE="]
