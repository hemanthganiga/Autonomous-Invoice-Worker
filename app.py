"""Streamlit dashboard for running and reviewing invoice-worker tasks."""

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import streamlit as st

from config import PROJECT_ROOT
from memory.memory_manager import MemoryManager
from workflow.graph import run_invoice_task


st.set_page_config(
    page_title="Autonomous Invoice Worker",
    page_icon="",
    layout="wide",
)

st.markdown(
    """
    <style>
    :root { --ink: #20312d; --muted: #65746e; --line: #dce5e1; --accent: #087b69; }
    .block-container { max-width: 1280px; padding-top: 1.7rem; padding-bottom: 3rem; }
    h1, h2, h3 { color: var(--ink); }
    div[data-testid="stForm"] { border: 1px solid var(--line); border-radius: 6px; padding: 1.2rem; background: #fbfdfc; }
    div[data-testid="stMetric"] { border-left: 3px solid var(--accent); padding-left: 0.8rem; }
    .eyebrow { color: var(--muted); font-size: 0.82rem; font-weight: 650; text-transform: uppercase; }
    </style>
    """,
    unsafe_allow_html=True,
)


def _format_task(task_id: str | None, tasks: dict[str, dict[str, Any]]) -> str:
    if task_id is None:
        return "Choose a saved task"
    item = tasks[task_id]
    summary = item["user_task"].replace("\n", " ")
    if len(summary) > 58:
        summary = summary[:55] + "..."
    return f"{item['status'].replace('_', ' ').title()} · {summary}"


