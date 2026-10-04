"""Verify a browser-submitted invoice against the SQLite company record."""

from typing import Any

from company_app.database import get_invoice
from tools.invoice_data import InvoiceData


def verify_invoice_record(
    invoice_data: dict[str, Any],
    browser_evidence: str | None = None,
) -> dict[str, Any]:
    """Confirm the database values match and include the browser row when available."""
    invoice = InvoiceData.model_validate(invoice_data)
    saved = get_invoice(invoice.invoice_number, invoice.company_name)
    if saved is None:
        return {"verified": False, "reason": "No matching invoice record exists"}

    matches = (
        saved.invoice_number == invoice.invoice_number
        and saved.company_name.casefold() == invoice.company_name.casefold()
        and saved.amount == invoice.amount
        and saved.due_date == invoice.due_date
    )
    if not matches:
        return {"verified": False, "reason": "Stored invoice fields do not match the extracted values"}

    expected_values = (
        invoice.invoice_number,
        invoice.company_name,
        f"${invoice.amount:,.2f}",
        invoice.due_date.isoformat(),
    )
    if browser_evidence and not all(
        value.casefold() in browser_evidence.casefold() for value in expected_values
    ):
        return {"verified": False, "reason": "Browser row evidence does not match the invoice"}

    return {
        "verified": True,
        "record_id": saved.id,
        "database_record": saved.model_dump(mode="json"),
        "browser_evidence": browser_evidence,
    }