#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Direct unit tests for git_match.py — previously exercised only indirectly through
mr-followup-remind.sh's and review-loop-warn.sh's own bash test suites."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import git_match  # noqa: E402


def test_plain_commit_matches():
    assert git_match.git_subcommand_re("commit").search("git commit -m x")


def test_worktree_dash_c_form_matches():
    assert git_match.git_subcommand_re("commit").search("git -C /tmp/wt commit -m x")


def test_rtk_wrapped_matches():
    assert git_match.git_subcommand_re("push").search("rtk git push")


def test_prefix_subcommand_does_not_match():
    """The trailing boundary class stops a prefix match on another subcommand."""
    assert not git_match.git_subcommand_re("push").search("git pushed-nothing")


def test_prose_mention_does_not_match():
    """SEP requires a real separator; prose merely mentioning the command must not match."""
    assert not git_match.git_subcommand_re("push").search("echo run git push later >> notes.txt")


def test_plain_form_is_newline_blind():
    """The plain form must NOT match a subcommand that only appears after an embedded
    newline with no other real separator before it — that blindness is what protects a
    caller that parses the match from a decoy line inside a heredoc."""
    assert not git_match.git_subcommand_re("commit").search("echo hello\ngit commit -m x")


def test_multiline_form_matches_across_newlines():
    assert git_match.git_subcommand_re_multiline("push").search("git add a.txt\ngit push -u origin b")


def test_leftmost_match_on_multiline_form_can_hit_a_decoy():
    """Documents the multiline form's own known hazard (why callers that PARSE the match
    must never use it): the leftmost match wins, even if it is a decoy line inside a
    heredoc body -- the boundary class needs a real separator (here, the newline that
    ends the decoy line) right after the subcommand, which a same-line quoted decoy
    does not provide."""
    m = git_match.git_subcommand_re_multiline("commit").search(
        "cat <<EOF\ngit -C /decoy commit\nEOF\ntrue && git -C /real commit"
    )
    assert m and "/decoy" in m.group(0)


def test_unquote_strips_independently_per_side():
    assert git_match.unquote('"foo\'') == "foo"
    assert git_match.unquote('"foo"') == "foo"
    assert git_match.unquote("bare") == "bare"
