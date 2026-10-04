"""Build final reports from observed state and verification evidence."""

from typing import Any


def build_report(state: dict[str, Any]) -> str:
    """Never report completion unless the verifier explicitly confirmed the record."""
    status = state.get("status", "blocked")
    if status == "waiting_approval":
        reason = state.get("approval_reason") or "sensitive action"
        return f"Paused for human approval: {reason}. No actions were executed."

    if status == "completed" and state.get("verification", {}).get("verified") is True:
        invoice = state.get("invoice_data", {})
        return (
            "Invoice saved and verified. "
            f"Invoice {invoice.get('invoice_number')} for {invoice.get('company_name')}; "
            f"amount {invoice.get('amount')}; due {invoice.get('due_date')}. "
            f"Evidence: {state['verification'].get('database_record')}"
        )

    reason = state.get("error") or "The requested result was not verified"
    return f"Task not completed: {reason}. No completion is claimed."