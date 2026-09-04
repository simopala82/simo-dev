"""Shared macOS keychain lookup with subshell-safe fallbacks.

The lookup tries, in order:
    1. <env_var> environment variable
    2. security find-generic-password -s <service> -a $USER -w
    3. security find-generic-password -s <service> -w

Both keychain attempts can fail with exit 44 and the misleading message
"SecKeychainSearchCopyNext: The specified item could not be found in the
keychain." even when the entry exists — macOS reports ACL denial as
not-found to avoid leaking item existence to untrusted callers.

When all strategies fail, callers get an error string that points at the
ACL-repair recipe documented in SKILL.md.
"""
import os
import shutil
import subprocess


def _build_acl_recipe(service: str, env_var: str) -> str:
    """Render a ready-to-run keychain-repair recipe with resolved binary paths."""
    flags = ["        -T /usr/bin/security"]
    for binary in ("python3", "glab"):
        resolved = shutil.which(binary)
        if resolved:
            flags.append(f"        -T {resolved}")
    flags_block = " \\\n".join(flags)
    return (
        f"Error: no token found for keychain service '{service}'.\n"
        f"  • Set {env_var} env var, OR\n"
        f"  • Add the entry with ACL pre-seeded for the active python3/glab binaries:\n"
        f"      read -rs -p 'token: ' TOKEN; echo\n"
        f"      security add-generic-password \\\n"
        f"        -s \"{service}\" -a \"$USER\" -w \"$TOKEN\" \\\n"
        f"{flags_block} \\\n"
        f"        -U\n"
        f"      unset TOKEN\n"
        f"  • If the entry exists but lookup still fails, open Keychain Access.app,\n"
        f"    find '{service}', Get Info → Access Control → grant access to your\n"
        f"    active python3 binary or check 'Allow all applications to access this item'."
    )


def get_token(service: str, env_var: str) -> tuple[str, str]:
    """Return (token, error). On success error is empty; on failure token is empty."""
    token = os.environ.get(env_var, "").strip()
    if token:
        return token, ""

    attempts: list[list[str]] = [
        ["security", "find-generic-password", "-s", service, "-a", os.environ.get("USER", ""), "-w"],
        ["security", "find-generic-password", "-s", service, "-w"],
    ]
    last_stderr = ""
    for cmd in attempts:
        if not cmd[-2]:
            continue
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
        except FileNotFoundError:
            return "", f"'security' command not found (is this macOS?). Set {env_var} instead."
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip(), ""
        last_stderr = (result.stderr or "").strip()

    hint = _build_acl_recipe(service, env_var)
    if last_stderr:
        hint += f"\n  (security stderr: {last_stderr})"
    return "", hint
