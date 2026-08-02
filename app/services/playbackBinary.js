const MOTION_FRAME_ENCODING = "episode-qc-motion-f32-le-v1";
const ACTION_FRAME_ENCODING = "episode-qc-action-f32-le-v1";

function optionalInt64(buffer, offset) {
  const value = buffer.readBigInt64LE(offset);
  return value === -1n ? null : Number(value);
}

function decodeMotionFrame(buffer, jointCount) {
  if (!Number.isInteger(jointCount) || jointCount <= 0) {
    throw new Error("Mocap 关节数量无效");
  }
  const expectedLength = 16 + jointCount * 7 * 4 + jointCount;
  if (buffer.length !== expectedLength) {
    throw new Error(`Mocap 二进制帧长度无效: ${buffer.length} != ${expectedLength}`);
  }
  let offset = 16;
  const positions = [];
  for (let joint = 0; joint < jointCount; joint += 1) {
    positions.push([
      buffer.readFloatLE(offset),
      buffer.readFloatLE(offset + 4),
      buffer.readFloatLE(offset + 8)
    ]);
    offset += 12;
  }
  const rotations_wxyz = [];
  for (let joint = 0; joint < jointCount; joint += 1) {
    rotations_wxyz.push([
      buffer.readFloatLE(offset),
      buffer.readFloatLE(offset + 4),
      buffer.readFloatLE(offset + 8),
      buffer.readFloatLE(offset + 12)
    ]);
    offset += 16;
  }
  const validity = Array.from(buffer.subarray(offset), (value) => value !== 0);
  return {
    positions,
    rotations_wxyz,
    validity,
    source_timestamp_ns: optionalInt64(buffer, 0),
    sequence: optionalInt64(buffer, 8)
  };
}

function decodeRobotActionFrame(buffer, sourceKey) {
  const hasRootPose = sourceKey === "soma";
  if (sourceKey !== "policy" && sourceKey !== "policy_target" && !hasRootPose) {
    throw new Error(`机器人动作源无效: ${sourceKey}`);
  }
  const expectedLength = 16 + 29 * 4 + (hasRootPose ? 7 * 4 : 0);
  if (buffer.length !== expectedLength) {
    throw new Error(`机器人动作二进制帧长度无效: ${buffer.length} != ${expectedLength}`);
  }
  let offset = 16;
  const joint_positions = [];
  for (let joint = 0; joint < 29; joint += 1) {
    joint_positions.push(buffer.readFloatLE(offset));
    offset += 4;
  }
  const frame = {
    source_key: sourceKey,
    joint_positions,
    source_timestamp_ns: optionalInt64(buffer, 0),
    sequence: optionalInt64(buffer, 8)
  };
  if (hasRootPose) {
    frame.root_position = [
      buffer.readFloatLE(offset),
      buffer.readFloatLE(offset + 4),
      buffer.readFloatLE(offset + 8)
    ];
    offset += 12;
    frame.root_quaternion_wxyz = [
      buffer.readFloatLE(offset),
      buffer.readFloatLE(offset + 4),
      buffer.readFloatLE(offset + 8),
      buffer.readFloatLE(offset + 12)
    ];
  }
  return frame;
}

module.exports = {
  ACTION_FRAME_ENCODING,
  MOTION_FRAME_ENCODING,
  decodeMotionFrame,
  decodeRobotActionFrame
};
