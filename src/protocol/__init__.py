"""Shared protocol definitions for agent-runner communication."""

from src.protocol.schema import (
    STATUS_BUILDING,
    STATUS_CLAIMED,
    STATUS_COMPLETED,
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

__all__ = [
    "STATUS_BUILDING",
    "STATUS_CLAIMED",
    "STATUS_COMPLETED",
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
