import io

import numpy as np
from PIL import Image

from episode_qc.mcap_video import VideoFrame
from episode_qc.stale_region import (
    StaleRegionConfig,
    StaleRegionScore,
    _update_tearing_tracks,
    scan_frames_for_stale_regions,
    score_triplet,
    score_window,
)


def test_clean_motion_edges_do_not_create_stale_region():
    previous = _texture_frame()
    current = _shift_with_fill(previous, shift_y=0, shift_x=4)
    next_frame = _shift_with_fill(previous, shift_y=0, shift_x=8)

    regions = score_triplet(previous, current, next_frame, config=_test_config())

    assert regions == []


def test_local_stale_region_scores_above_threshold():
    previous, current, next_frame = _stale_region_triplet()

    regions = score_triplet(previous, current, next_frame, config=_test_config())

    assert len(regions) == 1
    assert regions[0].score >= StaleRegionConfig.threshold
    assert regions[0].bbox == (20, 18, 18, 20)
    assert regions[0].reference_lag == 1


def test_local_stale_region_can_match_older_reference_frame():
    stale_reference, _, next_frame = _stale_region_triplet()
    distractor = _shift_with_fill(stale_reference, shift_y=0, shift_x=2)
    current = _shift_with_fill(stale_reference, shift_y=0, shift_x=4)
    current[18:38, 20:38] = stale_reference[18:38, 20:38]

    regions = score_window([stale_reference, distractor], current, next_frame, config=_test_config())

    assert len(regions) == 1
    assert regions[0].reference_lag == 2


def test_localized_corruption_after_frame_gap_scores_above_threshold():
    previous, current, next_frame = _localized_corruption_triplet()

    regions = score_window(
        [previous],
        current,
        next_frame,
        config=_test_config(),
        frame_gap_ratio=2.1,
        sequence_gap=2,
    )

    assert len(regions) == 1
    assert regions[0].detector == "localized_corruption"
    assert regions[0].score >= StaleRegionConfig.threshold
    assert regions[0].bbox == (6, 34, 30, 28)


def test_scan_frames_reports_candidate_from_jpegs():
    previous, current, next_frame = _stale_region_triplet()
    frames = [
        _video_frame(0, previous),
        _video_frame(1, current),
        _video_frame(2, next_frame),
    ]

    result = scan_frames_for_stale_regions(
        frames,
        config=_test_config(),
    )

    assert len(result.candidates) == 1
    assert result.candidates[0].frame_index == 1
    assert result.candidates[0].bbox == (20, 18, 18, 20)
    assert result.topics["/camera/test/image/jpeg"].decoded_frames == 3


def test_scan_frames_tracks_localized_corruption_across_frames():
    previous, current, next_frame = _localized_corruption_triplet()
    continued = next_frame.copy()
    continued[36:60, 8:34] = np.roll(continued[36:60, 8:34], shift=1, axis=0)
    frames = [
        _video_frame(0, previous, sequence=0),
        _video_frame(1, current, sequence=2),
        _video_frame(2, next_frame, sequence=3),
        _video_frame(3, continued, sequence=4),
    ]

    result = scan_frames_for_stale_regions(frames, config=_test_config(threshold=0.6))

    assert [candidate.frame_index for candidate in result.candidates] == [1, 2]
    assert [candidate.event_start_frame for candidate in result.candidates] == [1, 1]
    assert [candidate.event_frame_offset for candidate in result.candidates] == [0, 1]

    payload = result.to_dict()
    assert payload["summary"]["events"] == 1
    assert payload["events"][0]["event_frame_start"] == 1
    assert payload["events"][0]["event_frame_end"] == 2


