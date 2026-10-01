"""Run a plugin script as __main__ in a child whose stdio codec is cp1252.

Windows pipes and redirects default to the ANSI code page; PYTHONIOENCODING=cp1252
reproduces that for stdin/stdout/stderr on any OS. It does not touch the locale
encoding that open()/read_text()/subprocess(text=True) fall back to, so on a UTF-8
dev machine a locale-decoded input would still look fine. strict_encoding turns
every such implicit-encoding call into an error instead (EncodingWarning), which is
what keeps the input half of the fix honest.

`prelude` runs first in the child to stub the script's network/CLI boundary; runpy
then executes the real `if __name__ == "__main__":` block.
"""
import os
import subprocess
import sys
from pathlib import Path

_DRIVER = (
    "import runpy, sys\n"
    "exec(sys.argv[1])\n"
    "sys.argv = sys.argv[2:]\n"
    "runpy.run_path(sys.argv[0], run_name='__main__')\n"
)


def run_cp1252(script, args, prelude="", extra_env=None, stdin=None, strict_encoding=True):
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", **(extra_env or {})}
    env.pop("PYTHONUTF8", None)
    flags = ["-X", "warn_default_encoding", "-W", "error::EncodingWarning"] if strict_encoding else []
    return subprocess.run(
        [sys.executable, *flags, "-c", _DRIVER, prelude, str(Path(script)), *args],
        input=stdin,
        capture_output=True,
        env=env,
    )
