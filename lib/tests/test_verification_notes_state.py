"""Tests for lib/verification_notes_state.py."""
import json
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).parent.parent / "verification_notes_state.py"
MR = "https://gitlab.example.com/g/r/-/merge_requests/7"


def run_lib(args):
    r = subprocess.run([sys.executable, str(LIB)] + args, capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "feat/x", str(repo)], check=True)
    return repo


def body(tmp_path, text):
    f = tmp_path / f"body-{len(list(tmp_path.glob('body-*')))}.txt"
    f.write_text(text)
    return str(f)


def cmd(repo, *args):
    return run_lib([args[0], "--repo", str(repo), "--mr", MR, *args[1:]])


def test_has_and_show_before_and_after_record(tmp_path):
    repo = make_repo(tmp_path)
    assert cmd(repo, "has")[:2] == (0, "no\n")
    assert cmd(repo, "show")[:2] == (0, "")
    assert cmd(repo, "record", "--head", "aaa", "--body", body(tmp_path, "a\nb\n"))[0] == 0
    assert cmd(repo, "has")[1] == "yes\n"
    assert cmd(repo, "show")[1] == "a\nb\n"
    assert run_lib(["has", "--repo", str(repo), "--mr", MR + "8"])[1] == "no\n"


def test_stale_keys_on_head(tmp_path):
    repo = make_repo(tmp_path)
    assert cmd(repo, "stale", "--head", "aaa")[1] == "yes\n"
    cmd(repo, "record", "--head", "aaa", "--body", body(tmp_path, "a"))
    assert cmd(repo, "stale", "--head", "aaa")[1] == "no\n"
    assert cmd(repo, "stale", "--head", "bbb")[1] == "yes\n"


def test_record_without_body_keeps_previous_body(tmp_path):
    repo = make_repo(tmp_path)
    cmd(repo, "record", "--head", "aaa", "--body", body(tmp_path, "a"))
    assert cmd(repo, "record", "--head", "bbb")[0] == 0
    assert cmd(repo, "show")[1] == "a"
    assert cmd(repo, "stale", "--head", "bbb")[1] == "no\n"


def test_record_without_body_and_no_record_is_rejected(tmp_path):
    repo = make_repo(tmp_path)
    assert cmd(repo, "record", "--head", "aaa")[0] == 2


def test_qa_gate_is_sticky_and_drives_stale(tmp_path):
    repo = make_repo(tmp_path)
    cmd(repo, "record", "--head", "aaa", "--body", body(tmp_path, "a"))
    assert cmd(repo, "stale", "--head", "aaa", "--qa-gate")[1] == "yes\n"
    cmd(repo, "record", "--head", "aaa", "--body", body(tmp_path, "a+qa"), "--qa-gate")
    assert cmd(repo, "stale", "--head", "aaa", "--qa-gate")[1] == "no\n"
    cmd(repo, "record", "--head", "bbb")
    assert cmd(repo, "stale", "--head", "bbb", "--qa-gate")[1] == "no\n"


def test_state_lives_in_git_dir_per_branch(tmp_path):
    repo = make_repo(tmp_path)
    cmd(repo, "record", "--head", "aaa", "--body", body(tmp_path, "a"))
    assert json.loads((repo / ".git" / "simo-dev-vnotes.feat-x.json").read_text()) == {MR: {"body": "a", "head": "aaa", "qa_gate": False}}
    subprocess.run(["git", "-C", str(repo), "symbolic-ref", "HEAD", "refs/heads/feat/other"], check=True)
    assert cmd(repo, "has")[1] == "no\n"


def test_trailing_slash_is_the_same_mr(tmp_path):
    repo = make_repo(tmp_path)
    run_lib(["record", "--repo", str(repo), "--mr", MR + "/", "--head", "aaa", "--body", body(tmp_path, "a")])
    assert cmd(repo, "has")[1] == "yes\n"


def test_rejections(tmp_path):
    repo = make_repo(tmp_path)
    assert run_lib(["has", "--repo", "repo", "--mr", MR])[0] == 2
    assert run_lib(["has", "--repo", str(repo), "--mr", ""])[0] == 2
    assert cmd(repo, "stale")[0] == 2
    assert cmd(repo, "record", "--body", body(tmp_path, "a"))[0] == 2
    assert run_lib(["has", "--repo", str(tmp_path / "nogit"), "--mr", MR])[0] == 2


def test_linked_worktree_keeps_its_own_state(tmp_path):
    repo = make_repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    wt = tmp_path / "wt"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "feat/y", str(wt)], check=True)
    cmd(wt, "record", "--head", "aaa", "--body", body(tmp_path, "a"))
    assert (repo / ".git" / "worktrees" / "wt" / "simo-dev-vnotes.feat-y.json").exists()
    assert not (repo / ".git" / "simo-dev-vnotes.feat-y.json").exists()


def test_corrupt_state_file_is_rc2(tmp_path):
    repo = make_repo(tmp_path)
    (repo / ".git" / "simo-dev-vnotes.feat-x.json").write_text("{not json")
    assert cmd(repo, "has")[0] == 2
    (repo / ".git" / "simo-dev-vnotes.feat-x.json").write_text(json.dumps({MR: {"body": "a"}}))
    assert cmd(repo, "stale", "--head", "aaa")[0] == 2


def test_unreadable_state_path_is_rc2(tmp_path):
    repo = make_repo(tmp_path)
    (repo / ".git" / "simo-dev-vnotes.feat-x.json").mkdir()
    rc, out, err = cmd(repo, "has")
    assert rc == 2 and out == "" and "ERROR:" in err
