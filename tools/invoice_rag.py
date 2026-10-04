"""Retrieve relevant PDF excerpts and use Groq structured output to extract invoices."""

import re
from collections.abc import Callable
from pathlib import Path

from langchain_groq import ChatGroq
from pydantic import BaseModel, ConfigDict, Field
from rank_bm25 import BM25Okapi

from config import GROQ_API_KEY, GROQ_MODEL
from tools.invoice_data import InvoiceData
from tools.pdf_reader import extract_pdf_pages


class InvoiceChunk(BaseModel):
    page_number: int = Field(ge=1)
    text: str = Field(min_length=1)


class InvoiceFieldEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    page_number: int = Field(ge=1)


class InvoiceRAGResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice_number: InvoiceFieldEvidence
    company_name: InvoiceFieldEvidence
    amount: InvoiceFieldEvidence
    due_date: InvoiceFieldEvidence


class InvoiceRAGExtraction(BaseModel):
    invoice: InvoiceData
    evidence: dict[str, InvoiceFieldEvidence]
    retrieved_pages: list[int]


class InvoiceRAGError(ValueError):
    """Raised when retrieval or evidence-grounded invoice extraction fails."""


_RAG_QUERY = (
    "invoice number invoice no bill to billed to customer company name vendor supplier "
    "total due amount due invoice amount total amount due date"
)
_TOKEN_PATTERN = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def _tokenize(text: str) -> list[str]:
    return _TOKEN_PATTERN.findall(text.casefold())


def _chunks_from_pages(
    pages: list[str],
    *,
    lines_per_chunk: int = 12,
    overlap_lines: int = 6,
) -> list[InvoiceChunk]:
    chunks: list[InvoiceChunk] = []
    step = lines_per_chunk - overlap_lines
    for page_number, page_text in enumerate(pages, start=1):
        lines = [line.strip() for line in page_text.splitlines() if line.strip()]
        for start in range(0, len(lines), step):
            chunk_lines = lines[start : start + lines_per_chunk]
            if chunk_lines:
                chunks.append(
                    InvoiceChunk(page_number=page_number, text="\n".join(chunk_lines))
                )
            if start + lines_per_chunk >= len(lines):
                break
    return chunks


def retrieve_invoice_chunks(pdf_path: str | Path, *, top_k: int = 8) -> list[InvoiceChunk]:
    """Use BM25 to retrieve text windows likely to contain required invoice fields."""
    if not 1 <= top_k <= 20:
        raise ValueError("top_k must be between 1 and 20")

    chunks = _chunks_from_pages(extract_pdf_pages(pdf_path))
    if not chunks:
        raise InvoiceRAGError("No text chunks could be retrieved from the PDF")

    tokenized_chunks = [_tokenize(chunk.text) for chunk in chunks]
    if not any(tokenized_chunks):
        raise InvoiceRAGError("The PDF did not contain searchable text")

    retriever = BM25Okapi(tokenized_chunks)
    scores = retriever.get_scores(_tokenize(_RAG_QUERY))
    ranked_indexes = sorted(range(len(chunks)), key=lambda index: scores[index], reverse=True)
    selected_indexes = ranked_indexes[: min(top_k, len(chunks))]
    return [chunks[index] for index in selected_indexes]


def _default_model_call(prompt: str) -> InvoiceRAGResponse:
    if not GROQ_API_KEY:
        raise InvoiceRAGError("GROQ_API_KEY is not configured")
    try:
        structured_model = ChatGroq(
            api_key=GROQ_API_KEY,
            model=GROQ_MODEL,
            temperature=0,
        ).with_structured_output(InvoiceRAGResponse)
        response = structured_model.invoke(prompt)
    except Exception as error:
        raise InvoiceRAGError(
            f"Groq invoice extraction failed ({type(error).__name__})"
        ) from error
    return response if isinstance(response, InvoiceRAGResponse) else InvoiceRAGResponse.model_validate(response)


def _normalize_quote(value: str) -> str:
    return " ".join(value.casefold().split())


def extract_invoice_with_rag(
    pdf_path: str | Path,
    *,
    top_k: int = 8,
    model_call: Callable[[str], InvoiceRAGResponse] | None = None,
) -> InvoiceRAGExtraction:
    """Retrieve invoice excerpts, extract cited fields with Groq, then validate all values."""
    chunks = retrieve_invoice_chunks(pdf_path, top_k=top_k)
    context = "\n\n".join(
        f"[Page {chunk.page_number}]\n{chunk.text}" for chunk in chunks
    )
    prompt = f"""Extract the invoice fields from the provided PDF excerpts.
Use only facts visible in the excerpts. Do not guess or calculate missing fields.
company_name means the billed customer / BILL TO name when present, not the invoice issuer.
amount means the final total due, not a line item, subtotal, or tax amount.
For every field return its value, one exact supporting quote, and the 1-based page number.
Keep the value close to how it appears in the quote; the application validates and normalizes it.
If any required field is absent, do not invent it; return an empty value and quote.

Required fields: invoice number, billed company name, final amount due, and due date.

Retrieved excerpts:
{context}"""

    response = (model_call or _default_model_call)(prompt)
    response = (
        response
        if isinstance(response, InvoiceRAGResponse)
        else InvoiceRAGResponse.model_validate(response)
    )
    page_text_by_number: dict[int, str] = {}
    for chunk in chunks:
        page_text_by_number.setdefault(chunk.page_number, "")
        page_text_by_number[chunk.page_number] += f"\n{chunk.text}"

    field_evidence = {
        "invoice_number": response.invoice_number,
        "company_name": response.company_name,
        "amount": response.amount,
        "due_date": response.due_date,
    }
    for field_name, evidence in field_evidence.items():
        page_text = page_text_by_number.get(evidence.page_number)
        if page_text is None or _normalize_quote(evidence.quote) not in _normalize_quote(page_text):
            raise InvoiceRAGError(
                f"The {field_name} evidence quote was not found in the retrieved PDF text"
            )

    try:
        invoice = InvoiceData.model_validate(
            {
                field_name: evidence.value
                for field_name, evidence in field_evidence.items()
            }
        )
    except ValueError as error:
        raise InvoiceRAGError(f"The retrieved invoice fields failed validation: {error}") from error

    return InvoiceRAGExtraction(
        invoice=invoice,
        evidence=field_evidence,
        retrieved_pages=sorted(page_text_by_number),
    )