"""Build and invoke the conditional LangGraph invoice workflow."""

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from agents.planner import TaskPlan
from memory.memory_manager import MemoryManager
from workflow.nodes import ActionExecutor, PlanProvider, Verifier, create_nodes
from workflow.state import TaskState


def route_after_planner(state: TaskState) -> str:
    return "executor" if state.get("status") == "running" else "reporter"


def route_after_observer(state: TaskState) -> str:
    if not state.get("last_success"):
        return "recovery"
    if state.get("last_action") == "save_invoice":
        return "verifier"
    if state.get("last_action") == "verify_invoice":
        return "reporter"
    return "executor"


def route_after_recovery(state: TaskState) -> str:
    return state.get("recovery_target", "reporter")


def build_workflow(
    memory: MemoryManager | None = None,
    *,
    plan_provider: PlanProvider | None = None,
    action_executor: ActionExecutor | None = None,
    verifier: Verifier | None = None,
):
    """Compile the planner -> tools -> observer/recovery -> verifier -> reporter graph."""
    memory_manager = memory or MemoryManager()
    nodes = create_nodes(
        memory_manager,
        plan_provider=plan_provider,
        action_executor=action_executor,
        verifier=verifier,
    )

    graph = StateGraph(TaskState)
    for node_name, node_function in nodes.items():
        graph.add_node(node_name, node_function)

    graph.add_edge(START, "planner")
    graph.add_conditional_edges(
        "planner",
        route_after_planner,
        {"executor": "executor", "reporter": "reporter"},
    )
    graph.add_edge("executor", "observer")
    graph.add_conditional_edges(
        "observer",
        route_after_observer,
        {
            "executor": "executor",
            "verifier": "verifier",
            "recovery": "recovery",
            "reporter": "reporter",
        },
    )
    graph.add_edge("verifier", "observer")
    graph.add_conditional_edges(
        "recovery",
        route_after_recovery,
        {"executor": "executor", "verifier": "verifier", "reporter": "reporter"},
    )
    graph.add_edge("reporter", END)
    return graph.compile()


def run_invoice_task(
    user_task: str,
    invoice_directory: str,
    company_app_url: str = "http://127.0.0.1:8000",
    *,
    approval_granted: bool = False,
    max_retries: int = 2,
    memory: MemoryManager | None = None,
    plan_provider: PlanProvider | None = None,
    action_executor: ActionExecutor | None = None,
    verifier: Verifier | None = None,
    task_id: str | None = None,
    progress_callback: Callable[[TaskState], None] | None = None,
) -> TaskState:
    """Create a durable task record, then execute the compiled invoice workflow."""
    if not 0 <= max_retries <= 3:
        raise ValueError("max_retries must be between 0 and 3")

    memory_manager = memory or MemoryManager()
    identifier = task_id or str(uuid4())
    memory_manager.create_task(identifier, user_task)
    initial_state: TaskState = {
        "task_id": identifier,
        "user_task": user_task,
        "invoice_directory": invoice_directory,
        "company_app_url": company_app_url,
        "approval_granted": approval_granted,
        "needs_approval": False,
        "status": "queued",
        "retry_count": 0,
        "max_retries": max_retries,
        "file_index": 0,
        "events": [],
    }
    graph = build_workflow(
        memory_manager,
        plan_provider=plan_provider,
        action_executor=action_executor,
        verifier=verifier,
    )
    if progress_callback is None:
        return graph.invoke(initial_state)

    final_state: TaskState | None = None
    for snapshot in graph.stream(initial_state, stream_mode="values"):
        final_state = snapshot
        progress_callback(snapshot)
    if final_state is None:
        raise RuntimeError("The workflow ended without producing a state snapshot")
    return final_state