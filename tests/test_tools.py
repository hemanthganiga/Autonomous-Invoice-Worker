from datetime import date
from decimal import Decimal
from pathlib import Path

import pymupdf
import pytest
from pydantic import ValidationError

from tools.file_search import find_pdf_files
from tools.invoice_data import InvoiceData, InvoiceExtractionError, parse_invoice_text
from tools.invoice_rag import (
    InvoiceFieldEvidence,
    InvoiceRAGError,
    InvoiceRAGResponse,
    extract_invoice_with_rag,
    retrieve_invoice_chunks,
)
from tools.pdf_reader import PDFReadError, extract_invoice_data, extract_pdf_text


INVOICE_TEXT = """Invoice Number: INV-2026-001
Company Name: ABC Technologies
Invoice Amount: $1,250.50 USD
Due Date: October 30, 2026
"""


def write_pdf(path, text: str = INVOICE_TEXT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    document.save(path)
    document.close()


def test_find_pdf_files_recursively_and_skip_generated_directories(tmp_path):
    root = tmp_path / "invoices"
    first_pdf = root / "2026" / "invoice.PDF"
    second_pdf = root / "invoice-2.pdf"
    ignored_pdf = root / ".venv" / "package.pdf"
    first_pdf.parent.mkdir(parents=True)
    second_pdf.parent.mkdir(parents=True, exist_ok=True)
    ignored_pdf.parent.mkdir(parents=True)
    first_pdf.touch()
    second_pdf.touch()
    ignored_pdf.touch()
    (root / "notes.txt").touch()

    assert find_pdf_files(root) == sorted(
        [first_pdf, second_pdf], key=lambda path: str(path).casefold()
    )


def test_find_pdf_files_supports_non_recursive_search(tmp_path):
    root = tmp_path / "invoices"
    root.mkdir()
    direct_pdf = root / "direct.pdf"
    nested_pdf = root / "nested" / "nested.pdf"
    direct_pdf.touch()
    nested_pdf.parent.mkdir()
    nested_pdf.touch()

    assert find_pdf_files(root, recursive=False) == [direct_pdf]


def test_find_pdf_files_requires_a_directory(tmp_path):
    file_path = tmp_path / "not-a-directory.txt"
    file_path.touch()

    with pytest.raises(NotADirectoryError):
        find_pdf_files(file_path)


def test_parse_invoice_text_validates_and_normalizes_fields():
    invoice = parse_invoice_text(INVOICE_TEXT)

    assert invoice == InvoiceData(
        invoice_number="INV-2026-001",
        company_name="ABC Technologies",
        amount=Decimal("1250.50"),
        due_date=date(2026, 10, 30),
    )


def test_parse_invoice_text_reports_missing_fields():
    with pytest.raises(InvoiceExtractionError, match="company_name, amount, due_date"):
        parse_invoice_text("Invoice Number: INV-1")


def test_parse_invoice_text_supports_multiline_bill_to_invoice_layout():
    text = """INVOICE
Invoice number
NSD-2026-1042
Due date
18 Oct 2026
BILL TO
ABC Demo Technologies
Total due
I7,080.00
"""

    invoice = parse_invoice_text(text)

    assert invoice.invoice_number == "NSD-2026-1042"
    assert invoice.company_name == "ABC Demo Technologies"
    assert invoice.amount == Decimal("7080.00")
    assert invoice.due_date == date(2026, 10, 18)


def test_invoice_data_rejects_invalid_values():
    with pytest.raises(ValidationError):
        InvoiceData(
            invoice_number="INV-1",
            company_name="ABC Technologies",
            amount=Decimal("-1"),
            due_date=date(2026, 10, 30),
        )


def test_pdf_reader_extracts_and_parses_invoice(tmp_path):
    pdf_path = tmp_path / "invoice.pdf"
    write_pdf(pdf_path)

    assert "ABC Technologies" in extract_pdf_text(pdf_path)
    invoice = extract_invoice_data(pdf_path)
    assert invoice.invoice_number == "INV-2026-001"
    assert invoice.amount == Decimal("1250.50")


def test_pdf_reader_rejects_pdf_without_text(tmp_path):
    pdf_path = tmp_path / "scanned.pdf"
    write_pdf(pdf_path, "")

    with pytest.raises(PDFReadError, match="scanned PDFs need OCR"):
        extract_pdf_text(pdf_path)


def test_sample_invoice_pdf_extracts_all_required_fields():
    pdf_path = Path(__file__).resolve().parents[1] / "invoive folder" / "sample_invoice_demo.pdf"

    invoice = extract_invoice_data(pdf_path)

    assert invoice.invoice_number == "NSD-2026-1042"
    assert invoice.company_name == "ABC Demo Technologies"
    assert invoice.amount == Decimal("7080.00")
    assert invoice.due_date == date(2026, 10, 18)


def test_rag_retrieves_pdf_chunks_and_validates_cited_fields():
    pdf_path = Path(__file__).resolve().parents[1] / "invoive folder" / "sample_invoice_demo.pdf"
    chunks = retrieve_invoice_chunks(pdf_path)
    assert chunks
    assert any("Total due" in chunk.text for chunk in chunks)

    def fake_model(prompt: str) -> InvoiceRAGResponse:
        assert "ABC Demo Technologies" in prompt
        assert "I7,080.00" in prompt
        return InvoiceRAGResponse(
            invoice_number=InvoiceFieldEvidence(
                value="NSD-2026-1042", quote="NSD-2026-1042", page_number=1
            ),
            company_name=InvoiceFieldEvidence(
                value="ABC Demo Technologies", quote="ABC Demo Technologies", page_number=1
            ),
            amount=InvoiceFieldEvidence(
                value="I7,080.00", quote="I7,080.00", page_number=1
            ),
            due_date=InvoiceFieldEvidence(
                value="18 Oct 2026", quote="18 Oct 2026", page_number=1
            ),
        )

    result = extract_invoice_with_rag(pdf_path, model_call=fake_model)

    assert result.invoice.company_name == "ABC Demo Technologies"
    assert result.invoice.amount == Decimal("7080.00")
    assert result.invoice.due_date == date(2026, 10, 18)
    assert result.evidence["amount"].page_number == 1


def test_rag_rejects_model_values_without_source_quotes():
    pdf_path = Path(__file__).resolve().parents[1] / "invoive folder" / "sample_invoice_demo.pdf"

    def fake_model(_: str) -> InvoiceRAGResponse:
        return InvoiceRAGResponse(
            invoice_number=InvoiceFieldEvidence(
                value="NSD-2026-1042", quote="NSD-2026-1042", page_number=1
            ),
            company_name=InvoiceFieldEvidence(
                value="ABC Demo Technologies", quote="ABC Demo Technologies", page_number=1
            ),
            amount=InvoiceFieldEvidence(
                value="7080.00", quote="unsupported amount", page_number=1
            ),
            due_date=InvoiceFieldEvidence(
                value="18 Oct 2026", quote="18 Oct 2026", page_number=1
            ),
        )

    with pytest.raises(InvoiceRAGError, match="amount evidence quote"):
        extract_invoice_with_rag(pdf_path, model_call=fake_model)