"""Shared protocol definitions for agent-runner communication."""

from __future__ import annotations

from autoforge.protocol.schema import (
    STATUS_BUILDING,
    STATUS_BUILT,
    STATUS_CLAIMED,
    STATUS_COMPLETED,
    STATUS_DEPLOYED,
    STATUS_DEPLOYING,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    VALID_STATUSES,
    VALID_TRANSITIONS,
    StatusLiteral,
    TestRequest,
    extract_metric,
    request_fields,
    validate_status,
    validate_transition,
)

GIT_TIMEOUT = 60
"""Timeout in seconds for git subprocess calls (shared by agent and runner)."""

__all__ = [
    "GIT_TIMEOUT",
    "STATUS_BUILDING",
    "STATUS_BUILT",
    "STATUS_CLAIMED",
    "STATUS_COMPLETED",
    "STATUS_DEPLOYED",
    "STATUS_DEPLOYING",
    "STATUS_FAILED",
    "STATUS_PENDING",
    "STATUS_RUNNING",
    "VALID_STATUSES",
    "VALID_TRANSITIONS",
    "StatusLiteral",
    "TestRequest",
    "extract_metric",
    "request_fields",
    "validate_status",
    "validate_transition",
]
