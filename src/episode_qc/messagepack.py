from __future__ import annotations

import msgpack


def _extension(extension_type: int, data: bytes) -> dict[str, object]:
    return {"extension_type": extension_type, "data": data}


def decode_messagepack(payload: bytes) -> object:
    """Decode with msgpack's native extension instead of Python byte walking."""
    return msgpack.unpackb(
        payload,
        raw=False,
        strict_map_key=False,
        ext_hook=_extension,
    )
