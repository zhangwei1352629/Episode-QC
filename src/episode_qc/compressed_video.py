from __future__ import annotations

from dataclasses import dataclass

from episode_qc.compressed_image import ProtobufDecodeError, _decode_timestamp, _iter_fields


@dataclass(frozen=True)
class CompressedVideo:
    timestamp_seconds: int | None
    timestamp_nanos: int | None
    frame_id: str
    data: bytes
    format: str


def decode_compressed_video(payload: bytes) -> CompressedVideo:
    """Decode the Foxglove ``CompressedVideo`` protobuf wire format."""

    timestamp_seconds: int | None = None
    timestamp_nanos: int | None = None
    frame_id = ""
    video_data: bytes | None = None
    video_format = ""
    for field_number, wire_type, value in _iter_fields(payload):
        if field_number == 1 and wire_type == 2:
            timestamp_seconds, timestamp_nanos = _decode_timestamp(value)
        elif field_number == 2 and wire_type == 2:
            frame_id = value.decode("utf-8", errors="replace")
        elif field_number == 3 and wire_type == 2:
            video_data = value
        elif field_number == 4 and wire_type == 2:
            video_format = value.decode("utf-8", errors="replace")
    if video_data is None:
        raise ProtobufDecodeError("CompressedVideo payload does not contain field 3 data")
    return CompressedVideo(
        timestamp_seconds=timestamp_seconds,
        timestamp_nanos=timestamp_nanos,
        frame_id=frame_id,
        data=video_data,
        format=video_format,
    )
