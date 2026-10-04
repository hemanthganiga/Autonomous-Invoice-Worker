"""LangGraph nodes that plan, execute, observe, recover, verify, and report."""

from collections.abc import Callable
from typing import Any

from agents.executor import execute_action
from agents.observer import observe_action
from agents.planner import TaskPlan, create_plan
from agents.recovery import choose_recovery
from agents.reporter import build_report
from agents.verifier import verify_invoice_record
from company_app.database import get_invoice
from memory.memory_manager import MemoryManager
from workflow.state import TaskState

PlanProvider = Callable[[str], TaskPlan]
ActionExecutor = Callable[[str, dict[str, Any]], dict[str, Any]]
Verifier = Callable[[dict[str, Any], str | None], dict[str, Any]]


def _append_event(
    memory: MemoryManager,
    state: TaskState,
    event_type: str,
    status: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    event = memory.log_event(
        state["task_id"],
        event_type,
        status,
        message,
        details,
    )
    return [*state.get("events", []), event]


def create_nodes(
    memory: MemoryManager,
    *,
    plan_provider: PlanProvider | None = None,
    action_executor: ActionExecutor | None = None,
    verifier: Verifier | None = None,
) -> dict[str, Callable[[TaskState], dict[str, Any]]]:
    executor = action_executor or execute_action
    verify = verifier or verify_invoice_record

    def planner_node(state: TaskState) -> dict[str, Any]:
        try:
            plan = create_plan(state["user_task"], plan_provider)
        except Exception as error:
            message = str(error) if error.__class__.__name__ == "PlannerError" else (
                f"Planning failed ({type(error).__name__})"
            )
            memory.update_task(state["task_id"], status="blocked")
            return {
                "status": "blocked",
                "error": message,
                "report": None,
                "events": _append_event(memory, state, "planner", "failed", message),
            }

        plan_data = plan.model_dump(mode="json")
        if plan.requires_approval:
            approved = state.get("approval_granted", False)
            status = "blocked" if approved else "waiting_approval"
            reason = plan.approval_reason or "sensitive action"
            message = (
                "Approval was recorded, but this workflow has no tool for the requested sensitive action."
                if approved
                else f"Human approval is required for {reason}; no task actions were executed."
            )
            memory.update_task(state["task_id"], status=status, plan=plan_data)
            return {
                "plan": plan_data,
                "status": status,
                "needs_approval": not approved,
                "approval_reason": reason,
                "error": message if approved else None,
                "next_action": None,
                "events": _append_event(
                    memory,
                    state,
                    "planner",
                    status,
                    message,
                    {"approval_reason": reason},
                ),
            }

        memory.update_task(state["task_id"], status="running", plan=plan_data)
        message = f"Created a {len(plan.steps)}-step allowlisted invoice plan"
        return {
            "plan": plan_data,
            "status": "running",
            "needs_approval": False,
            "next_action": plan.steps[0].action,
            "retry_count": 0,
            "error": None,
            "events": _append_event(memory, state, "planner", "success", message, plan_data),
        }

    def executor_node(state: TaskState) -> dict[str, Any]:
        action = state.get("next_action") or ""
        try:
            result = executor(action, dict(state))
            error = None
        except Exception as exception:
            result = None
            error = f"{type(exception).__name__}: {exception}"
        return {
            "last_action": action,
            "last_result": result,
            "error": error,
        }

    def observer_node(state: TaskState) -> dict[str, Any]:
        action = state.get("last_action", "")
        success, message = observe_action(action, state.get("last_result"), state.get("error"))
        if not success and not state.get("error"):
            state_error = message
        else:
            state_error = state.get("error")

        event_details: dict[str, Any] = {"action": action}
        result = state.get("last_result") or {}
        if success and action == "extract_invoice_data":
            event_details.update(
                {
                    "source_file": result.get("source_file"),
                    "retrieved_pages": result.get("retrieved_pages", []),
                    "extraction_evidence": result.get("extraction_evidence", {}),
                }
            )

        updates: dict[str, Any] = {
            "last_success": success,
            "observation": message,
            "status": "running" if success else "recovering",
            "error": state_error,
            "events": _append_event(
                memory,
                state,
                "observer",
                "success" if success else "failed",
                message,
                event_details,
            ),
        }

        if success and action == "search_invoice_files":
            files = result["files"]
            updates.update(
                {"invoice_files": files, "file_index": 0, "next_action": "extract_invoice_data"}
            )
        elif success and action == "extract_invoice_data":
            invoice_data = result["invoice_data"]
            updates["invoice_data"] = invoice_data
            updates["extraction_evidence"] = result.get("extraction_evidence", {})
            updates["next_action"] = "save_invoice"
            memory.update_task(state["task_id"], invoice_data=invoice_data)
        elif success and action == "save_invoice":
            updates["browser_evidence"] = result["browser_evidence"]
            updates["next_action"] = "verify_invoice"
        elif success and action == "verify_invoice":
            updates["status"] = "completed"
            memory.update_task(
                state["task_id"],
                status="completed",
                verification=result,
            )

        return updates

    def verifier_node(state: TaskState) -> dict[str, Any]:
        try:
            result = verify(state["invoice_data"], state.get("browser_evidence"))
            error = None
        except Exception as exception:
            result = {"verified": False}
            error = f"{type(exception).__name__}: {exception}"
        return {
            "last_action": "verify_invoice",
            "last_result": result,
            "verification": result,
            "error": error,
        }

    def recovery_node(state: TaskState) -> dict[str, Any]:
        action = state.get("last_action", "")
        already_saved = False
        invoice_data = state.get("invoice_data")
        if action == "save_invoice" and invoice_data:
            saved = get_invoice(
                str(invoice_data["invoice_number"]),
                str(invoice_data["company_name"]),
            )
            already_saved = bool(
                saved
                and str(saved.amount) == str(invoice_data["amount"])
                and saved.due_date.isoformat() == str(invoice_data["due_date"])
            )

        decision = choose_recovery(
            action=action,
            retry_count=state.get("retry_count", 0),
            max_retries=state.get("max_retries", 2),
            file_index=state.get("file_index", 0),
            file_count=len(state.get("invoice_files", [])),
            save_already_persisted=already_saved,
        )
        status = decision.status
        error = state.get("error")
        if status == "blocked" and not error:
            error = decision.message
        memory.update_task(state["task_id"], status=status)
        return {
            "status": status,
            "error": error,
            "retry_count": decision.retry_count,
            "file_index": decision.file_index
            if decision.file_index is not None
            else state.get("file_index", 0),
            "next_action": decision.next_action,
            "recovery_target": decision.next_node,
            "needs_user_input": decision.needs_user_input,
            "last_result": None,
            "events": _append_event(
                memory,
                state,
                "recovery",
                status,
                decision.message,
                {"failed_action": action, "retry_count": decision.retry_count},
            ),
        }

    def reporter_node(state: TaskState) -> dict[str, Any]:
        report = build_report(dict(state))
        memory.update_task(
            state["task_id"],
            status=state.get("status", "blocked"),
            final_report=report,
        )
        return {
            "report": report,
            "events": _append_event(memory, state, "reporter", state.get("status", "blocked"), report),
        }

    return {
        "planner": planner_node,
        "executor": executor_node,
        "observer": observer_node,
        "verifier": verifier_node,
        "recovery": recovery_node,
        "reporter": reporter_node,
    }