from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Iterator, TextIO


@dataclass(frozen=True)
class BvhJoint:
    name: str
    parent_index: int
    offset: tuple[float, float, float]
    channels: tuple[str, ...]
    channel_start: int


@dataclass(frozen=True)
class BvhHeader:
    joints: tuple[BvhJoint, ...]
    frame_count: int
    frame_time_sec: float
    channel_count: int


def read_bvh_header(path: str | Path) -> BvhHeader:
    with Path(path).open("r", encoding="utf-8-sig", errors="replace") as source:
        return _read_header(source)


def iter_bvh_motion(path: str | Path) -> Iterator[dict[str, object]]:
    """Yield BVH frames as world-space poses without retaining the file in memory."""
    with Path(path).open("r", encoding="utf-8-sig", errors="replace") as source:
        header = _read_header(source)
        emitted = 0
        buffered: list[float] = []
        for line in source:
            if emitted >= header.frame_count:
                break
            buffered.extend(float(value) for value in line.split())
            while len(buffered) >= header.channel_count and emitted < header.frame_count:
                values = buffered[: header.channel_count]
                del buffered[: header.channel_count]
                positions, rotations = _world_pose(header.joints, values)
                yield {
                    "positions": positions,
                    "rotations_wxyz": rotations,
                    "validity": [True] * len(header.joints),
                    "source_timestamp_ns": round(emitted * header.frame_time_sec * 1_000_000_000),
                    "sequence": emitted,
                }
                emitted += 1
        if emitted != header.frame_count:
            raise ValueError(f"BVH 帧数据不完整：声明 {header.frame_count}，读取 {emitted}")
        if buffered:
            raise ValueError("BVH 帧数据包含多余通道值")


def _read_header(source: TextIO) -> BvhHeader:
    first = _next_content_line(source)
    if first.upper() != "HIERARCHY":
        raise ValueError("BVH 缺少 HIERARCHY")

    mutable_joints: list[dict[str, object]] = []
    stack: list[int | None] = []
    pending: int | None = None
    pending_end_site = False
    channel_count = 0

    for raw_line in source:
        line = raw_line.strip()
        if not line:
            continue
        if line.upper() == "MOTION":
            break
        parts = line.split()
        keyword = parts[0].upper()
        if keyword in {"ROOT", "JOINT"}:
            if len(parts) < 2:
                raise ValueError(f"BVH {keyword} 缺少名称")
            parent = next((value for value in reversed(stack) if value is not None), -1)
            pending = len(mutable_joints)
            mutable_joints.append(
                {
                    "name": " ".join(parts[1:]),
                    "parent_index": parent,
                    "offset": (0.0, 0.0, 0.0),
                    "channels": (),
                    "channel_start": channel_count,
                }
            )
            pending_end_site = False
        elif keyword == "END":
            pending = None
            pending_end_site = True
        elif keyword == "{":
            stack.append(None if pending_end_site else pending)
            pending = None
            pending_end_site = False
        elif keyword == "}":
            if not stack:
                raise ValueError("BVH 层级括号不匹配")
            stack.pop()
        elif keyword == "OFFSET":
            current = stack[-1] if stack else None
            if current is not None:
                if len(parts) != 4:
                    raise ValueError("BVH OFFSET 必须包含三个坐标")
                mutable_joints[current]["offset"] = tuple(float(value) for value in parts[1:4])
        elif keyword == "CHANNELS":
            current = stack[-1] if stack else None
            if current is None or len(parts) < 2:
                raise ValueError("BVH CHANNELS 所在层级无效")
            count = int(parts[1])
            channels = tuple(parts[2:])
            if len(channels) != count:
                raise ValueError("BVH CHANNELS 数量不一致")
            mutable_joints[current]["channels"] = channels
            mutable_joints[current]["channel_start"] = channel_count
            channel_count += count
    else:
        raise ValueError("BVH 缺少 MOTION")

    if stack:
        raise ValueError("BVH HIERARCHY 括号未闭合")
    if not mutable_joints or channel_count <= 0:
        raise ValueError("BVH 未定义骨架通道")

    frames_line = _next_content_line(source)
    frame_time_line = _next_content_line(source)
    if not frames_line.lower().startswith("frames:"):
        raise ValueError("BVH 缺少 Frames")
    if not frame_time_line.lower().startswith("frame time:"):
        raise ValueError("BVH 缺少 Frame Time")
    frame_count = int(frames_line.split(":", 1)[1].strip())
    frame_time_sec = float(frame_time_line.split(":", 1)[1].strip())
    if frame_count < 0 or not math.isfinite(frame_time_sec) or frame_time_sec <= 0:
        raise ValueError("BVH 帧数或帧间隔无效")

    return BvhHeader(
        joints=tuple(BvhJoint(**value) for value in mutable_joints),
        frame_count=frame_count,
        frame_time_sec=frame_time_sec,
        channel_count=channel_count,
    )


