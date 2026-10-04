"""FastAPI web application for the local demo invoice portal."""

import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, AsyncIterator

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, ValidationError
from starlette.middleware.sessions import SessionMiddleware

from company_app import database
from memory.memory_manager import MemoryManager
from tools.invoice_data import InvoiceData
from workflow.graph import run_invoice_task


TEMPLATE_DIRECTORY = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIRECTORY))
SESSION_SECRET = os.getenv("COMPANY_APP_SESSION_SECRET") or secrets.token_urlsafe(32)


class TaskStartRequest(BaseModel):
    user_task: str = Field(min_length=1, max_length=4000)
    invoice_directory: str = Field(min_length=1, max_length=2048)
    company_app_url: str = "http://127.0.0.1:8000"
    approval_granted: bool = False
    max_retries: int = Field(default=2, ge=0, le=3)


class TaskStartResponse(BaseModel):
    task_id: str
    status: str
    needs_approval: bool = False
    retry_count: int = 0
    plan: dict | None = None
    invoice_data: dict | None = None
    extraction_evidence: dict | None = None
    verification: dict | None = None
    events: list[dict] = Field(default_factory=list)
    report: str | None = None


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    database.init_db()
    yield


app = FastAPI(title="Demo Company Invoice Portal", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie="company_invoice_session",
    same_site="lax",
    https_only=False,
)


def _login_redirect(request: Request) -> RedirectResponse | None:
    if request.session.get("authenticated") is not True:
        return RedirectResponse(url="/login", status_code=303)
    return None


def _invoice_form(
    request: Request,
    *,
    error: str | None = None,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request=request,
        name="invoice_form.html",
        context={"error": error},
        status_code=status_code,
    )


@app.get("/")
def home(request: Request) -> RedirectResponse:
    destination = "/invoices" if request.session.get("authenticated") else "/login"
    return RedirectResponse(url=destination, status_code=303)


@app.get("/login")
def login_page(request: Request):
    if request.session.get("authenticated") is True:
        return RedirectResponse(url="/invoices", status_code=303)
    credentials_configured = bool(
        os.getenv("COMPANY_APP_USERNAME") and os.getenv("COMPANY_APP_PASSWORD")
    )
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": None, "credentials_configured": credentials_configured},
    )


@app.post("/login")
def login(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
):
    expected_username = os.getenv("COMPANY_APP_USERNAME", "")
    expected_password = os.getenv("COMPANY_APP_PASSWORD", "")
    if not expected_username or not expected_password:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "error": "Login is not configured. Set COMPANY_APP_USERNAME and COMPANY_APP_PASSWORD in .env.",
                "credentials_configured": False,
            },
            status_code=503,
        )

    username_matches = secrets.compare_digest(username, expected_username)
    password_matches = secrets.compare_digest(password, expected_password)
    if not (username_matches and password_matches):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "error": "Those demo credentials were not recognized.",
                "credentials_configured": True,
            },
            status_code=401,
        )

    request.session.clear()
    request.session["authenticated"] = True
    return RedirectResponse(url="/invoices", status_code=303)


@app.post("/logout")
def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@app.get("/invoices")
def invoices_page(request: Request):
    redirect = _login_redirect(request)
    if redirect:
        return redirect
    message = request.session.pop("flash_message", None)
    return templates.TemplateResponse(
        request=request,
        name="invoices.html",
        context={"invoices": database.list_invoices(), "message": message},
    )


@app.get("/invoices/new")
def new_invoice_page(request: Request):
    redirect = _login_redirect(request)
    if redirect:
        return redirect
    return _invoice_form(request)


@app.post("/invoices/new")
def save_invoice(
    request: Request,
    invoice_number: Annotated[str, Form()],
    company_name: Annotated[str, Form()],
    amount: Annotated[str, Form()],
    due_date: Annotated[str, Form()],
):
    redirect = _login_redirect(request)
    if redirect:
        return redirect

    try:
        invoice = InvoiceData(
            invoice_number=invoice_number,
            company_name=company_name,
            amount=amount,
            due_date=due_date,
        )
    except ValidationError:
        return _invoice_form(
            request,
            error="Check the invoice fields. Amount must be positive and due date valid.",
            status_code=422,
        )

    try:
        database.add_invoice(invoice)
    except database.DuplicateInvoiceError as error:
        return _invoice_form(request, error=str(error), status_code=409)

    request.session["flash_message"] = "Invoice saved successfully."
    return RedirectResponse(url="/invoices", status_code=303)


@app.post("/api/tasks", response_model=TaskStartResponse)
def start_task_api(task_request: TaskStartRequest) -> TaskStartResponse:
    """Run an invoice workflow and return its final state and evidence."""
    state = run_invoice_task(
        user_task=task_request.user_task,
        invoice_directory=task_request.invoice_directory,
        company_app_url=task_request.company_app_url,
        approval_granted=task_request.approval_granted,
        max_retries=task_request.max_retries,
    )
    return TaskStartResponse(
        task_id=state["task_id"],
        status=state.get("status", "blocked"),
        needs_approval=state.get("needs_approval", False),
        retry_count=state.get("retry_count", 0),
        plan=state.get("plan"),
        invoice_data=state.get("invoice_data"),
        extraction_evidence=state.get("extraction_evidence"),
        verification=state.get("verification"),
        events=state.get("events", []),
        report=state.get("report"),
    )


@app.get("/api/tasks")
def list_tasks_api(limit: int = Query(default=50, ge=1, le=200)) -> list[dict]:
    """List recent task summaries for dashboards and API clients."""
    return MemoryManager().list_tasks(limit=limit)


@app.get("/api/tasks/{task_id}")
def get_task_api(task_id: str) -> dict:
    """Return one persisted task together with its append-only execution log."""
    memory = MemoryManager()
    task = memory.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    task["events"] = memory.get_events(task_id)
    return task