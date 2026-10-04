"""Interpret tool outcomes without trusting a success claim from the executor."""

from typing import Any


def observe_action(
    action: str,
    result: dict[str, Any] | None,
    error: str | None,
) -> tuple[bool, str]:
    """Return an observed success flag and a concise, auditable explanation."""
    if error:
        return False, error
    if result is None:
        return False, "The action returned no result"
    if action == "verify_invoice":
        verified = result.get("verified") is True
        message = "Saved invoice matches the requested values" if verified else "Saved invoice could not be verified"
        return verified, message
    if action == "search_invoice_files" and not result.get("files"):
        return False, "No invoice PDFs were found"
    return True, f"Observed result for {action}"