from __future__ import annotations

from episode_qc.compressed_image import ProtobufDecodeError
from episode_qc.compressed_video import decode_compressed_video


def _varint(value: int) -> bytes:
    encoded = bytearray()
    while value >= 0x80:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def _bytes_field(number: int, value: bytes) -> bytes:
    return _varint((number << 3) | 2) + _varint(len(value)) + value


def test_decode_foxglove_compressed_video():
    timestamp = _varint(8) + _varint(123) + _varint(16) + _varint(456)
    payload = b"".join(
        [
            _bytes_field(1, timestamp),
            _bytes_field(2, b"head_left_camera"),
            _bytes_field(3, b"\x00\x00\x00\x01h264-packet"),
            _bytes_field(4, b"h264"),
        ]
    )

    decoded = decode_compressed_video(payload)

    assert decoded.timestamp_seconds == 123
    assert decoded.timestamp_nanos == 456
    assert decoded.frame_id == "head_left_camera"
    assert decoded.data == b"\x00\x00\x00\x01h264-packet"
    assert decoded.format == "h264"


def test_decode_foxglove_compressed_video_requires_payload_data():
    try:
        decode_compressed_video(_bytes_field(4, b"h264"))
    except ProtobufDecodeError as exc:
        assert "field 3" in str(exc)
    else:
        raise AssertionError("missing CompressedVideo data was accepted")
