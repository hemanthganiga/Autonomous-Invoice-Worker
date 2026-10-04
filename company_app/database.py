"""SQLite persistence for the simulated company's invoice records."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from config import DATABASE_PATH
from pydantic import BaseModel

from tools.invoice_data import InvoiceData


class DuplicateInvoiceError(ValueError):
    """Raised when a company invoice number has already been saved."""


class SavedInvoice(InvoiceData):
    id: int
    created_at: str


@contextmanager
def _connection(database_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    path = database_path or DATABASE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db(database_path: Path | None = None) -> None:
    """Create the invoice table if it has not been created yet."""
    with _connection(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS invoices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_number TEXT NOT NULL COLLATE NOCASE,
                company_name TEXT NOT NULL COLLATE NOCASE,
                amount TEXT NOT NULL,
                due_date TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (invoice_number, company_name)
            )
            """
        )


def add_invoice(invoice: InvoiceData, database_path: Path | None = None) -> int:
    """Save a validated invoice and return its new database ID."""
    try:
        with _connection(database_path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO invoices (invoice_number, company_name, amount, due_date)
                VALUES (?, ?, ?, ?)
                """,
                (
                    invoice.invoice_number,
                    invoice.company_name,
                    str(invoice.amount),
                    invoice.due_date.isoformat(),
                ),
            )
            invoice_id = cursor.lastrowid
    except sqlite3.IntegrityError as error:
        raise DuplicateInvoiceError(
            "That invoice number is already saved for this company."
        ) from error

    if invoice_id is None:
        raise RuntimeError("SQLite did not return an invoice ID")
    return invoice_id


def list_invoices(database_path: Path | None = None) -> list[SavedInvoice]:
    """Return saved invoices newest first."""
    with _connection(database_path) as connection:
        rows = connection.execute(
            """
            SELECT id, invoice_number, company_name, amount, due_date, created_at
            FROM invoices
            ORDER BY id DESC
            """
        ).fetchall()
    return [SavedInvoice.model_validate(dict(row)) for row in rows]


def get_invoice(
    invoice_number: str,
    company_name: str,
    database_path: Path | None = None,
) -> SavedInvoice | None:
    """Look up one invoice by its case-insensitive vendor and invoice number."""
    with _connection(database_path) as connection:
        row = connection.execute(
            """
            SELECT id, invoice_number, company_name, amount, due_date, created_at
            FROM invoices
            WHERE invoice_number = ? COLLATE NOCASE
              AND company_name = ? COLLATE NOCASE
            """,
            (invoice_number, company_name),
        ).fetchone()
    return SavedInvoice.model_validate(dict(row)) if row is not None else None