"""Read text from local PDFs and extract validated invoice details."""

from pathlib import Path

import pymupdf

from tools.invoice_data import InvoiceData, parse_invoice_text


class PDFReadError(ValueError):
    """Raised when a PDF cannot be read or contains no extractable text."""


def extract_pdf_text(pdf_path: str | Path) -> str:
    """Return text from a text-based PDF; OCR for scanned pages is not performed."""
    return "\n".join(extract_pdf_pages(pdf_path))


def extract_pdf_pages(pdf_path: str | Path) -> list[str]:
    """Return extracted text by page so later stages can cite source locations."""
    path = Path(pdf_path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"PDF file does not exist: {path}")
    if path.suffix.casefold() != ".pdf":
        raise ValueError(f"Expected a .pdf file: {path}")

    try:
        with pymupdf.open(path) as document:
            if document.is_encrypted and not document.authenticate(""):
                raise PDFReadError("PDF is password-protected")
            pages = [page.get_text("text") for page in document]
    except PDFReadError:
        raise
    except (pymupdf.FileDataError, pymupdf.EmptyFileError, OSError, RuntimeError) as error:
        raise PDFReadError(f"Could not read PDF: {path.name}") from error

    if not any(page.strip() for page in pages):
        raise PDFReadError("PDF contains no extractable text; scanned PDFs need OCR")
    return pages


def extract_invoice_data(pdf_path: str | Path) -> InvoiceData:
    """Extract text from a PDF, then parse and validate its invoice fields."""
    return parse_invoice_text(extract_pdf_text(pdf_path))