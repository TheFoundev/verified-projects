"""Decrypt policy: CI detection, marker check and the local escape hatch."""

from __future__ import annotations

from dataclasses import dataclass

from .config import DecryptPolicy

__all__ = ["Decision", "authorize_decrypt", "detect_ci", "check_marker"]


@dataclass
class Decision:
    allowed: bool
    mode: str  # "ci" | "local" | "denied"
    reason: str

    def __bool__(self) -> bool:  # pragma: no cover - convenience
        return self.allowed


def detect_ci(policy: DecryptPolicy, env_map) -> tuple[bool, str]:
    """CI is true when ``policy.ci_var`` equals ``policy.ci_truthy``."""
    value = str(env_map.get(policy.ci_var, "")).strip()
    if value.lower() == policy.ci_truthy.lower():
        return True, f"{policy.ci_var}={value}"
    return False, f"{policy.ci_var} is not set to {policy.ci_truthy!r} (found {value!r})"


def check_marker(policy: DecryptPolicy, env_map) -> tuple[bool, str]:
    """The expected marker (per ``decrypt_policy``) must be present in CI."""
    value = env_map.get(policy.marker_var)
    if value is None or str(value) == "":
        return False, f"marker env var {policy.marker_var} is missing or empty"
    if policy.marker is not None and str(value) != policy.marker:
        return False, f"{policy.marker_var} does not match the configured marker"
    return True, f"{policy.marker_var} present"


def authorize_decrypt(
    policy: DecryptPolicy, env_map, allow_local: bool = False
) -> Decision:
    """Decryption is CI-gated: ``CI=true`` AND the configured marker.

    ``--allow-local`` is the escape hatch for developers, but it can be
    switched off entirely with ``decrypt_policy.enforce_local_deny``.
    """
    if not policy.require_ci:
        if allow_local:
            return Decision(True, "local", "decrypt_policy.require_ci is false; --allow-local honoured")
        ci_ok, ci_reason = detect_ci(policy, env_map)
        if ci_ok:
            marker_ok, marker_reason = check_marker(policy, env_map)
            if marker_ok:
                return Decision(True, "ci", f"{ci_reason}; {marker_reason}")
            return Decision(False, "denied", f"CI detected ({ci_reason}) but {marker_reason}")
        return Decision(
            False,
            "denied",
            f"{ci_reason}; pass --allow-local to decrypt interactively",
        )

    ci_ok, ci_reason = detect_ci(policy, env_map)
    if ci_ok:
        marker_ok, marker_reason = check_marker(policy, env_map)
        if marker_ok:
            return Decision(True, "ci", f"{ci_reason}; {marker_reason}")
        return Decision(False, "denied", f"CI detected ({ci_reason}) but {marker_reason}")

    # Not CI: only the escape hatch can authorise, and policy can deny it.
    if allow_local:
        if policy.enforce_local_deny:
            return Decision(
                False,
                "denied",
                "--allow-local refused: decrypt_policy.enforce_local_deny is true "
                "(decryption is restricted to CI)",
            )
        return Decision(
            True,
            "local",
            f"--allow-local granted outside CI ({ci_reason}); audit event will record the escape hatch",
        )
    return Decision(
        False,
        "denied",
        f"decrypt refused: not running in CI ({ci_reason}) and --allow-local was not given",
    )
