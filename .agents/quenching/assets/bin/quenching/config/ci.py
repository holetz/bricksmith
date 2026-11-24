"""The `shared.ci` block: whether the quenching demands green CI before it merges.

`enforce` is `required` (the default: the merge needs every check green), `when-present` (a PR
with no check at all may merge, one with checks still needs them green) or `off` (a PR with no
check, or with checks still pending, may merge). A **red**, skipped-required or unreadable check
is refused under every value: the key only decides what to do when there is no check, or when a
check is still pending. Whatever relaxes the exigence puts the local gate in its place: the merge
then needs `--gate-exit 0`, the exit code of the repository gate the caller just ran.

The key lives in the project layer only. It is read raw from the repository document, never from
the merged envelope, so a user or a local layer cannot widen what merges without proof. A value
the registry does not know falls back to `required` and is named in the warnings.
"""
from __future__ import annotations

ENFORCE = ("required", "when-present", "off")
DEFAULTS = {"enforce": "required"}


def read(root: str) -> tuple[str, list[str]]:
    """`(enforce, warnings)` for the repository at `root`, from the project layer alone."""
    from quenching.common.config import find_repo_root, repository_document

    try:
        document = repository_document(find_repo_root(root))[0]
    except Exception as exc:  # an unreadable file fails closed: the default demands CI
        return DEFAULTS["enforce"], [f"shared.ci unreadable ({exc}); {DEFAULTS['enforce']} applies"]
    shared = document.get("shared") if isinstance(document, dict) else None
    block = shared.get("ci") if isinstance(shared, dict) else None
    if block is None:
        return DEFAULTS["enforce"], []
    if not isinstance(block, dict):
        return DEFAULTS["enforce"], ["shared.ci is not an object; required applies"]
    warnings = [f"shared.ci.{key} is not a known key; ignored" for key in block if key not in DEFAULTS]
    value = block.get("enforce", DEFAULTS["enforce"])
    if value not in ENFORCE:
        warnings.append(f"shared.ci.enforce must be one of {', '.join(ENFORCE)}; required applies")
        value = DEFAULTS["enforce"]
    return value, warnings


def judge(enforce: str, state: str, gate_exit: int | None) -> tuple[bool, str | None]:
    """`(accepted, reason)` for a PR whose checks read as `state`.

    `state` is `green`, `none` or `pending`; any other reading (red, skipped, unreadable) is the
    caller's own refusal and is never accepted here. `reason` is set only for the refusals this
    key owns: `no-checks`, `pending` and `gate-proof-required`."""
    if state == "green":
        return True, None
    if state not in ("none", "pending"):
        return False, None
    relaxed = enforce == "off" or (enforce == "when-present" and state == "none")
    if not relaxed:
        return False, "no-checks" if state == "none" else "pending"
    if gate_exit != 0:
        return False, "gate-proof-required"
    return True, None


def proof_message(enforce: str, state: str) -> str:
    return (f"CI is not required for this PR (shared.ci.enforce={enforce}, checks {state}); "
            "the local gate stands in for it, so pass --gate-exit 0 after a green repository gate")
