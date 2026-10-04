"""Typed state shared by all LangGraph invoice-workflow nodes."""

from typing import Any, TypedDict


class TaskState(TypedDict, total=False):
    task_id: str
    user_task: str
    invoice_directory: str
    company_app_url: str
    approval_granted: bool
    approval_reason: str | None
    needs_approval: bool
    status: str
    plan: dict[str, Any]
    invoice_files: list[str]
    file_index: int
    invoice_data: dict[str, Any]
    extraction_evidence: dict[str, Any]
    browser_evidence: str
    verification: dict[str, Any]
    next_action: str | None
    last_action: str
    last_result: dict[str, Any] | None
    last_success: bool
    observation: str
    error: str | None
    retry_count: int
    max_retries: int
    recovery_target: str
    needs_user_input: bool
    report: str
    events: list[dict[str, Any]]