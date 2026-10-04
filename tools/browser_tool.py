"""Use Playwright to add an invoice to the local demo app and verify the row."""

import os
import re
from urllib.parse import urlparse

from config import PROJECT_ROOT

os.environ.setdefault(
    "PLAYWRIGHT_BROWSERS_PATH", str(PROJECT_ROOT / ".playwright-browsers")
)

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from tools.invoice_data import InvoiceData


class BrowserAutomationError(RuntimeError):
    """Raised when the browser cannot complete or verify a local invoice action."""


def create_invoice_via_browser(
    base_url: str,
    invoice: InvoiceData,
    *,
    username: str | None = None,
    password: str | None = None,
) -> str:
    """Submit an invoice through the local website and return its verified row text."""
    parsed_url = urlparse(base_url)
    if parsed_url.scheme not in {"http", "https"} or parsed_url.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise ValueError("Browser automation is restricted to a localhost company app")

    username = username or os.getenv("COMPANY_APP_USERNAME")
    password = password or os.getenv("COMPANY_APP_PASSWORD")
    if not username or not password:
        raise BrowserAutomationError(
            "Set COMPANY_APP_USERNAME and COMPANY_APP_PASSWORD in the project .env"
        )

    base_url = base_url.rstrip("/")
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(f"{base_url}/login", wait_until="domcontentloaded")
                page.get_by_label("Username").fill(username)
                page.get_by_label("Password").fill(password)
                page.get_by_role("button", name="Sign in").click()
                page.wait_for_url(f"{base_url}/invoices")

                page.goto(f"{base_url}/invoices/new", wait_until="domcontentloaded")
                page.get_by_label("Invoice number").fill(invoice.invoice_number)
                page.get_by_label("Company name").fill(invoice.company_name)
                page.get_by_label("Invoice amount").fill(str(invoice.amount))
                page.get_by_label("Due date").fill(invoice.due_date.isoformat())
                page.get_by_role("button", name="Save invoice").click()

                error_alert = page.locator(".alert.error")
                if error_alert.count():
                    raise BrowserAutomationError(error_alert.inner_text())

                page.wait_for_url(f"{base_url}/invoices")
                row = page.get_by_role(
                    "row", name=re.compile(re.escape(invoice.invoice_number), re.IGNORECASE)
                )
                if row.count() != 1:
                    raise BrowserAutomationError(
                        "Invoice submission returned, but one matching saved row was not found"
                    )

                evidence = row.inner_text()
                expected_values = (
                    invoice.invoice_number,
                    invoice.company_name,
                    f"${invoice.amount:,.2f}",
                    invoice.due_date.isoformat(),
                )
                if not all(value.casefold() in evidence.casefold() for value in expected_values):
                    raise BrowserAutomationError(
                        "Saved row did not match all submitted invoice values"
                    )
                return evidence
            finally:
                browser.close()
    except BrowserAutomationError:
        raise
    except PlaywrightError as error:
        raise BrowserAutomationError(f"Browser action failed: {error}") from error