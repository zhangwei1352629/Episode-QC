from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import Iterator


class ProtobufDecodeError(ValueError):
    """Raised when a protobuf payload cannot be decoded by the small parser."""


@dataclass(frozen=True)
class CompressedImage:
    timestamp_seconds: int | None
    timestamp_nanos: int | None
    frame_id: str
    data: bytes
    format: str


def decode_compressed_image(payload: bytes) -> CompressedImage:
    """Decode Foxglove protobuf or ROS 1 ``sensor_msgs/CompressedImage``.

    The project only needs a tiny subset of protobuf wire types here. Keeping
    this local parser avoids generating protobuf classes at runtime.
    """

    try:
        return _decode_foxglove_compressed_image(payload)
    except (ProtobufDecodeError, UnicodeDecodeError):
        return _decode_ros1_compressed_image(payload)


def _decode_foxglove_compressed_image(payload: bytes) -> CompressedImage:
    timestamp_seconds: int | None = None
    timestamp_nanos: int | None = None
    frame_id = ""
    image_data: bytes | None = None
    image_format = ""

    for field_number, wire_type, value in _iter_fields(payload):
        if field_number == 1 and wire_type == 2:
            timestamp_seconds, timestamp_nanos = _decode_timestamp(value)
        elif field_number == 2 and wire_type == 2:
            image_data = value
        elif field_number == 3 and wire_type == 2:
            image_format = value.decode("utf-8", errors="replace")
        elif field_number == 4 and wire_type == 2:
            frame_id = value.decode("utf-8", errors="replace")

    if image_data is None:
        raise ProtobufDecodeError("CompressedImage payload does not contain field 2 data")

    return CompressedImage(
        timestamp_seconds=timestamp_seconds,
        timestamp_nanos=timestamp_nanos,
        frame_id=frame_id,
        data=image_data,
        format=image_format,
    )


def _decode_ros1_compressed_image(payload: bytes) -> CompressedImage:
    """Decode the ROS 1 wire layout without depending on a ROS runtime."""

    offset = 0

    def uint32() -> int:
        nonlocal offset
        _ensure_available(payload, offset, 4)
        value = struct.unpack_from("<I", payload, offset)[0]
        offset += 4
        return value

    def string() -> str:
        nonlocal offset
        length = uint32()
        _ensure_available(payload, offset, length)
        value = payload[offset : offset + length].decode("utf-8")
        offset += length
        return value

    _sequence = uint32()
    seconds = uint32()
    nanos = uint32()
    frame_id = string()
    image_format = string()
    data_length = uint32()
    _ensure_available(payload, offset, data_length)
    image_data = payload[offset : offset + data_length]
    offset += data_length
    if offset != len(payload):
        raise ProtobufDecodeError("ROS1 CompressedImage payload contains trailing bytes")
    if not image_data:
        raise ProtobufDecodeError("ROS1 CompressedImage payload contains no image data")
    return CompressedImage(
        timestamp_seconds=seconds,
        timestamp_nanos=nanos,
        frame_id=frame_id,
        data=image_data,
        format=image_format,
    )


def _decode_timestamp(payload: bytes) -> tuple[int | None, int | None]:
    seconds: int | None = None
    nanos: int | None = None

    for field_number, wire_type, value in _iter_fields(payload):
        if wire_type != 0:
            continue
        if field_number == 1:
            seconds = value
        elif field_number == 2:
            nanos = value

    return seconds, nanos


def _iter_fields(payload: bytes) -> Iterator[tuple[int, int, bytes | int]]:
    offset = 0
    size = len(payload)

    while offset < size:
        key, offset = _read_varint(payload, offset)
        field_number = key >> 3
        wire_type = key & 0b111
        if field_number == 0:
            raise ProtobufDecodeError("protobuf field number 0 is invalid")

        if wire_type == 0:
            value, offset = _read_varint(payload, offset)
            yield field_number, wire_type, value
        elif wire_type == 1:
            _ensure_available(payload, offset, 8)
            yield field_number, wire_type, payload[offset : offset + 8]
            offset += 8
        elif wire_type == 2:
            length, offset = _read_varint(payload, offset)
            _ensure_available(payload, offset, length)
            yield field_number, wire_type, payload[offset : offset + length]
            offset += length
        elif wire_type == 5:
            _ensure_available(payload, offset, 4)
            yield field_number, wire_type, payload[offset : offset + 4]
            offset += 4
        else:
            raise ProtobufDecodeError(f"unsupported protobuf wire type {wire_type}")


def _read_varint(payload: bytes, offset: int) -> tuple[int, int]:
    result = 0
    shift = 0

    while offset < len(payload):
        byte = payload[offset]
        offset += 1
        result |= (byte & 0x7F) << shift

        if byte & 0x80 == 0:
            return result, offset

        shift += 7
        if shift > 70:
            raise ProtobufDecodeError("protobuf varint is too long")

    raise ProtobufDecodeError("unexpected end of protobuf varint")


def _ensure_available(payload: bytes, offset: int, length: int) -> None:
    if length < 0 or offset + length > len(payload):
        raise ProtobufDecodeError("protobuf field length exceeds payload size")
