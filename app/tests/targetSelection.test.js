const test = require("node:test");
const assert = require("node:assert/strict");

test("显式对象选择按关节、相机和基础对象解析", async () => {
  const { resolveSelectedTarget } = await import("../renderer/target-selection.mjs");
  const cameras = [{ stream_id: "cam-head", topic: "/camera/head", display_name: "头部相机" }];

  assert.deepEqual(
    resolveSelectedTarget({ baseTarget: "mocap", cameras }),
    {
      targetType: "mocap",
      targetKey: "/mocap/human_motion",
      selectionKey: null,
      displayName: "全身动作",
    },
  );
  assert.equal(
    resolveSelectedTarget({ selectedCameraId: "cam-head", baseTarget: "mocap", cameras }).displayName,
    "画面 · 头部相机",
  );
  assert.equal(
    resolveSelectedTarget({ selectedJoint: "left_wrist", jointDisplayName: () => "左手腕" }).displayName,
    "关节 · 左手腕",
  );
});

test("标签只在当前显式对象受支持时可用", async () => {
  const { labelSupportsTarget, targetTypesDescription } = await import("../renderer/target-selection.mjs");
  const label = { target_types: ["camera", "joint"] };

  assert.equal(labelSupportsTarget(label, { targetType: "camera" }), true);
  assert.equal(labelSupportsTarget(label, { targetType: "mocap" }), false);
  assert.equal(targetTypesDescription(label.target_types), "画面、关节");
});