def _next_content_line(source: TextIO) -> str:
    for line in source:
        value = line.strip()
        if value:
            return value
    raise ValueError("BVH 文件意外结束")


def _world_pose(
    joints: tuple[BvhJoint, ...],
    values: list[float],
) -> tuple[list[list[float]], list[list[float]]]:
    positions_cm: list[tuple[float, float, float]] = []
    rotations: list[list[float]] = []
    for joint in joints:
        translation = [0.0, 0.0, 0.0]
        local_rotation = (1.0, 0.0, 0.0, 0.0)
        for offset, channel in enumerate(joint.channels):
            value = values[joint.channel_start + offset]
            lower = channel.lower()
            if lower.endswith("position"):
                translation["xyz".index(lower[0])] = value
            elif lower.endswith("rotation"):
                local_rotation = _quat_multiply(
                    local_rotation,
                    _axis_angle_quaternion(lower[0], math.radians(value)),
                )
            else:
                raise ValueError(f"BVH 不支持的通道: {channel}")

        local_position = tuple(joint.offset[index] + translation[index] for index in range(3))
        if joint.parent_index < 0:
            world_position = local_position
            world_rotation = local_rotation
        else:
            parent_position = positions_cm[joint.parent_index]
            parent_rotation = rotations[joint.parent_index]
            rotated = _rotate_vector(tuple(parent_rotation), local_position)
            world_position = tuple(parent_position[index] + rotated[index] for index in range(3))
            world_rotation = _quat_multiply(tuple(parent_rotation), local_rotation)
        positions_cm.append(world_position)
        rotations.append(list(_normalize_quaternion(world_rotation)))
    # FZMotion BVH uses centimetres. QC's motion contract uses metres.
    return [[component / 100.0 for component in position] for position in positions_cm], rotations


def _axis_angle_quaternion(axis: str, angle: float) -> tuple[float, float, float, float]:
    half = angle / 2.0
    sine = math.sin(half)
    if axis == "x":
        return math.cos(half), sine, 0.0, 0.0
    if axis == "y":
        return math.cos(half), 0.0, sine, 0.0
    if axis == "z":
        return math.cos(half), 0.0, 0.0, sine
    raise ValueError(f"BVH 旋转轴无效: {axis}")


def _quat_multiply(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    lw, lx, ly, lz = left
    rw, rx, ry, rz = right
    return (
        lw * rw - lx * rx - ly * ry - lz * rz,
        lw * rx + lx * rw + ly * rz - lz * ry,
        lw * ry - lx * rz + ly * rw + lz * rx,
        lw * rz + lx * ry - ly * rx + lz * rw,
    )


def _normalize_quaternion(value: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    length = math.sqrt(sum(component * component for component in value))
    if length <= 1e-12:
        return 1.0, 0.0, 0.0, 0.0
    return tuple(component / length for component in value)  # type: ignore[return-value]


def _rotate_vector(
    quaternion: tuple[float, float, float, float],
    vector: tuple[float, float, float],
) -> tuple[float, float, float]:
    normalized = _normalize_quaternion(quaternion)
    pure = (0.0, *vector)
    conjugate = (normalized[0], -normalized[1], -normalized[2], -normalized[3])
    rotated = _quat_multiply(_quat_multiply(normalized, pure), conjugate)
    return rotated[1], rotated[2], rotated[3]
