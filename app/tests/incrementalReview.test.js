const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

test("未确认的复检 Episode 可以沿用上一轮结论后确认", async () => {
  const {
    canConfirmEpisode,
    pendingInheritedDecision,
  } = await import("../renderer/incremental-review.mjs");

  const episode = {
    review_status: "unreviewed",
    quality_decision: null,
    previous_review: { decision: "pass" },
  };

  assert.equal(pendingInheritedDecision(episode), "pass");
  assert.equal(canConfirmEpisode(episode), true);
});

test("无有效历史结论的 Episode 仍要求人工选择结论", async () => {
  const {
    canConfirmEpisode,
    pendingInheritedDecision,
  } = await import("../renderer/incremental-review.mjs");

  for (const episode of [
    { review_status: "unreviewed", quality_decision: null, previous_review: null },
    { review_status: "unreviewed", quality_decision: null, previous_review: { decision: "unknown" } },
  ]) {
    assert.equal(pendingInheritedDecision(episode), "");
    assert.equal(canConfirmEpisode(episode), false);
  }
});

test("已完成或待复核 Episode 保持可继续", async () => {
  const { canConfirmEpisode, pendingInheritedDecision } = await import(
    "../renderer/incremental-review.mjs"
  );

  assert.equal(canConfirmEpisode({ review_status: "completed", quality_decision: "pass" }), true);
  assert.equal(canConfirmEpisode({ review_status: "reviewed", quality_decision: "repair" }), true);
  assert.equal(canConfirmEpisode({ review_status: "needs_recheck", quality_decision: null }), true);
  assert.equal(
    pendingInheritedDecision({
      review_status: "completed",
      quality_decision: "pass",
      previous_review: { decision: "reject" },
    }),
    ""
  );
});

test("确认按钮会保存继承结论后再进入下一条", () => {
  const renderer = fs.readFileSync(
    path.resolve(__dirname, "../renderer/renderer.js"),
    "utf8"
  );

  assert.match(renderer, /pendingInheritedDecision\(episode\)/);
  assert.match(renderer, /const saved = await setDecision\(inheritedDecision\)/);
  assert.match(renderer, /继承：\$\{decisionName\(inheritedDecision\)\}（待确认）/);
});
