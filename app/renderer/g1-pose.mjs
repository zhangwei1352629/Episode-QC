const WRIST_SOURCE_TO_G1 = {
  left: [
    0, 1, 0,
    0, 0, 1,
    1, 0, 0,
  ],
  right: [
    0, -1, 0,
    0, 0, -1,
    1, 0, 0,
  ],
};

const clamp = (value, minimum, maximum) => Math.max(minimum, Math.min(maximum, value));
const SQRT_HALF = Math.SQRT1_2;
const ROS_TO_THREE_WXYZ = [SQRT_HALF, -SQRT_HALF, 0, 0];

function normalizedQuaternionWxyz(value) {
  if (!Array.isArray(value) || value.length !== 4 || value.some((item) => !Number.isFinite(Number(item)))) return null;
  const quaternion = value.map(Number);
  const length = Math.hypot(...quaternion);
  if (length < 1e-8) return null;
  return quaternion.map((item) => item / length);
}

function multiplyQuaternionWxyz(first, second) {
  const [aw, ax, ay, az] = first;
  const [bw, bx, by, bz] = second;
  return [
    aw * bw - ax * bx - ay * by - az * bz,
    aw * bx + ax * bw + ay * bz - az * by,
    aw * by - ax * bz + ay * bw + az * bx,
    aw * bz + ax * by - ay * bx + az * bw,
  ];
}

export function robotRootPoseInThree(rootPosition, rootQuaternionWxyz, planarOriginRos = null) {
  const sourceQuaternion = normalizedQuaternionWxyz(rootQuaternionWxyz) || [1, 0, 0, 0];
  const inverseBasis = [ROS_TO_THREE_WXYZ[0], -ROS_TO_THREE_WXYZ[1], 0, 0];
  const convertedQuaternion = normalizedQuaternionWxyz(
    multiplyQuaternionWxyz(multiplyQuaternionWxyz(ROS_TO_THREE_WXYZ, sourceQuaternion), inverseBasis),
  );
  let position = null;
  if (Array.isArray(rootPosition) && rootPosition.length === 3 && rootPosition.every((item) => Number.isFinite(Number(item)))) {
    const source = rootPosition.map(Number);
    const origin = Array.isArray(planarOriginRos) && planarOriginRos.length >= 2
      ? planarOriginRos.map(Number)
      : [0, 0];
    position = [source[0] - (origin[0] || 0), source[2], -(source[1] - (origin[1] || 0))];
  }
  return {
    position,
    quaternionXyzw: [convertedQuaternion[1], convertedQuaternion[2], convertedQuaternion[3], convertedQuaternion[0]],
  };
}

export function chooseSupportFoot(leftHeight, rightHeight, current = null, switchThreshold = 0.015) {
  const heights = { left: Number(leftHeight), right: Number(rightHeight) };
  const valid = Object.entries(heights).filter(([, value]) => Number.isFinite(value));
  if (!valid.length) return null;
  if (valid.length === 1) return valid[0][0];
  if (current === "left" || current === "right") {
    const other = current === "left" ? "right" : "left";
    if (heights[current] <= heights[other] + Math.max(0, Number(switchThreshold) || 0)) return current;
  }
  return heights.left <= heights.right ? "left" : "right";
}

function rotationMatrixFromQuaternionWxyz(value) {
  const [w, x, y, z] = value;
  return [
    1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w),
    2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w),
    2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y),
  ];
}

function multiplyMatrix3(first, second) {
  const result = new Array(9).fill(0);
  for (let row = 0; row < 3; row += 1) {
    for (let column = 0; column < 3; column += 1) {
      for (let index = 0; index < 3; index += 1) result[row * 3 + column] += first[row * 3 + index] * second[index * 3 + column];
    }
  }
  return result;
}

function transposeMatrix3(value) {
  return [value[0], value[3], value[6], value[1], value[4], value[7], value[2], value[5], value[8]];
}

function xyzAnglesFromRotationMatrix(value) {
  const pitch = Math.asin(clamp(value[2], -1, 1));
  if (Math.abs(value[2]) < 0.9999999) {
    return [Math.atan2(-value[5], value[8]), pitch, Math.atan2(-value[1], value[0])];
  }
  return [Math.atan2(value[7], value[4]), pitch, 0];
}

export function wristAnglesFromWorldQuaternions(side, forearmWxyz, handWxyz) {
  const forearm = normalizedQuaternionWxyz(forearmWxyz);
  const hand = normalizedQuaternionWxyz(handWxyz);
  const basis = WRIST_SOURCE_TO_G1[side];
  if (!forearm || !hand || !basis) return { roll: 0, pitch: 0, yaw: 0 };

  const inverseForearm = [forearm[0], -forearm[1], -forearm[2], -forearm[3]];
  const relative = normalizedQuaternionWxyz(multiplyQuaternionWxyz(inverseForearm, hand));
  if (!relative) return { roll: 0, pitch: 0, yaw: 0 };
  const sourceRotation = rotationMatrixFromQuaternionWxyz(relative);
  const g1Rotation = multiplyMatrix3(multiplyMatrix3(basis, sourceRotation), transposeMatrix3(basis));
  const [roll, pitch, yaw] = xyzAnglesFromRotationMatrix(g1Rotation);
  return { roll, pitch, yaw };
}

export function g1ElbowAngleFromHumanFlexion(flexion) {
  return Math.PI / 2 - (Number.isFinite(flexion) ? flexion : 0);
}
