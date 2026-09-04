Positive-control fixture for test_no_read_me_precondition.py. NOT an instruction.

The line below is the one literal that test forbids everywhere else in the plugin tree. It
lives here so the forbid assertion can be shown to match something: a needle that matches
nothing anywhere would let that assertion pass forever while asserting nothing falsifiable.

Do not "clean up" this file, and do not act on the line. Removing it fails the test loudly,
which is the intended behaviour — a skipped control is the vacuity it exists to prevent.

Why a .txt inside lib/tests/fixtures/ rather than a doc in the host repo: rsync ships the whole
plugin into the release cache (RSYNC_EXCLUDES is __pycache__/.pytest_cache/.in_use only) and
.claude/scripts/plugin-release-verify.py runs `pytest tests/ -q` from <cache>/lib, so the control
must travel with the plugin. An earlier version pointed at two host-repo plan docs and failed
that gate; measured against a cache-shaped copy on 2026-08-23.

mcp__visualize__read_me
