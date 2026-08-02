const assert = require("node:assert/strict");
const test = require("node:test");

const closeTo = (actual, expected, tolerance = 1e-9) => assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} != ${expected}`);
const axisQuaternion = (axis, angle) => {
  const half = angle / 2;
  const values = { x: [Math.cos(half), Math.sin(half), 0, 0], y: [Math.cos(half), 0, Math.sin(half), 0], z: [Math.cos(half), 0, 0, Math.sin(half)] };
  return values[axis];
};

test("相同的前臂和手部旋转映射为中立腕关节", async () => {
  const { wristAnglesFromWorldQuaternions } = await import("../renderer/g1-pose.mjs");
  for (const side of ["left", "right"]) {
    const result = wristAnglesFromWorldQuaternions(side, [1, 0, 0, 0], [1, 0, 0, 0]);
    closeTo(result.roll, 0);
    closeTo(result.pitch, 0);
    closeTo(result.yaw, 0);
  }
});

test("人体左右前臂坐标正确映射到 G1 腕关节轴", async () => {
  const { wristAnglesFromWorldQuaternions } = await import("../renderer/g1-pose.mjs");
  const angle = 0.3;
  const identity = [1, 0, 0, 0];
  closeTo(wristAnglesFromWorldQuaternions("left", identity, axisQuaternion("y", angle)).roll, angle);
  closeTo(wristAnglesFromWorldQuaternions("right", identity, axisQuaternion("y", angle)).roll, -angle);
  closeTo(wristAnglesFromWorldQuaternions("left", identity, axisQuaternion("z", angle)).pitch, angle);
  closeTo(wristAnglesFromWorldQuaternions("right", identity, axisQuaternion("z", angle)).pitch, -angle);
  closeTo(wristAnglesFromWorldQuaternions("left", identity, axisQuaternion("x", angle)).yaw, angle);
  closeTo(wristAnglesFromWorldQuaternions("right", identity, axisQuaternion("x", angle)).yaw, angle);
});

test("人体手臂伸直时使用 G1 肘关节的九十度机械中立位", async () => {
  const { g1ElbowAngleFromHumanFlexion } = await import("../renderer/g1-pose.mjs");
  closeTo(g1ElbowAngleFromHumanFlexion(0), Math.PI / 2);
  closeTo(g1ElbowAngleFromHumanFlexion(Math.PI / 2), 0);
});
