"""Execute one action from the local invoice-tool allowlist."""

from pathlib import Path
from typing import Any

from tools.browser_tool import create_invoice_via_browser
from tools.file_search import find_pdf_files
from tools.invoice_data import InvoiceData
from tools.invoice_rag import extract_invoice_with_rag


class ExecutorError(ValueError):
    """Raised when an action is not part of the local invoice-tool allowlist."""


def execute_action(action: str, state: dict[str, Any]) -> dict[str, Any]:
    """Dispatch one allowlisted action and return serializable workflow output."""
    if action == "search_invoice_files":
        paths = find_pdf_files(state["invoice_directory"])
        if not paths:
            raise FileNotFoundError("No PDF invoices were found in the selected folder")
        return {"files": [str(path) for path in paths], "count": len(paths)}

    if action == "extract_invoice_data":
        files: list[str] = state["invoice_files"]
        file_index: int = state["file_index"]
        extraction = extract_invoice_with_rag(Path(files[file_index]))
        return {
            "invoice_data": extraction.invoice.model_dump(mode="json"),
            "extraction_evidence": {
                field_name: evidence.model_dump(mode="json")
                for field_name, evidence in extraction.evidence.items()
            },
            "retrieved_pages": extraction.retrieved_pages,
            "source_file": files[file_index],
        }

    if action == "save_invoice":
        invoice = InvoiceData.model_validate(state["invoice_data"])
        evidence = create_invoice_via_browser(
            state["company_app_url"],
            invoice,
        )
        return {"browser_evidence": evidence}

    raise ExecutorError(f"Action is not allowlisted: {action}")