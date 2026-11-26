"""Tests for the mr-followup review-comment fetchers (fail-closed contract).

A fetch that could not complete must be impossible to read as "zero review
comments": rc 2, a message on stderr, and the UNAVAILABLE marker on stdout --
never the zero-comments sentinel.
"""
import http.client
import importlib.util
import json
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

FETCHERS = Path(__file__).parent.parent.parent / "skills" / "mr-followup" / "lib"
SENTINEL = "No open review comments found."


def _load(name):
    spec = importlib.util.spec_from_file_location(name, FETCHERS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gl = _load("fetch_discussions")


class _FakeResp:
    """Minimal urlopen context manager: json.load() calls read()."""

    def __init__(self, payload, next_page=""):
        self._payload = payload
        self.headers = {"X-Next-Page": next_page}

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _gl_argv(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["fetch_discussions.py", "grp/proj", "91", "gitlab.example.com"])


def test_gitlab_http_error_is_unverifiable(monkeypatch, capsys):
    monkeypatch.setattr(gl, "get_token", lambda: "tok")

    def boom(req):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(gl.urllib.request, "urlopen", boom)
    _gl_argv(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        gl.main()
    assert exc.value.code == 2
    out, err = capsys.readouterr()
    assert gl.UNAVAILABLE in out
    assert SENTINEL not in out
    assert err.startswith("Error:")


def test_gitlab_connection_error_is_unverifiable(monkeypatch, capsys):
    monkeypatch.setattr(gl, "get_token", lambda: "tok")

    def boom(req):
        raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))

    monkeypatch.setattr(gl.urllib.request, "urlopen", boom)
    _gl_argv(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        gl.main()
    assert exc.value.code == 2
    out, err = capsys.readouterr()
    assert gl.UNAVAILABLE in out
    assert SENTINEL not in out
    assert err.startswith("Error:")


def test_gitlab_non_json_body_is_unverifiable(monkeypatch, capsys):
    monkeypatch.setattr(gl, "get_token", lambda: "tok")

    class _HtmlResp(_FakeResp):
        def read(self):
            return b"<html>502 Bad Gateway</html>"

    monkeypatch.setattr(gl.urllib.request, "urlopen", lambda req: _HtmlResp([]))
    _gl_argv(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        gl.main()
    assert exc.value.code == 2
    out, _ = capsys.readouterr()
    assert gl.UNAVAILABLE in out
    assert SENTINEL not in out


def test_gitlab_body_read_failure_is_unverifiable(monkeypatch, capsys):
    """IncompleteRead is an HTTPException, not an OSError, and urlopen has
    already returned by the time it fires -- so urllib never wraps it."""
    monkeypatch.setattr(gl, "get_token", lambda: "tok")

    class _TruncResp(_FakeResp):
        def read(self):
            raise http.client.IncompleteRead(b"partial", 999)

    monkeypatch.setattr(gl.urllib.request, "urlopen", lambda req: _TruncResp([]))
    _gl_argv(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        gl.main()
    assert exc.value.code == 2
    out, err = capsys.readouterr()
    assert gl.UNAVAILABLE in out
    assert SENTINEL not in out
    assert "IncompleteRead" in err


def test_gitlab_second_page_failure_is_unverifiable(monkeypatch, capsys):
    """A failure after page 1 must not emit the page-1 comments as the answer."""
    monkeypatch.setattr(gl, "get_token", lambda: "tok")
    page1 = [{"resolved": False, "notes": [
        {"system": False, "body": "first", "author": {"name": "bob"}}]}]
    calls = {"n": 0}

    def urlopen(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResp(page1, next_page="2")
        raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))

    monkeypatch.setattr(gl.urllib.request, "urlopen", urlopen)
    _gl_argv(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        gl.main()
    assert exc.value.code == 2
    out, _ = capsys.readouterr()
    assert calls["n"] == 2
    assert gl.UNAVAILABLE in out
    assert SENTINEL not in out
    assert "first" not in out


# The real strings keychain.get_token returns, not a fixture of our own: the
# first is built by calling keychain's own recipe builder (so it cannot drift,
# and it carries non-ASCII bullets, exercising unverifiable() with a message
# stdout could not encode); the second has no `Error:` prefix, which is why the
# contract is "a message", not "an `Error:` line".
_kc = _load("keychain")


@pytest.mark.parametrize("err", [
    pytest.param(_kc._build_acl_recipe("gitlab-api-token", "GITLAB_TOKEN"), id="real-acl-recipe"),
    "'security' command not found (is this macOS?). Set GITLAB_TOKEN instead.",
])
def test_gitlab_missing_token_is_unverifiable(monkeypatch, capsys, err):
    monkeypatch.setattr(gl, "keychain_get_token", lambda **kw: (None, err))
    _gl_argv(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        gl.main()
    assert exc.value.code == 2
    out, captured_err = capsys.readouterr()
    assert gl.UNAVAILABLE in out
    assert SENTINEL not in out
    assert err in captured_err


def test_gitlab_zero_comments_prints_sentinel(monkeypatch, capsys):
    monkeypatch.setattr(gl, "get_token", lambda: "tok")
    monkeypatch.setattr(gl.urllib.request, "urlopen", lambda req: _FakeResp([]))
    _gl_argv(monkeypatch)
    gl.main()
    out, _ = capsys.readouterr()
    assert out.strip() == SENTINEL
    assert gl.UNAVAILABLE not in out


def test_gitlab_system_notes_only_print_sentinel(monkeypatch, capsys):
    """MR !91's real shape: one discussion, one note, system:true."""
    monkeypatch.setattr(gl, "get_token", lambda: "tok")
    payload = [{"resolved": False, "notes": [{"system": True, "body": "changed the description"}]}]
    monkeypatch.setattr(gl.urllib.request, "urlopen", lambda req: _FakeResp(payload))
    _gl_argv(monkeypatch)
    gl.main()
    out, _ = capsys.readouterr()
    assert out.strip() == SENTINEL


@pytest.mark.parametrize("project, mr_iid", [
    # A zsh `set -- $x` loop over "<project> <iid>" pairs does not word-split: the
    # whole pair lands in $1 and $2 is empty (observed 2026-11-23, GitLab answered 400).
    ("grp/proj 91", ""),
    ("grp/proj", ""),
    ("grp/proj", "!91"),
    ("grp/proj", "abc"),
])
def test_gitlab_bad_args_fail_before_any_request(monkeypatch, capsys, project, mr_iid):
    def no_token():
        raise AssertionError("token looked up for bad args")

    def no_request(req):
        raise AssertionError(f"request sent for bad args: {req.full_url}")

    monkeypatch.setattr(gl, "get_token", no_token)
    monkeypatch.setattr(gl.urllib.request, "urlopen", no_request)
    monkeypatch.setattr(sys, "argv", ["fetch_discussions.py", project, mr_iid, "gitlab.example.com"])
    with pytest.raises(SystemExit) as exc:
        gl.main()
    assert exc.value.code not in (0, None)
    out, _ = capsys.readouterr()
    assert SENTINEL not in out
    assert "Error:" in str(exc.value.code)


gh = _load("fetch_pr_threads")

_THREADS_OK = {"data": {"repository": {"pullRequest": {
    "reviewThreads": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []}}}}}
_REVIEWS_OK = {"data": {"repository": {"pullRequest": {"reviews": {"nodes": []}}}}}


def _gh_run(threads=_THREADS_OK, reviews=_REVIEWS_OK, returncode=0, stdout=None, stderr=""):
    """Fake subprocess.run for `gh api graphql`, keyed on which query was sent."""

    def run(cmd, **kwargs):
        if stdout is not None or returncode != 0:
            return subprocess.CompletedProcess(cmd, returncode, stdout=stdout or "", stderr=stderr)
        payload = threads if any("reviewThreads" in a for a in cmd) else reviews
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    return run


def _gh_argv(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["fetch_pr_threads.py", "owner/repo", "7"])
    monkeypatch.setattr(gh.shutil, "which", lambda binary: "/usr/bin/gh")


def test_github_gh_api_failure_is_unverifiable(monkeypatch, capsys):
    _gh_argv(monkeypatch)
    monkeypatch.setattr(gh.subprocess, "run", _gh_run(returncode=1, stderr="gh: HTTP 401\n"))
    with pytest.raises(SystemExit) as exc:
        gh.main()
    assert exc.value.code == 2
    out, err = capsys.readouterr()
    assert gh.UNAVAILABLE in out
    assert SENTINEL not in out
    assert "Error:" in err


def test_github_missing_gh_binary_is_unverifiable(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["fetch_pr_threads.py", "owner/repo", "7"])
    monkeypatch.setattr(gh.shutil, "which", lambda binary: None)
    with pytest.raises(SystemExit) as exc:
        gh.main()
    assert exc.value.code == 2
    out, err = capsys.readouterr()
    assert gh.UNAVAILABLE in out
    assert SENTINEL not in out
    assert "Error:" in err


def test_github_non_json_output_is_unverifiable(monkeypatch, capsys):
    _gh_argv(monkeypatch)
    monkeypatch.setattr(gh.subprocess, "run", _gh_run(stdout="<html>502</html>"))
    with pytest.raises(SystemExit) as exc:
        gh.main()
    assert exc.value.code == 2
    out, _ = capsys.readouterr()
    assert gh.UNAVAILABLE in out
    assert SENTINEL not in out


def test_github_null_pull_request_in_reviews_is_unverifiable(monkeypatch, capsys):
    """Threads query fine, reviews query returns a null PR -> must not read as zero."""
    _gh_argv(monkeypatch)
    monkeypatch.setattr(
        gh.subprocess, "run",
        _gh_run(reviews={"data": {"repository": {"pullRequest": None}}}),
    )
    with pytest.raises(SystemExit) as exc:
        gh.main()
    assert exc.value.code == 2
    out, _ = capsys.readouterr()
    assert gh.UNAVAILABLE in out
    assert SENTINEL not in out


def test_github_zero_comments_prints_sentinel(monkeypatch, capsys):
    _gh_argv(monkeypatch)
    monkeypatch.setattr(gh.subprocess, "run", _gh_run())
    gh.main()
    out, _ = capsys.readouterr()
    assert out.strip() == SENTINEL
    assert gh.UNAVAILABLE not in out


def test_github_partial_output_still_carries_the_marker(monkeypatch, capsys):
    """Threads print before the reviews query runs: the marker is appended,
    not substituted, so a consumer must match it anywhere in the capture."""
    _gh_argv(monkeypatch)
    threads = {"data": {"repository": {"pullRequest": {"reviewThreads": {
        "pageInfo": {"hasNextPage": False, "endCursor": None},
        "nodes": [{"isResolved": False, "isOutdated": False, "path": "a.py", "line": 3,
                   "comments": {"nodes": [{"author": {"login": "bob"}, "body": "fix this"}]}}],
    }}}}}
    monkeypatch.setattr(
        gh.subprocess, "run",
        _gh_run(threads=threads, reviews={"data": {"repository": {"pullRequest": None}}}),
    )
    with pytest.raises(SystemExit) as exc:
        gh.main()
    assert exc.value.code == 2
    out, _ = capsys.readouterr()
    assert "fix this" in out
    assert gh.UNAVAILABLE in out
    assert not out.startswith(gh.UNAVAILABLE)
    assert SENTINEL not in out


def test_marker_wording_matches_the_skill_body():
    """SKILL.md step 5 quotes this prefix so an operator can match stdout."""
    assert gl.UNAVAILABLE == gh.UNAVAILABLE
    assert gl.UNAVAILABLE.startswith("REVIEW COMMENTS UNAVAILABLE")
    assert gl.RC_UNVERIFIABLE == gh.RC_UNVERIFIABLE == 2
    skill = (FETCHERS.parent / "SKILL.md").read_text()
    assert "REVIEW COMMENTS UNAVAILABLE" in skill


def test_marker_survives_an_ascii_stdout():
    """The marker's one job is to survive a failure: a non-ASCII character in
    it raises UnicodeEncodeError while printing and loses it entirely. Only
    stdout needs this -- stderr defaults to errors='backslashreplace', so a
    non-ASCII message there degrades the text but keeps the rc."""
    for marker in (gl.UNAVAILABLE, gh.UNAVAILABLE):
        marker.encode("ascii")


GITLAB_PRELUDE = r'''
import io, json, urllib.request
class _Resp(io.BytesIO):
    headers = {"X-Next-Page": ""}
payload = [{"resolved": False, "notes": [{"system": False, "author": {"name": "Zo\u00eb \u2605"},
            "body": "rename \u2192 fetch_all", "position": {"new_path": "a.py", "new_line": 3}}]}]
urllib.request.urlopen = lambda req, **kw: _Resp(json.dumps(payload).encode())
'''

FAKE_GH = r'''
import json, sys
if any("reviewThreads" in a for a in sys.argv):
    out = {"data": {"repository": {"pullRequest": {"reviewThreads": {
        "pageInfo": {"hasNextPage": False, "endCursor": None},
        "nodes": [{"isResolved": False, "isOutdated": False, "path": "a.py", "line": 3,
                   "comments": {"nodes": [{"author": {"login": "zoe"}, "body": "rename \u2192 fetch_all \u2605"}]}}]}}}}}
else:
    out = {"data": {"repository": {"pullRequest": {"reviews": {"nodes": []}}}}}
sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))
'''


def test_gitlab_fetcher_prints_non_ascii_comment_on_cp1252_stdout():
    from cp1252_run import run_cp1252
    result = run_cp1252(FETCHERS / "fetch_discussions.py", ["group/proj", "7", "gitlab.example.com"],
                        prelude=GITLAB_PRELUDE, extra_env={"GITLAB_TOKEN": "t"})
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    out = result.stdout.decode("utf-8")
    assert "Zoë ★" in out and "rename → fetch_all" in out


def test_github_fetcher_prints_non_ascii_comment_on_cp1252_stdout(tmp_path):
    import os
    from cp1252_run import run_cp1252
    gh = tmp_path / "gh"
    gh.write_text(f"#!{sys.executable}\n{FAKE_GH}", encoding="utf-8")
    gh.chmod(0o755)
    result = run_cp1252(FETCHERS / "fetch_pr_threads.py", ["owner/repo", "7"],
                        extra_env={"PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"})
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert "rename → fetch_all ★" in result.stdout.decode("utf-8")


def test_github_fetcher_fails_closed_on_non_utf8_gh_output(tmp_path):
    import os
    from cp1252_run import run_cp1252
    gh = tmp_path / "gh"
    gh.write_text(f"#!{sys.executable}\nimport sys\nsys.stdout.buffer.write(b'\\xff')\n", encoding="utf-8")
    gh.chmod(0o755)
    result = run_cp1252(FETCHERS / "fetch_pr_threads.py", ["owner/repo", "7"],
                        extra_env={"PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"})
    assert result.returncode == 2, result.stderr.decode("utf-8", "replace")
    assert b"REVIEW COMMENTS UNAVAILABLE" in result.stdout