def _history_view(memory: MemoryManager, task_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    task = memory.get_task(task_id)
    if task is None:
        raise KeyError(f"Task no longer exists: {task_id}")
    events = memory.get_events(task_id)
    extraction_event = next(
        (
            event
            for event in events
            if event["event_type"] == "observer"
            and event.get("details", {}).get("action") == "extract_invoice_data"
        ),
        {},
    )
    state = {
        "task_id": task_id,
        "user_task": task["user_task"],
        "status": task["status"],
        "plan": task["plan"],
        "invoice_data": task["invoice_data"],
        "extraction_evidence": extraction_event.get("details", {}).get(
            "extraction_evidence", {}
        ),
        "verification": task["verification"],
        "report": task["final_report"],
        "needs_approval": task["status"] == "waiting_approval",
        "retry_count": sum(event["event_type"] == "recovery" for event in events),
    }
    return state, events


def _render_task(state: dict[str, Any], events: list[dict[str, Any]]) -> None:
    status = state.get("status", "unknown")
    st.markdown("<div class='eyebrow'>Task result</div>", unsafe_allow_html=True)
    summary_columns = st.columns(3)
    summary_columns[0].metric("Status", status.replace("_", " ").title())
    summary_columns[1].metric("Retries", state.get("retry_count", 0))
    verification = state.get("verification") or {}
    verification_text = "Verified" if verification.get("verified") is True else "Not verified"
    summary_columns[2].metric("Final check", verification_text)

    if state.get("report"):
        if status == "completed" and verification.get("verified") is True:
            st.success(state["report"])
        elif status == "waiting_approval":
            st.warning(state["report"])
        else:
            st.error(state["report"])

    plan = state.get("plan") or {}
    if plan.get("steps"):
        st.markdown("### Execution plan")
        st.dataframe(plan["steps"], hide_index=True, use_container_width=True)

    observer_events = [event for event in events if event["event_type"] == "observer"]
    completed_actions = [
        event.get("details", {}).get("action", event["message"])
        for event in observer_events
        if event["status"] == "success"
    ]
    failed_actions = [
        event.get("details", {}).get("action", event["message"])
        for event in observer_events
        if event["status"] == "failed"
    ]
    recovery_events = [event for event in events if event["event_type"] == "recovery"]

    left, right = st.columns(2)
    with left:
        st.markdown("### Completed actions")
        if completed_actions:
            for action in completed_actions:
                st.write(f"✓ {action}")
        else:
            st.caption("No actions completed.")
    with right:
        st.markdown("### Failed actions")
        if failed_actions:
            for action in failed_actions:
                st.write(f"• {action}")
        else:
            st.caption("No action failures recorded.")

    if recovery_events:
        st.markdown("### Retry history")
        st.dataframe(
            [
                {
                    "time": event["created_at"],
                    "status": event["status"],
                    "details": event["message"],
                }
                for event in recovery_events
            ],
            hide_index=True,
            use_container_width=True,
        )

    invoice = state.get("invoice_data")
    if invoice:
        st.markdown("### Extracted invoice information")
        st.json(invoice)
    if state.get("extraction_evidence"):
        st.markdown("### Source evidence")
        st.json(state["extraction_evidence"])

    if verification:
        st.markdown("### Verification evidence")
        st.json(verification)

    if events:
        st.markdown("### Execution log")
        st.dataframe(
            [
                {
                    "time": event["created_at"],
                    "event": event["event_type"],
                    "status": event["status"],
                    "details": event["message"],
                }
                for event in events
            ],
            hide_index=True,
            use_container_width=True,
        )


st.title("Autonomous Invoice Worker")
st.caption("Local invoice processing · approval-gated · evidence-verified")

memory = MemoryManager()
saved_tasks = memory.list_tasks(limit=50)
saved_by_id = {task["task_id"]: task for task in saved_tasks}

with st.sidebar:
    st.subheader("Task history")
    selected_task_id = st.selectbox(
        "Recent runs",
        options=[None, *saved_by_id.keys()],
        format_func=lambda task_id: _format_task(task_id, saved_by_id),
        label_visibility="collapsed",
    )
    st.divider()
    st.caption("Company portal: http://127.0.0.1:8000")
    st.caption("Use dummy invoices only.")

st.markdown("### Start a task")
with st.form("task_form"):
    user_task = st.text_area(
        "Instruction",
        value=(
            "Find the latest invoice in the selected folder, enter its details into "
            "the internal company app, and verify it was saved."
        ),
        height=96,
    )
    input_columns = st.columns([2, 1])
    with input_columns[0]:
        invoice_directory = st.text_input(
            "Invoice folder",
            value="",
            placeholder=str(PROJECT_ROOT / "sample_invoices"),
        )
    with input_columns[1]:
        company_app_url = st.text_input(
            "Local company app URL",
            value="http://127.0.0.1:8000",
        )
    max_retries = st.slider("Maximum automatic retries", min_value=0, max_value=3, value=2)
    start_task = st.form_submit_button("Start task", type="primary", use_container_width=True)

if start_task:
    folder = Path(invoice_directory).expanduser()
    parsed_url = urlparse(company_app_url)
    if not user_task.strip():
        st.error("Enter a task instruction first.")
    elif not folder.is_dir():
        st.error("Choose an existing folder containing text-based invoice PDFs.")
    elif parsed_url.hostname not in {"localhost", "127.0.0.1", "::1"}:
        st.error("The company app URL must point to localhost.")
    else:
        live_status = st.status("Starting workflow", expanded=True)
        event_count = [0]

        def show_progress(snapshot: dict[str, Any]) -> None:
            events = snapshot.get("events", [])
            if len(events) > event_count[0]:
                for event in events[event_count[0] :]:
                    live_status.write(f"{event['event_type']}: {event['message']}")
                event_count[0] = len(events)
            else:
                live_status.write(snapshot.get("status", "running").replace("_", " ").title())

        try:
            result = run_invoice_task(
                user_task=user_task,
                invoice_directory=str(folder),
                company_app_url=company_app_url,
                max_retries=max_retries,
                progress_callback=show_progress,
            )
        except Exception as error:
            live_status.update(label="Workflow could not start", state="error")
            st.error(f"Workflow error ({type(error).__name__}): {error}")
        else:
            st.session_state["last_task_id"] = result["task_id"]
            if result.get("status") == "completed":
                live_status.update(label="Invoice saved and verified", state="complete")
            elif result.get("status") == "waiting_approval":
                live_status.update(label="Paused for approval", state="complete")
            else:
                live_status.update(label="Task stopped", state="error")

if selected_task_id:
    try:
        task_state, task_events = _history_view(memory, selected_task_id)
    except KeyError as error:
        st.warning(str(error))
    else:
        _render_task(task_state, task_events)
elif st.session_state.get("last_task_id"):
    task_state, task_events = _history_view(memory, st.session_state["last_task_id"])
    _render_task(task_state, task_events)
else:
    st.info("Choose an invoice folder and start a task. Previous runs are available in Task history.")