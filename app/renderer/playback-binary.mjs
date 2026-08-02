export const MOTION_FRAME_ENCODING = "episode-qc-motion-f32-le-v1";
export const ACTION_FRAME_ENCODING = "episode-qc-action-f32-le-v1";

function asBytes(value) {
  if (value instanceof Uint8Array) return value;
  if (value instanceof ArrayBuffer) return new Uint8Array(value);
  throw new TypeError("二进制播放帧必须是 ArrayBuffer 或 Uint8Array");
}

function optionalInt64(view, offset) {
  const value = view.getBigInt64(offset, true);
  return value === -1n ? null : Number(value);
}

export function decodeMotionFrame(value, jointCount) {
  if (!Number.isInteger(jointCount) || jointCount <= 0) throw new Error("Mocap 关节数量无效");
  const bytes = asBytes(value);
  const expectedLength = 16 + jointCount * 7 * 4 + jointCount;
  if (bytes.byteLength !== expectedLength) {
    throw new Error(`Mocap 二进制帧长度无效: ${bytes.byteLength} != ${expectedLength}`);
  }
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let offset = 16;
  const positions = [];
  for (let joint = 0; joint < jointCount; joint += 1) {
    positions.push([
      view.getFloat32(offset, true),
      view.getFloat32(offset + 4, true),
      view.getFloat32(offset + 8, true),
    ]);
    offset += 12;
  }
  const rotations_wxyz = [];
  for (let joint = 0; joint < jointCount; joint += 1) {
    rotations_wxyz.push([
      view.getFloat32(offset, true),
      view.getFloat32(offset + 4, true),
      view.getFloat32(offset + 8, true),
      view.getFloat32(offset + 12, true),
    ]);
    offset += 16;
  }
  return {
    positions,
    rotations_wxyz,
    validity: Array.from(bytes.subarray(offset), (item) => item !== 0),
    source_timestamp_ns: optionalInt64(view, 0),
    sequence: optionalInt64(view, 8),
  };
}

export function decodeRobotActionFrame(value, sourceKey) {
  const hasRootPose = sourceKey === "soma";
  if (!["policy", "policy_target", "soma"].includes(sourceKey)) {
    throw new Error(`机器人动作源无效: ${sourceKey}`);
  }
  const bytes = asBytes(value);
  const expectedLength = 16 + 29 * 4 + (hasRootPose ? 7 * 4 : 0);
  if (bytes.byteLength !== expectedLength) {
    throw new Error(`机器人动作二进制帧长度无效: ${bytes.byteLength} != ${expectedLength}`);
  }
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let offset = 16;
  const joint_positions = [];
  for (let joint = 0; joint < 29; joint += 1) {
    joint_positions.push(view.getFloat32(offset, true));
    offset += 4;
  }
  const frame = {
    source_key: sourceKey,
    joint_positions,
    source_timestamp_ns: optionalInt64(view, 0),
    sequence: optionalInt64(view, 8),
  };
  if (hasRootPose) {
    frame.root_position = [
      view.getFloat32(offset, true),
      view.getFloat32(offset + 4, true),
      view.getFloat32(offset + 8, true),
    ];
    offset += 12;
    frame.root_quaternion_wxyz = [
      view.getFloat32(offset, true),
      view.getFloat32(offset + 4, true),
      view.getFloat32(offset + 8, true),
      view.getFloat32(offset + 12, true),
    ];
  }
  return frame;
}
