"""Agent-side modules for the autosearch optimization loop."""

from src.agent.git_ops import (
    force_push_submodule,
    full_revert,
    git_add_commit_push,
    git_submodule_head,
    record_result_or_revert,
)
from src.agent.history import append_result, best_result, load_history
from src.agent.loop import main
from src.agent.metric import below_threshold, compare_metric
from src.agent.protocol import (
    create_request,
    find_latest_request,
    find_request_by_seq,
    next_sequence,
    poll_for_completion,
)
from src.agent.strategy import extract_profile_summary, format_context, validate_change

__all__ = [
    "append_result",
    "below_threshold",
    "best_result",
    "compare_metric",
    "create_request",
    "extract_profile_summary",
    "find_latest_request",
    "find_request_by_seq",
    "force_push_submodule",
    "format_context",
    "full_revert",
    "git_add_commit_push",
    "git_submodule_head",
    "load_history",
    "main",
    "next_sequence",
    "poll_for_completion",
    "record_result_or_revert",
    "validate_change",
]
