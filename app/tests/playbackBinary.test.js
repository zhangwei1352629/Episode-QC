const test = require("node:test");
const assert = require("node:assert/strict");

const {
  decodeMotionFrame,
  decodeRobotActionFrame
} = require("../services/playbackBinary");

test("decodes fixed-width motion cache frames", () => {
  const buffer = Buffer.alloc(16 + 2 * 7 * 4 + 2);
  buffer.writeBigInt64LE(1234n, 0);
  buffer.writeBigInt64LE(7n, 8);
  let offset = 16;
  for (const value of [0, 1, 2, 3, 4, 5, 1, 0, 0, 0, 0.5, 0.5, 0.5, 0.5]) {
    buffer.writeFloatLE(value, offset);
    offset += 4;
  }
  buffer[offset] = 1;
  buffer[offset + 1] = 0;

  const frame = decodeMotionFrame(buffer, 2);

  assert.deepEqual(frame.positions, [[0, 1, 2], [3, 4, 5]]);
  assert.deepEqual(frame.rotations_wxyz, [[1, 0, 0, 0], [0.5, 0.5, 0.5, 0.5]]);
  assert.deepEqual(frame.validity, [true, false]);
  assert.equal(frame.source_timestamp_ns, 1234);
  assert.equal(frame.sequence, 7);
});

test("decodes policy action cache frames", () => {
  const buffer = Buffer.alloc(16 + 29 * 4);
  buffer.writeBigInt64LE(-1n, 0);
  buffer.writeBigInt64LE(9n, 8);
  for (let index = 0; index < 29; index += 1) {
    buffer.writeFloatLE(index / 10, 16 + index * 4);
  }

  const frame = decodeRobotActionFrame(buffer, "policy");

  assert.equal(frame.source_timestamp_ns, null);
  assert.equal(frame.sequence, 9);
  assert.equal(frame.joint_positions.length, 29);
  assert.ok(Math.abs(frame.joint_positions[28] - 2.8) < 1e-5);
});

test("decodes policy target cache frames", () => {
  const buffer = Buffer.alloc(16 + 29 * 4);
  buffer.writeBigInt64LE(12n, 0);
  buffer.writeBigInt64LE(4n, 8);
  for (let index = 0; index < 29; index += 1) {
    buffer.writeFloatLE(index / 20, 16 + index * 4);
  }

  const frame = decodeRobotActionFrame(buffer, "policy_target");

  assert.equal(frame.source_timestamp_ns, 12);
  assert.equal(frame.sequence, 4);
  assert.equal(frame.joint_positions.length, 29);
  assert.ok(Math.abs(frame.joint_positions[28] - 1.4) < 1e-5);
});
