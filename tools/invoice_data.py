"""Structured invoice fields and deterministic parsing for labeled PDF text."""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field, field_validator


class InvoiceExtractionError(ValueError):
    """Raised when required invoice fields cannot be read from the document text."""


class InvoiceData(BaseModel):
    """Validated invoice fields needed by the simulated company application."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    invoice_number: str = Field(min_length=1)
    company_name: str = Field(min_length=1)
    amount: Decimal = Field(gt=0)
    due_date: date

    @field_validator("invoice_number", "company_name")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()

    @field_validator("amount", mode="before")
    @classmethod
    def parse_amount(cls, value: object) -> Decimal:
        if isinstance(value, str):
            normalized = value.strip()
            normalized = re.sub(r"(?i)^(?:INR|RS\.?|I)(?=\s*[\d,])\s*", "", normalized)
            normalized = re.sub(r"(?i)\s*(?:INR|RS\.?|USD)$", "", normalized)
            normalized = re.sub(r"[$,\s]", "", normalized)
            try:
                value = Decimal(normalized)
            except InvalidOperation as error:
                raise ValueError("amount must be a valid decimal number") from error
        return Decimal(value)

    @field_validator("due_date", mode="before")
    @classmethod
    def parse_date(cls, value: object) -> date:
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            for date_format in (
                "%Y-%m-%d",
                "%m/%d/%Y",
                "%d/%m/%Y",
                "%d-%m-%Y",
                "%B %d, %Y",
                "%b %d, %Y",
                "%d %B %Y",
                "%d %b %Y",
            ):
                try:
                    return datetime.strptime(value.strip(), date_format).date()
                except ValueError:
                    continue
        raise ValueError("due date must use YYYY-MM-DD, MM/DD/YYYY, or a month name")


_FIELD_LABELS = {
    "invoice_number": (r"Invoice\s*(?:Number|No\.?|#)",),
    "company_name": (
        r"(?:Company\s*Name|Bill\s*To|Billed\s*To|Customer(?:\s*Name)?)",
        r"(?:Vendor|Supplier)",
    ),
    "amount": (
        r"(?:Amount\s*Due|Total\s*Due|Invoice\s*Amount|Total\s*Amount|Balance\s*Due)",
    ),
    "due_date": (r"Due\s*Date",),
}


def _extract_labeled_value(text: str, labels: tuple[str, ...]) -> str | None:
    for label in labels:
        pattern = re.compile(
            rf"^[ \t]*(?:{label})[ \t]*:?[ \t]*(?:([^\r\n]+)|\r?\n[ \t]*([^\r\n]+))",
            re.IGNORECASE | re.MULTILINE,
        )
        match = pattern.search(text)
        if match is not None:
            value = (match.group(1) or match.group(2) or "").strip()
            if value:
                return value
    return None


def parse_invoice_text(text: str) -> InvoiceData:
    """Parse labeled invoice text and validate every required value."""
    raw_values: dict[str, str] = {}
    missing_fields: list[str] = []

    for field_name, labels in _FIELD_LABELS.items():
        value = _extract_labeled_value(text, labels)
        if value is None:
            missing_fields.append(field_name)
        else:
            raw_values[field_name] = value

    if missing_fields:
        missing = ", ".join(missing_fields)
        raise InvoiceExtractionError(f"Missing required invoice field(s): {missing}")

    return InvoiceData.model_validate(raw_values)