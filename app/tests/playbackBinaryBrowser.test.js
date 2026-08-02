const assert = require("node:assert/strict");
const test = require("node:test");

test("browser decoder reads motion frames", async () => {
  const { decodeMotionFrame } = await import("../renderer/playback-binary.mjs");
  const buffer = Buffer.alloc(16 + 2 * 7 * 4 + 2);
  buffer.writeBigInt64LE(-1n, 0);
  buffer.writeBigInt64LE(6n, 8);
  let offset = 16;
  for (let index = 0; index < 14; index += 1) {
    buffer.writeFloatLE(index / 10, offset);
    offset += 4;
  }
  buffer[offset] = 1;
  buffer[offset + 1] = 0;

  const frame = decodeMotionFrame(buffer, 2);

  assert.equal(frame.source_timestamp_ns, null);
  assert.equal(frame.sequence, 6);
  assert.deepEqual(frame.validity, [true, false]);
  assert.ok(Math.abs(frame.positions[1][2] - 0.5) < 1e-5);
  assert.ok(Math.abs(frame.rotations_wxyz[1][3] - 1.3) < 1e-5);
});

test("browser decoder reads Policy target frames", async () => {
  const { decodeRobotActionFrame } = await import("../renderer/playback-binary.mjs");
  const buffer = Buffer.alloc(16 + 29 * 4);
  buffer.writeBigInt64LE(25n, 0);
  buffer.writeBigInt64LE(7n, 8);
  for (let index = 0; index < 29; index += 1) buffer.writeFloatLE(index / 20, 16 + index * 4);

  const frame = decodeRobotActionFrame(buffer, "policy_target");

  assert.equal(frame.source_timestamp_ns, 25);
  assert.equal(frame.sequence, 7);
  assert.ok(Math.abs(frame.joint_positions[28] - 1.4) < 1e-5);
});
