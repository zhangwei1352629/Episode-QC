const test = require("node:test");
const assert = require("node:assert/strict");

test("同一步骤新物品继承人工语义、步骤、手和位置，但清空物品及单条异常", async () => {
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
  assert.equal(draft.values.semantic_description, "左手把黑色上衣从椅子放入洗衣机");
  assert.equal(draft.values.exception_type, "");
  assert.equal(draft.values.recovery_action, "");
});

test("沿用同一物品可以切换固定步骤，并继承人工语义但不复用单条异常", async () => {
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
  assert.equal(draft.values.semantic_description, "左手拿起黑色上衣");
  assert.equal(draft.values.exception_type, "");
  assert.equal(draft.values.recovery_action, "");
});

test("只有声明人工语义字段的固定动作标签进入语义复用流程", async () => {
  const { labelUsesEgoSemanticFields } = await import("../renderer/ego-annotation-draft.mjs");

  assert.equal(labelUsesEgoSemanticFields({ fields: [{ code: "semantic_description" }] }), true);
  assert.equal(labelUsesEgoSemanticFields({ fields: [{ code: "body_part" }] }), false);
  assert.equal(labelUsesEgoSemanticFields({ fields: [{ code: "camera_issue" }] }), false);
});

test("沿用来源是任务顺序中的上一条 Episode，而不是筛选列表或上一个标签", async () => {
  const {
    egoDraftForLabel,
    previousEpisodeForCurrent,
  } = await import("../renderer/ego-annotation-draft.mjs");
  const episodes = [
    { id: "ep-001", episode_name: "EP0001" },
    { id: "ep-002", episode_name: "EP0002" },
    { id: "ep-003", episode_name: "EP0003" },
  ];
  const previous = previousEpisodeForCurrent(episodes, "ep-003");
  assert.equal(previous.id, "ep-002");

  const draft = egoDraftForLabel([
    {
      label_code: "phase_pick_clothes",
      start_offset_ns: 10,
      attributes: {
        semantic_description: "左手从椅子拿起黑色衣服",
        body_part: "left_hand",
        object_name: "黑色衣服",
      },
    },
    {
      label_code: "phase_open_washer_door",
      start_offset_ns: 20,
      attributes: {
        semantic_description: "右手打开洗衣机门",
        body_part: "right_hand",
        object_name: "洗衣机门",
      },
    },
  ], "phase_pick_clothes");

  assert.equal(draft.labelCode, "phase_pick_clothes");
  assert.equal(draft.values.semantic_description, "左手从椅子拿起黑色衣服");
  assert.equal(draft.values.body_part, "left_hand");
  assert.equal(draft.values.object_name, "黑色衣服");
});

test("上一条 Episode 没有当前步骤描述时不回退到其他标签", async () => {
  const { egoDraftForLabel } = await import("../renderer/ego-annotation-draft.mjs");
  const draft = egoDraftForLabel([
    {
      label_code: "phase_open_washer_door",
      attributes: { semantic_description: "右手打开洗衣机门" },
    },
  ], "phase_pick_clothes");

  assert.equal(draft, null);
});
