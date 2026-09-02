const test = require("node:test");
const assert = require("node:assert/strict");

test("同一步骤新物品保留步骤、手和位置，但清空物品、人工语义及单条异常", async () => {
  const { sameStepNewObjectDraft } = await import("../renderer/ego-annotation-draft.mjs");
  const draft = sameStepNewObjectDraft({
    labelCode: "transfer_clothing",
    values: {
      semantic_description: "左手把黑色上衣从椅子放入洗衣机",
      body_part: "left_hand",
      object_name: "黑色上衣",
      object_color: "黑色",
      source_name: "椅子",
      target_name: "洗衣机滚筒",
      exception_type: "retry",
      recovery_action: "重新抓取",
    },
  });

  assert.equal(draft.labelCode, "transfer_clothing");
  assert.equal(draft.values.body_part, "left_hand");
  assert.equal(draft.values.source_name, "椅子");
  assert.equal(draft.values.target_name, "洗衣机滚筒");
  assert.equal(draft.values.object_name, "");
  assert.equal(draft.values.object_color, "");
  assert.equal(draft.values.semantic_description, "");
  assert.equal(draft.values.exception_type, "");
  assert.equal(draft.values.recovery_action, "");
});

test("沿用同一物品可以切换固定步骤，并且不复用人工语义和单条异常", async () => {
  const { reuseSameObjectDraft } = await import("../renderer/ego-annotation-draft.mjs");
  const draft = reuseSameObjectDraft({
    labelCode: "pick_clothing",
    values: {
      semantic_description: "左手拿起黑色上衣",
      body_part: "left_hand",
      object_name: "黑色上衣",
      object_color: "黑色",
      source_name: "椅子",
      target_name: "洗衣机滚筒",
      exception_type: "object_dropped",
      recovery_action: "重新拾起",
    },
  }, "place_clothing");

  assert.equal(draft.labelCode, "place_clothing");
  assert.equal(draft.values.object_name, "黑色上衣");
  assert.equal(draft.values.object_color, "黑色");
  assert.equal(draft.values.body_part, "left_hand");
  assert.equal(draft.values.semantic_description, "");
  assert.equal(draft.values.exception_type, "");
  assert.equal(draft.values.recovery_action, "");
});

test("只有声明人工语义字段的固定动作标签进入语义复用流程", async () => {
  const { labelUsesEgoSemanticFields } = await import("../renderer/ego-annotation-draft.mjs");

  assert.equal(labelUsesEgoSemanticFields({ fields: [{ code: "semantic_description" }] }), true);
  assert.equal(labelUsesEgoSemanticFields({ fields: [{ code: "body_part" }] }), false);
  assert.equal(labelUsesEgoSemanticFields({ fields: [{ code: "camera_issue" }] }), false);
});
