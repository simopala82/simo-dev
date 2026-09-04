import subprocess

import pytest


@pytest.fixture
def git_repo(tmp_path):
    """A fresh git repo with one empty commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "--quiet"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--quiet", "--allow-empty", "-m", "init"],
        check=True,
    )
    return repo
