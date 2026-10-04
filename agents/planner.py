"""Create a constrained invoice plan with Groq structured output."""

import re
from collections.abc import Callable
from typing import Literal

from langchain_groq import ChatGroq
from pydantic import BaseModel, Field

from config import GROQ_API_KEY, GROQ_MODEL

PlanAction = Literal[
    "search_invoice_files",
    "extract_invoice_data",
    "save_invoice",
    "verify_invoice",
]

REQUIRED_ACTIONS: tuple[PlanAction, ...] = (
    "search_invoice_files",
    "extract_invoice_data",
    "save_invoice",
    "verify_invoice",
)


class PlanStep(BaseModel):
    action: PlanAction
    description: str = Field(min_length=1, max_length=240)


class TaskPlan(BaseModel):
    summary: str = Field(min_length=1, max_length=500)
    steps: list[PlanStep]
    requires_approval: bool = False
    approval_reason: str | None = None


class PlannerError(ValueError):
    """Raised when a request or model plan is outside the supported workflow."""


_SENSITIVE_PATTERNS = (
    ("financial transaction", re.compile(r"\b(?:pay(?:ment)?|purchase|refund)\b", re.I)),
    (
        "outbound communication",
        re.compile(
            r"\b(?:email|e-mail|send\s+(?:an?\s+)?(?:email|message)|"
            r"message\s+(?:the\s+)?(?:vendor|supplier|customer)|"
            r"contact\s+(?:the\s+)?(?:vendor|supplier|customer))\b",
            re.I,
        ),
    ),
)


def approval_reason_for_task(user_task: str) -> str | None:
    """Return a policy reason when the request includes an unapproved sensitive action."""
    for reason, pattern in _SENSITIVE_PATTERNS:
        if pattern.search(user_task):
            return reason
    return None


def _call_groq_planner(user_task: str) -> TaskPlan:
    if not GROQ_API_KEY:
        raise PlannerError("GROQ_API_KEY is not configured")

    prompt = f"""You plan tasks for a local invoice-entry demo.
Only these actions are available and they must appear once, in this exact order:
search_invoice_files, extract_invoice_data, save_invoice, verify_invoice.
Do not propose payments, external communication, arbitrary code, or other tools.
Return a concise summary and a short description for each action.
Set requires_approval when the requested goal contains a sensitive action.

User task: {user_task}"""

    structured_model = ChatGroq(
        api_key=GROQ_API_KEY,
        model=GROQ_MODEL,
        temperature=0,
    ).with_structured_output(TaskPlan)
    response = structured_model.invoke(prompt)
    return response if isinstance(response, TaskPlan) else TaskPlan.model_validate(response)


def create_plan(
    user_task: str,
    plan_provider: Callable[[str], TaskPlan] | None = None,
) -> TaskPlan:
    """Plan the only automated workflow currently supported: local invoice entry."""
    normalized_task = user_task.strip()
    if not normalized_task:
        raise PlannerError("Enter a task before starting the worker")

    approval_reason = approval_reason_for_task(normalized_task)
    if approval_reason:
        return TaskPlan(
            summary="The request includes an action that requires human approval.",
            steps=[],
            requires_approval=True,
            approval_reason=approval_reason,
        )

    if "invoice" not in normalized_task.casefold():
        raise PlannerError("This workflow currently supports local invoice-entry tasks only")

    plan = plan_provider(normalized_task) if plan_provider else _call_groq_planner(normalized_task)
    if not isinstance(plan, TaskPlan):
        plan = TaskPlan.model_validate(plan)

    actions = tuple(step.action for step in plan.steps)
    if actions != REQUIRED_ACTIONS:
        raise PlannerError("The planner returned actions outside the approved invoice workflow")
    return plan