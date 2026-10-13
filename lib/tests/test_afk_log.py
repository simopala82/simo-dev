"""Tests for lib/afk_log.py (afk no-plan decision-log without naming the state dir)."""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).parent.parent / "afk_log.py"

spec = importlib.util.spec_from_file_location("afk_log", LIB)
afk_log = importlib.util.module_from_spec(spec)
spec.loader.exec_module(afk_log)


def git_repo(tmp_path, branch="feat/x"):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", branch, str(repo)], check=True)
    return repo


def run(repo, *args, log_dir, stdin=""):
    env = {**os.environ, "AFK_LOG_DIR": str(log_dir)}
    return subprocess.run(
        [sys.executable, str(LIB), *args, "--repo", str(repo)],
        input=stdin.encode("utf-8"), capture_output=True, env=env)


def test_log_path_flattens_branch(tmp_path, monkeypatch):
    repo = git_repo(tmp_path, "fix/a/b")
    monkeypatch.setenv("AFK_LOG_DIR", str(tmp_path / "state"))
    assert afk_log.log_path(str(repo)) == tmp_path / "state" / "afk-fix-a-b.md"


def test_log_path_default_dir_is_claude_state(tmp_path, monkeypatch):
    repo = git_repo(tmp_path)
    monkeypatch.delenv("AFK_LOG_DIR", raising=False)
    assert afk_log.log_path(str(repo)) == Path.home() / ".claude" / "state" / "afk-feat-x.md"


def test_append_creates_dir_and_file(tmp_path):
    repo = git_repo(tmp_path)
    out = run(repo, "append", stdin="- [gate] direction → y\n", log_dir=tmp_path / "state")
    log = tmp_path / "state" / "afk-feat-x.md"
    assert out.returncode == 0, out.stderr
    assert log.read_text(encoding="utf-8") == "- [gate] direction → y\n"
    assert out.stdout.decode().strip() == f"afk_log: appended 1 lines to {log}"


def test_append_adds_missing_newline_before_entry(tmp_path):
    log = tmp_path / "l.md"
    log.write_text("RESUME: x\n- a", encoding="utf-8")
    assert afk_log.append(log, "- b\n- c\n") == 0
    assert log.read_text(encoding="utf-8") == "RESUME: x\n- a\n- b\n- c\n"


def test_append_drops_trailing_blank_lines(tmp_path):
    log = tmp_path / "l.md"
    assert afk_log.append(log, "- x\n  \n") == 0
    assert log.read_text(encoding="utf-8") == "- x\n"


def test_append_empty_is_rc4_and_writes_nothing(tmp_path):
    repo = git_repo(tmp_path)
    out = run(repo, "append", stdin="\n  \n", log_dir=tmp_path / "state")
    assert out.returncode == 4
    assert not (tmp_path / "state").exists()


def test_resume_inserts_line_at_top(tmp_path):
    log = tmp_path / "l.md"
    log.write_text("## AFK run\n- a\n", encoding="utf-8")
    assert afk_log.resume(log, "step 3, worktree /w\n") == 0
    assert log.read_text(encoding="utf-8") == "RESUME: step 3, worktree /w\n## AFK run\n- a\n"


def test_resume_replaces_existing_line_and_flattens(tmp_path):
    log = tmp_path / "l.md"
    log.write_text("RESUME: old\n## AFK run\n- a\n", encoding="utf-8")
    assert afk_log.resume(log, "RESUME: new\n  two lines\n") == 0
    assert log.read_text(encoding="utf-8") == "RESUME: new two lines\n## AFK run\n- a\n"


def test_resume_cli_rewrites_top_line(tmp_path):
    repo = git_repo(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    (state / "afk-feat-x.md").write_text("RESUME: old\n- a\n", encoding="utf-8")
    out = run(repo, "resume", stdin="step 2\n", log_dir=state)
    assert out.returncode == 0, out.stderr
    assert (state / "afk-feat-x.md").read_text(encoding="utf-8") == "RESUME: step 2\n- a\n"


def test_resume_on_missing_log_creates_it(tmp_path):
    log = tmp_path / "d" / "l.md"
    assert afk_log.resume(log, "start") == 0
    assert log.read_text(encoding="utf-8") == "RESUME: start\n"


def test_resume_empty_is_rc4(tmp_path):
    log = tmp_path / "l.md"
    assert afk_log.resume(log, " \n") == 4
    assert not log.exists()


def test_show_prints_log(tmp_path):
    repo = git_repo(tmp_path)
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "afk-feat-x.md").write_text("RESUME: r\n- a\n", encoding="utf-8")
    out = run(repo, "show", log_dir=tmp_path / "state")
    assert out.returncode == 0
    assert out.stdout.decode() == "RESUME: r\n- a\n"


def test_show_missing_is_rc3(tmp_path):
    repo = git_repo(tmp_path)
    out = run(repo, "show", log_dir=tmp_path / "state")
    assert out.returncode == 3
    assert out.stdout == b""


def test_show_non_ascii_on_cp1252(tmp_path):
    from cp1252_run import run_cp1252
    repo = git_repo(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    (state / "afk-feat-x.md").write_text("RESUME: r\n- [gate] direction → y ★\n", encoding="utf-8")
    out = run_cp1252(LIB, ["show", "--repo", str(repo)], extra_env={"AFK_LOG_DIR": str(state)})
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")
    assert out.stdout.decode("utf-8") == "RESUME: r\n- [gate] direction → y ★\n"


def test_append_non_ascii_stdin_on_cp1252(tmp_path):
    from cp1252_run import run_cp1252
    repo = git_repo(tmp_path)
    state = tmp_path / "state"
    out = run_cp1252(LIB, ["append", "--repo", str(repo)], extra_env={"AFK_LOG_DIR": str(state)},
                     stdin="- a → b ★\n".encode("utf-8"))
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")
    assert (state / "afk-feat-x.md").read_text(encoding="utf-8") == "- a → b ★\n"


def test_missing_repo_is_usage_error(tmp_path):
    out = run(tmp_path / "nope", "append", stdin="- x\n", log_dir=tmp_path / "state")
    assert out.returncode == 2
    assert b"--repo: not a directory" in out.stderr
    assert not (tmp_path / "state").exists()


def fenced_lines(md: Path) -> list[str]:
    in_fence, code = False, []
    for line in md.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            code.append(line)
    return code


def test_afk_skill_code_never_names_the_state_dir():
    # A `.claude/state` path on a command line brings back the non-persistable "Allow once"
    # prompt in sessions outside this repo; the no-plan log must go through afk_log.py.
    # Fenced blocks only: inline prose calls are out of scope by construction, like
    # test_plugin_root_form.py.
    afk_dir = LIB.parent.parent / "skills" / "afk"
    docs = sorted(afk_dir.rglob("*.md"))
    assert docs
    code = [l for md in docs for l in fenced_lines(md)]
    assert not [l for l in code if ".claude/state" in l]
    for sub in ("append", "resume", "show"):
        assert any(f'lib/afk_log.py" {sub} --repo "<worktree-abs>"' in l for l in code), sub
