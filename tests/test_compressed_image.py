from episode_qc.compressed_image import decode_compressed_image


def test_decode_compressed_image_payload():
    jpeg = b"\xff\xd8fake-jpeg\xff\xd9"
    timestamp = _field_varint(1, 123) + _field_varint(2, 456)
    payload = (
        _field_bytes(1, timestamp)
        + _field_bytes(4, b"camera_frame")
        + _field_bytes(2, jpeg)
        + _field_bytes(3, b"jpeg")
    )

    image = decode_compressed_image(payload)

    assert image.timestamp_seconds == 123
    assert image.timestamp_nanos == 456
    assert image.frame_id == "camera_frame"
    assert image.data == jpeg
    assert image.format == "jpeg"


def _field_varint(field_number: int, value: int) -> bytes:
    return _varint((field_number << 3) | 0) + _varint(value)


def _field_bytes(field_number: int, value: bytes) -> bytes:
    return _varint((field_number << 3) | 2) + _varint(len(value)) + value


def _varint(value: int) -> bytes:
    chunks = []
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            chunks.append(byte | 0x80)
        else:
            chunks.append(byte)
            return bytes(chunks)
