from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from company_app import database
from company_app import main as company_main
from company_app.main import app
from memory.memory_manager import MemoryManager
from tools.browser_tool import create_invoice_via_browser
from tools.invoice_data import InvoiceData


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "company-test.sqlite3")
    monkeypatch.setenv("COMPANY_APP_USERNAME", "test-user")
    monkeypatch.setenv("COMPANY_APP_PASSWORD", "test-password")
    with TestClient(app) as test_client:
        yield test_client


def log_in(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"username": "test-user", "password": "test-password"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/invoices"


def make_invoice(
    invoice_number: str = "INV-TEST-001",
    company_name: str = "Example Supplier",
) -> InvoiceData:
    return InvoiceData(
        invoice_number=invoice_number,
        company_name=company_name,
        amount=Decimal("225.75"),
        due_date=date(2026, 11, 15),
    )


def test_login_is_required_for_invoice_pages(client: TestClient):
    response = client.get("/invoices", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_demo_login_and_logout(client: TestClient):
    log_in(client)
    assert "Invoices" in client.get("/invoices").text

    response = client.post("/logout", follow_redirects=False)
    assert response.status_code == 303
    assert client.get("/invoices", follow_redirects=False).headers["location"] == "/login"


def test_invoice_saves_and_duplicate_is_rejected(client: TestClient):
    log_in(client)
    form_data = {
        "invoice_number": "INV-TEST-001",
        "company_name": "Example Supplier",
        "amount": "225.75",
        "due_date": "2026-11-15",
    }

    response = client.post("/invoices/new", data=form_data, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/invoices"
    assert database.get_invoice("INV-TEST-001", "Example Supplier") is not None

    duplicate_data = {
        **form_data,
        "invoice_number": "inv-test-001",
        "company_name": "example supplier",
    }
    duplicate_response = client.post("/invoices/new", data=duplicate_data)
    assert duplicate_response.status_code == 409
    assert "already saved" in duplicate_response.text
    assert len(database.list_invoices()) == 1


def test_invalid_invoice_is_not_saved(client: TestClient):
    log_in(client)
    response = client.post(
        "/invoices/new",
        data={
            "invoice_number": "INV-BAD",
            "company_name": "Example Supplier",
            "amount": "-1",
            "due_date": "not-a-date",
        },
    )

    assert response.status_code == 422
    assert "Check the invoice fields" in response.text
    assert database.list_invoices() == []


def test_invalid_credentials_do_not_create_a_session(client: TestClient):
    response = client.post(
        "/login",
        data={"username": "wrong", "password": "wrong"},
    )

    assert response.status_code == 401
    assert client.get("/invoices", follow_redirects=False).headers["location"] == "/login"


def test_login_does_not_expose_credentials_when_not_configured(
    client: TestClient,
    monkeypatch,
):
    monkeypatch.delenv("COMPANY_APP_USERNAME")
    monkeypatch.delenv("COMPANY_APP_PASSWORD")

    response = client.get("/login")

    assert response.status_code == 200
    assert "Configure app login" in response.text
    assert "Demo credentials:" not in response.text


def test_task_api_starts_workflow_and_returns_report(client: TestClient, monkeypatch):
    def fake_run_invoice_task(**kwargs):
        return {
            "task_id": "api-task-1",
            "status": "completed",
            "needs_approval": False,
            "retry_count": 0,
            "plan": {"summary": "Invoice flow", "steps": []},
            "invoice_data": {"invoice_number": "INV-API-1"},
            "verification": {"verified": True},
            "events": [],
            "report": "Invoice saved and verified.",
        }

    monkeypatch.setattr(company_main, "run_invoice_task", fake_run_invoice_task)
    response = client.post(
        "/api/tasks",
        json={
            "user_task": "Enter and verify the invoice",
            "invoice_directory": "C:/dummy-invoices",
        },
    )

    assert response.status_code == 200
    assert response.json()["task_id"] == "api-task-1"
    assert response.json()["verification"]["verified"] is True


def test_task_history_api_returns_persisted_events(client: TestClient, tmp_path, monkeypatch):
    memory = MemoryManager(tmp_path / "api-history.sqlite3")
    memory.create_task("api-history-1", "Find invoice")
    memory.log_event("api-history-1", "planner", "success", "Plan created")
    monkeypatch.setattr(company_main, "MemoryManager", lambda: memory)

    list_response = client.get("/api/tasks")
    detail_response = client.get("/api/tasks/api-history-1")

    assert list_response.status_code == 200
    assert list_response.json()[0]["task_id"] == "api-history-1"
    assert detail_response.status_code == 200
    assert detail_response.json()["events"][0]["message"] == "Plan created"


def test_task_history_api_returns_404_for_unknown_task(client: TestClient, tmp_path, monkeypatch):
    memory = MemoryManager(tmp_path / "api-history.sqlite3")
    monkeypatch.setattr(company_main, "MemoryManager", lambda: memory)

    assert client.get("/api/tasks/missing-task").status_code == 404


def test_browser_tool_refuses_non_localhost_urls():
    with pytest.raises(ValueError, match="localhost"):
        create_invoice_via_browser("https://example.com", make_invoice())