def test_temporal_tearing_requires_continuous_seed_promotion():
    config = _test_config(
        detectors=("temporal_tearing",),
        min_tearing_seed_score=0.58,
        min_tearing_event_frames=3,
    )
    tracks = []
    promoted = []

    for frame_index in range(10, 13):
        seed = _tearing_seed(
            bbox=(40, 0, 20, 24),
            frame_index=frame_index,
            score=0.62,
            frame_gap_ratio=1.38 if frame_index == 10 else 1.0,
        )
        promoted, tracks = _update_tearing_tracks(tracks, [seed], frame_index, config)

    assert len(promoted) == 1
    assert promoted[0].detector == "temporal_tearing"
    assert promoted[0].score >= config.threshold
    assert promoted[0].event_start_frame == 10
    assert promoted[0].event_frame_offset == 2


def _stale_region_triplet() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    previous = _texture_frame()
    current = _shift_with_fill(previous, shift_y=0, shift_x=4)
    next_frame = _shift_with_fill(previous, shift_y=0, shift_x=8)
    current[18:38, 20:38] = previous[18:38, 20:38]
    return previous, current, next_frame


def _localized_corruption_triplet() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    previous = _texture_frame()
    current = previous.copy()
    current[36:60, 8:34] = 0.02
    current[38:60:4, 8:34] = 0.42
    current[36:60, 10:34:5] = 0.0
    next_frame = current.copy()
    next_frame[36:60, 8:34] = np.roll(next_frame[36:60, 8:34], shift=1, axis=1)
    return previous, current, next_frame


def _test_config(
    *,
    threshold: float = StaleRegionConfig.threshold,
    detectors: tuple[str, ...] = ("stale_region", "localized_corruption"),
    min_tearing_seed_score: float = StaleRegionConfig.min_tearing_seed_score,
    min_tearing_event_frames: int = StaleRegionConfig.min_tearing_event_frames,
) -> StaleRegionConfig:
    return StaleRegionConfig(
        detectors=detectors,
        threshold=threshold,
        resize=(64, 64),
        tile_size=2,
        min_component_width=8,
        min_component_height=8,
        max_stale_delta=0.06,
        min_tearing_seed_score=min_tearing_seed_score,
        min_tearing_event_frames=min_tearing_event_frames,
    )


def _tearing_seed(
    *,
    bbox: tuple[int, int, int, int],
    frame_index: int,
    score: float,
    frame_gap_ratio: float,
) -> StaleRegionScore:
    width, height = bbox[2], bbox[3]
    area_pixels = width * height
    return StaleRegionScore(
        detector="temporal_tearing",
        score=score,
        bbox=bbox,
        area_pixels=area_pixels,
        area_ratio=area_pixels / (64 * 64),
        rectangularity=1.0,
        stale_delta=0.0,
        compensated_change=0.04,
        future_change=0.08,
        previous_to_next_change=0.09,
        temporal_contrast=0.02,
        surround_change=0.03,
        surround_contrast=0.02,
        reference_lag=1,
        localized_change=0.08,
        texture_increase=0.05,
        frame_gap_ratio=frame_gap_ratio,
        sequence_gap=1,
        motion_residual=0.04,
        event_start_frame=None,
        event_frame_offset=0,
        similarity_score=0.0,
        change_score=0.8,
        contrast_score=0.2,
        surround_score=1.0,
    )


def _moving_square(x: int) -> np.ndarray:
    image = np.zeros((64, 64), dtype=np.float32)
    image[22:42, x : x + 12] = 1.0
    return image


def _texture_frame() -> np.ndarray:
    rng = np.random.default_rng(7)
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


def _video_frame(index: int, image: np.ndarray, *, sequence: int | None = None) -> VideoFrame:
    return VideoFrame(
        topic="/camera/test/image/jpeg",
        index=index,
        log_time_ns=index,
        publish_time_ns=index,
        sequence=index if sequence is None else sequence,
        timestamp_ns=index,
        frame_id="test",
        format="jpeg",
        jpeg=_jpeg_bytes(image),
    )


def _jpeg_bytes(image: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(np.uint8(np.clip(image, 0.0, 1.0) * 255)).save(buffer, format="JPEG")
    return buffer.getvalue()
