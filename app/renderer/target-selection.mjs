const TARGET_TYPE_NAMES = {
  global: "全局",
  mocap: "全身动作",
  joint: "关节",
  camera: "画面",
  stream: "数据流",
  retarget: "重定向动作",
  robot: "机器人",
  hand: "手部",
};

export function targetTypeName(targetType) {
  return TARGET_TYPE_NAMES[targetType] || targetType || "未知对象";
}

export function targetTypesDescription(targetTypes) {
  return [...new Set(targetTypes || [])].map(targetTypeName).join("、") || "未配置";
}

export function resolveSelectedTarget({
  selectedJoint = null,
  selectedCameraId = null,
  baseTarget = "global",
  cameras = [],
  jointDisplayName = (value) => value,
} = {}) {
  if (selectedJoint) {
    return {
      targetType: "joint",
      targetKey: selectedJoint,
      selectionKey: selectedJoint,
      displayName: `关节 · ${jointDisplayName(selectedJoint)}`,
    };
  }
  if (selectedCameraId) {
    const camera = cameras.find((item) => item.stream_id === selectedCameraId);
    return {
      targetType: "camera",
      targetKey: camera?.topic || selectedCameraId,
      selectionKey: selectedCameraId,
      displayName: `画面 · ${camera?.display_name || selectedCameraId}`,
    };
  }
  if (baseTarget === "mocap") {
    return {
      targetType: "mocap",
      targetKey: "/mocap/human_motion",
      selectionKey: null,
      displayName: "全身动作",
    };
  }
  return {
    targetType: "global",
    targetKey: null,
    selectionKey: null,
    displayName: "全局",
  };
}

export function labelSupportsTarget(label, target) {
  return Boolean(target?.targetType && (label?.target_types || []).includes(target.targetType));
}
