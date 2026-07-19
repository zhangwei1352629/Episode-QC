import io

import numpy as np
from PIL import Image

from episode_qc.flow_verify import FlowVerifyConfig, verify_frames_flow
from episode_qc.mcap_video import VideoFrame


def test_block_flow_reports_local_motion_inconsistency():
    previous = _texture_frame()
    current = _shift_with_fill(previous, shift_y=0, shift_x=4)
    next_frame = _shift_with_fill(previous, shift_y=0, shift_x=8)
    current[20:44, 12:36] = previous[20:44, 12:36]
    frames = [
        _video_frame(0, previous),
        _video_frame(1, current),
        _video_frame(2, next_frame),
    ]

    payload = verify_frames_flow(
        frames,
        topic="/camera/test/image/jpeg",
        config=FlowVerifyConfig(
            resize=(64, 64),
            block_size=4,
            search_radius=6,
            threshold=0.55,
            min_component_width=8,
            min_component_height=8,
        ),
    )

    assert payload["summary"]["candidates"] >= 1
    assert payload["candidates"][0]["detector"] == "flow_block_residual"
    assert payload["candidates"][0]["frame_index"] == 1


def _texture_frame() -> np.ndarray:
    rng = np.random.default_rng(9)
    texture = rng.random((64, 64), dtype=np.float32)
    vertical_gradient = np.linspace(0.0, 0.35, 64, dtype=np.float32)[:, None]
    return np.clip((texture * 0.65) + vertical_gradient, 0.0, 1.0)


def _shift_with_fill(image: np.ndarray, *, shift_y: int, shift_x: int, fill: float = 0.0) -> np.ndarray:
    shifted = np.full_like(image, fill)
    height, width = image.shape
    src_y0 = max(0, -shift_y)
    src_y1 = min(height, height - shift_y)
    dst_y0 = max(0, shift_y)
    dst_y1 = min(height, height + shift_y)
    src_x0 = max(0, -shift_x)
    src_x1 = min(width, width - shift_x)
    dst_x0 = max(0, shift_x)
    dst_x1 = min(width, width + shift_x)
    shifted[dst_y0:dst_y1, dst_x0:dst_x1] = image[src_y0:src_y1, src_x0:src_x1]
    return shifted


def _video_frame(index: int, image: np.ndarray) -> VideoFrame:
    return VideoFrame(
        topic="/camera/test/image/jpeg",
        index=index,
        log_time_ns=index,
        publish_time_ns=index,
        sequence=index,
        timestamp_ns=index,
        frame_id="test",
        format="jpeg",
        jpeg=_jpeg_bytes(image),
    )


def _jpeg_bytes(image: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(np.uint8(np.clip(image, 0.0, 1.0) * 255)).save(buffer, format="JPEG")
    return buffer.getvalue()
