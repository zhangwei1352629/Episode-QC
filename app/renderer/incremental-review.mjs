const VALID_DECISIONS = new Set([
  "pass",
  "pass_with_labels",
  "trim",
  "repair",
  "recollect",
  "reject",
]);

const CONFIRMED_REVIEW_STATUSES = new Set([
  "completed",
  "reviewed",
  "needs_recheck",
]);

export function pendingInheritedDecision(episode) {
  if (!episode || episode.review_status !== "unreviewed" || episode.quality_decision) {
    return "";
  }
  const decision = String(episode.previous_review?.decision || "").trim();
  return VALID_DECISIONS.has(decision) ? decision : "";
}

export function canConfirmEpisode(episode) {
  return Boolean(
    CONFIRMED_REVIEW_STATUSES.has(episode?.review_status)
      || pendingInheritedDecision(episode)
  );
}
