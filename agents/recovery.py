"""Choose bounded retries and safe alternative invoice files."""

from typing import Literal

from pydantic import BaseModel


class RecoveryDecision(BaseModel):
    next_node: Literal["executor", "verifier", "reporter"]
    next_action: str | None = None
    file_index: int | None = None
    retry_count: int
    status: Literal["running", "blocked"]
    message: str
    needs_user_input: bool = False


def choose_recovery(
    *,
    action: str,
    retry_count: int,
    max_retries: int,
    file_index: int,
    file_count: int,
    save_already_persisted: bool = False,
) -> RecoveryDecision:
    """Select the next safe step or stop when no bounded alternative remains."""
    if action == "save_invoice" and save_already_persisted:
        return RecoveryDecision(
            next_node="verifier",
            next_action="verify_invoice",
            file_index=file_index,
            retry_count=retry_count,
            status="running",
            message="The save may have completed; verifying the stored record instead of resubmitting.",
        )

    if retry_count >= max_retries:
        return RecoveryDecision(
            next_node="reporter",
            retry_count=retry_count,
            status="blocked",
            message="Automatic retries were exhausted; no completion is claimed.",
            needs_user_input=True,
        )

    next_retry_count = retry_count + 1
    if action == "extract_invoice_data" and file_index + 1 < file_count:
        return RecoveryDecision(
            next_node="executor",
            next_action="extract_invoice_data",
            file_index=file_index + 1,
            retry_count=next_retry_count,
            status="running",
            message="The current PDF could not be parsed; trying the next candidate file.",
        )

    if action == "save_invoice":
        return RecoveryDecision(
            next_node="executor",
            next_action="save_invoice",
            file_index=file_index,
            retry_count=next_retry_count,
            status="running",
            message="The save failed and no matching record exists; retrying within the limit.",
        )

    if action == "verify_invoice":
        return RecoveryDecision(
            next_node="verifier",
            next_action="verify_invoice",
            file_index=file_index,
            retry_count=next_retry_count,
            status="running",
            message="Retrying the read-only verification step within the limit.",
        )

    return RecoveryDecision(
        next_node="reporter",
        retry_count=next_retry_count,
        status="blocked",
        message="No safe automatic alternative is available.",
        needs_user_input=True,
    )