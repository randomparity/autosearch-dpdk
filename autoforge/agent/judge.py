"""Shared judge-dispatch helper used by both cmd_judge and the interactive loop."""

from __future__ import annotations

from autoforge.agent.git_ops import ResultContext, record_result_or_revert, record_verdict
from autoforge.campaign import CampaignConfig, judge_plugin, project_config, project_name
from autoforge.plugins.loader import load_judge
from autoforge.protocol import Direction, TestRequest


def apply_judge_verdict(
    metric: float | None,
    best_val: float | None,
    direction: Direction,
    campaign: CampaignConfig,
    request: TestRequest,
    ctx: ResultContext,
    dry_run: bool = False,
) -> None:
    """Apply keep/revert verdict from a configured judge plugin, or fall back to default.

    Args:
        metric: Metric value from this test run, or None if the test failed.
        best_val: Best metric seen so far, or None if no prior baseline.
        direction: Whether higher or lower is better.
        campaign: Full campaign config for this sprint.
        request: The completed test request.
        ctx: Result context with paths and metadata.
        dry_run: If True, skip git operations.
    """
    judge_name = judge_plugin(campaign)
    if judge_name:
        pname = project_name(campaign)
        pcfg = project_config(campaign)
        j = load_judge(pname, judge_name, project_config=pcfg, runner_config={})
        verdict = j.judge(metric, best_val, direction, campaign, request)
        print(f"Judge '{judge_name}': {'keep' if verdict.keep else 'revert'} — {verdict.reason}")
        record_verdict(verdict.keep, metric, best_val, ctx, dry_run=dry_run)
    else:
        record_result_or_revert(metric, best_val, direction, ctx, dry_run=dry_run)
