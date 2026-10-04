from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from agents.planner import REQUIRED_ACTIONS, PlanStep, TaskPlan
from agents.recovery import choose_recovery
from memory.memory_manager import MemoryManager
from tools.invoice_data import InvoiceData
from workflow.graph import run_invoice_task


def make_plan(_: str) -> TaskPlan:
    return TaskPlan(
        summary="Find, extract, save, and verify the invoice locally.",
        steps=[PlanStep(action=action, description=action) for action in REQUIRED_ACTIONS],
    )


def make_invoice_data() -> dict[str, Any]:
    invoice = InvoiceData(
        invoice_number="INV-WORKFLOW-001",
        company_name="Example Supplier",
        amount=Decimal("428.75"),
        due_date=date(2026, 12, 15),
    )
    return invoice.model_dump(mode="json")


def make_executor(*, fail_first_extraction: bool = False, fail_all_extractions: bool = False):
    calls: list[tuple[str, int]] = []

    def execute(action: str, state: dict[str, Any]) -> dict[str, Any]:
        calls.append((action, state.get("file_index", 0)))
        if action == "search_invoice_files":
            return {"files": ["bad.pdf", "good.pdf"]}
        if action == "extract_invoice_data":
            if fail_all_extractions or (fail_first_extraction and state["file_index"] == 0):
                raise ValueError("Dummy PDF could not be parsed")
            return {"invoice_data": make_invoice_data(), "source_file": "good.pdf"}
        if action == "save_invoice":
            return {"browser_evidence": "INV-WORKFLOW-001 Example Supplier $428.75 2026-12-15"}
        raise AssertionError(f"Unexpected action: {action}")

    return execute, calls


def make_verifier(invoice_data: dict[str, Any], browser_evidence: str | None):
    return {
        "verified": True,
        "record_id": 12,
        "database_record": invoice_data,
        "browser_evidence": browser_evidence,
    }


def run_test_task(
    tmp_path,
    *,
    executor,
    max_retries: int = 2,
    user_task: str = "Find the latest invoice and enter it in the internal company app.",
    approval_granted: bool = False,
    plan_provider=make_plan,
    progress_callback=None,
):
    memory = MemoryManager(tmp_path / "task-history.sqlite3")
    result = run_invoice_task(
        user_task,
        str(tmp_path / "invoices"),
        memory=memory,
        plan_provider=plan_provider,
        action_executor=executor,
        verifier=make_verifier,
        max_retries=max_retries,
        approval_granted=approval_granted,
        task_id="test-task",
        progress_callback=progress_callback,
    )
    return result, memory


def test_successful_workflow_persists_verified_report_and_audit(tmp_path):
    executor, calls = make_executor()
    result, memory = run_test_task(tmp_path, executor=executor)

    assert result["status"] == "completed"
    assert result["verification"]["verified"] is True
    assert "Invoice saved and verified" in result["report"]
    assert [action for action, _ in calls] == [
        "search_invoice_files",
        "extract_invoice_data",
        "save_invoice",
    ]

    saved_task = memory.get_task("test-task")
    assert saved_task is not None
    assert saved_task["status"] == "completed"
    assert saved_task["invoice_data"]["invoice_number"] == "INV-WORKFLOW-001"
    assert saved_task["verification"]["verified"] is True
    event_types = [event["event_type"] for event in memory.get_events("test-task")]
    assert "planner" in event_types
    assert "observer" in event_types
    assert "verifier" not in event_types
    assert event_types[-1] == "reporter"


def test_extraction_failure_uses_next_pdf_with_bounded_retry(tmp_path):
    executor, calls = make_executor(fail_first_extraction=True)
    result, memory = run_test_task(tmp_path, executor=executor, max_retries=1)

    assert result["status"] == "completed"
    assert ("extract_invoice_data", 1) in calls
    assert result["retry_count"] == 1
    assert any(event["event_type"] == "recovery" for event in memory.get_events("test-task"))


def test_exhausted_extraction_retries_never_claim_completion(tmp_path):
    executor, calls = make_executor(fail_all_extractions=True)
    result, _ = run_test_task(tmp_path, executor=executor, max_retries=1)

    assert result["status"] == "blocked"
    assert not any(action == "save_invoice" for action, _ in calls)
    assert "not completed" in result["report"].casefold()


def test_sensitive_request_pauses_without_executing_tools(tmp_path):
    def unexpected_executor(action: str, state: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("Sensitive request must not execute any tool")

    result, memory = run_test_task(
        tmp_path,
        executor=unexpected_executor,
        user_task="Pay this invoice and email the supplier.",
        plan_provider=lambda _: pytest.fail("Sensitive task should not call the model"),
    )

    assert result["status"] == "waiting_approval"
    assert result["needs_approval"] is True
    assert "no actions were executed" in result["report"].casefold()
    assert memory.get_events("test-task")[-1]["event_type"] == "reporter"


def test_recovery_does_not_repeat_a_save_that_already_persisted():
    decision = choose_recovery(
        action="save_invoice",
        retry_count=0,
        max_retries=2,
        file_index=0,
        file_count=1,
        save_already_persisted=True,
    )

    assert decision.next_node == "verifier"
    assert decision.next_action == "verify_invoice"
    assert decision.retry_count == 0


def test_progress_callback_receives_full_state_snapshots(tmp_path):
    executor, _ = make_executor()
    snapshots = []
    result, _ = run_test_task(
        tmp_path,
        executor=executor,
        progress_callback=snapshots.append,
    )

    assert snapshots
    assert snapshots[0]["status"] == "queued"
    assert any(snapshot.get("status") == "running" for snapshot in snapshots)
    assert snapshots[-1]["status"] == "completed"
    assert result["task_id"] == snapshots[-1]["task_id"]


def test_memory_manager_lists_recent_task_summaries(tmp_path):
    memory = MemoryManager(tmp_path / "task-history.sqlite3")
    memory.create_task("task-a", "First task")
    memory.create_task("task-b", "Second task")

    tasks = memory.list_tasks(limit=1)

    assert len(tasks) == 1
    assert tasks[0]["task_id"] == "task-b"
    assert tasks[0]["user_task"] == "Second task"