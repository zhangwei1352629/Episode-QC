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

test("SOMA 根位姿从 ROS 坐标转换到 Three 坐标并保留相对平移", async () => {
  const { robotRootPoseInThree } = await import("../renderer/g1-pose.mjs");
  const pose = robotRootPoseInThree([1.2, -0.4, 0.79], [1, 0, 0, 0], [1.0, -0.5]);
  closeTo(pose.position[0], 0.2);
  closeTo(pose.position[1], 0.79);
  closeTo(pose.position[2], -0.1);
  assert.deepEqual(pose.quaternionXyzw.map((item) => Math.abs(item) < 1e-12 ? 0 : item), [0, 0, 0, 1]);
});

test("支撑脚选择带滞回且在明显抬脚后切换", async () => {
  const { chooseSupportFoot } = await import("../renderer/g1-pose.mjs");
  assert.equal(chooseSupportFoot(0.001, 0.006), "left");
  assert.equal(chooseSupportFoot(0.010, 0.000, "left"), "left");
  assert.equal(chooseSupportFoot(0.030, 0.000, "left"), "right");
  assert.equal(chooseSupportFoot(Number.NaN, 0.004, "left"), "right");
});
