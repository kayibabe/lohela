"""Immutable recommendation evidence derived at prediction creation time."""

from datetime import datetime, timezone

DECISION_POLICY_VERSION = "bet-watch-pass-v1"


def build_decision_evidence(
    *,
    q_score: float | None,
    edge: float | None,
    model_agreement: float | None,
    data_quality_score: float | None,
    active_models: list[str] | None,
    source_odds_at: datetime | None,
    kickoff_at: datetime | None,
) -> dict:
    """Return the decision and reasons without inventing missing evidence."""
    reasons: list[str] = []
    risks: list[str] = []
    has_price = edge is not None and source_odds_at is not None
    if edge is not None and edge > 0:
        reasons.append("positive_edge")
    else:
        risks.append("no_positive_edge")
    if q_score is not None and q_score >= 85:
        reasons.append("high_quality_score")
    else:
        risks.append("below_high_quality_threshold")
    if model_agreement is not None and model_agreement <= 0.05:
        reasons.append("strong_model_agreement")
    elif model_agreement is None or model_agreement > 0.10:
        risks.append("model_disagreement")
    if data_quality_score is not None and data_quality_score >= 60:
        reasons.append("good_data_quality")
    else:
        risks.append("data_quality_review")
    if active_models:
        reasons.append("active_model_set")
    else:
        risks.append("missing_active_models")
    if not source_odds_at:
        risks.append("missing_odds_timestamp")
    elif kickoff_at and source_odds_at > kickoff_at:
        risks.append("post_kickoff_odds")
    elif kickoff_at and (kickoff_at.astimezone(timezone.utc) - source_odds_at.astimezone(timezone.utc)).total_seconds() > 8 * 3600:
        risks.append("stale_odds_snapshot")

    if not has_price:
        status = "PASS"
    elif (
        q_score is not None
        and q_score >= 85
        and edge is not None
        and edge > 0
        and data_quality_score is not None
        and data_quality_score >= 60
        and bool(active_models)
    ):
        status = "BET"
    else:
        status = "WATCH"
    return {
        "status": status,
        "reasons": reasons,
        "risks": risks,
        "policy_version": DECISION_POLICY_VERSION,
    }
