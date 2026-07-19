from __future__ import annotations

import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image

from episode_qc.mcap_video import VideoFrame, iter_video_frames
from episode_qc.stale_region import (
    _component_pixel_indices,
    _connected_components,
    _draw_scaled_bbox,
    _safe_topic_name,
    decode_jpeg_grayscale,
)


@dataclass(frozen=True)
class FlowVerifyConfig:
    resize: tuple[int, int] = (160, 90)
    block_size: int = 8
    search_radius: int = 8
    window_frames: int = 8
    threshold: float = 0.72
    max_regions_per_frame: int = 3
    min_area_ratio: float = 0.01
    max_area_ratio: float = 0.35
    min_component_width: int = 16
    min_component_height: int = 12
    export_dir: Path | None = None


def verify_mcap_flow_window(
    mcap_path: str | Path,
    *,
    topic: str,
    center_frame: int | None = None,
    elapsed_sec: float | None = None,
    config: FlowVerifyConfig | None = None,
) -> dict[str, object]:
    active_config = config or FlowVerifyConfig()
    frames = list(iter_video_frames(mcap_path, topics=[topic]))
    if not frames:
        return _empty_payload(mcap_path, topic, active_config)
    if center_frame is None:
        if elapsed_sec is None:
            raise ValueError("center_frame or elapsed_sec is required")
        center_frame = _frame_index_for_elapsed(frames, elapsed_sec)

    start_index = max(0, center_frame - active_config.window_frames)
    end_index = center_frame + active_config.window_frames
    selected = [frame for frame in frames if start_index <= frame.index <= end_index]
    return verify_frames_flow(
        selected,
        mcap_path=mcap_path,
        topic=topic,
        config=active_config,
        base_log_time_ns=frames[0].log_time_ns,
    )


def verify_frames_flow(
    frames: Iterable[VideoFrame],
    *,
    mcap_path: str | Path | None = None,
    topic: str,
    config: FlowVerifyConfig | None = None,
    base_log_time_ns: int | None = None,
) -> dict[str, object]:
    active_config = config or FlowVerifyConfig()
    decoded: list[tuple[VideoFrame, np.ndarray]] = []
    decode_errors = 0
    for frame in frames:
        try:
            decoded.append((frame, decode_jpeg_grayscale(frame.jpeg, active_config.resize)))
        except Exception:
            decode_errors += 1

    candidates: list[dict[str, object]] = []
    if active_config.export_dir is not None:
        active_config.export_dir.mkdir(parents=True, exist_ok=True)
    if base_log_time_ns is None and decoded:
        base_log_time_ns = decoded[0][0].log_time_ns

    for previous, current, next_frame in zip(decoded, decoded[1:], decoded[2:]):
        frame, image = current
        regions = _score_flow_triplet(
            previous[1],
            image,
            next_frame[1],
            config=active_config,
        )
        for region_index, region in enumerate(regions[: active_config.max_regions_per_frame]):
            if region["score"] < active_config.threshold:
                continue
            snapshot_path = _export_flow_snapshot(active_config.export_dir, frame, region_index, region, active_config.resize)
            candidates.append(
                {
                    "detector": "flow_block_residual",
                    "topic": frame.topic,
                    "frame_index": frame.index,
                    "region_index": region_index,
                    "log_time_ns": frame.log_time_ns,
                    "publish_time_ns": frame.publish_time_ns,
                    "sequence": frame.sequence,
                    "timestamp_ns": frame.timestamp_ns,
                    "elapsed_sec": _elapsed_sec(base_log_time_ns, frame),
                    "snapshot_path": snapshot_path,
                    **region,
                }
            )

    events = _flow_events(candidates)
    return {
        "mcap_path": str(mcap_path) if mcap_path is not None else None,
        "topic": topic,
        "config": {
            "resize": list(active_config.resize),
            "block_size": active_config.block_size,
            "search_radius": active_config.search_radius,
            "window_frames": active_config.window_frames,
            "threshold": active_config.threshold,
            "min_area_ratio": active_config.min_area_ratio,
            "max_area_ratio": active_config.max_area_ratio,
        },
        "summary": {
            "frames": len(decoded) + decode_errors,
            "decoded_frames": len(decoded),
            "decode_errors": decode_errors,
            "candidates": len(candidates),
            "events": len(events),
        },
        "candidates": candidates,
        "events": events,
    }


def _score_flow_triplet(
    previous: np.ndarray,
    current: np.ndarray,
    next_frame: np.ndarray,
    *,
    config: FlowVerifyConfig,
) -> list[dict[str, object]]:
    previous_flow, previous_error = _block_match_flow(previous, current, config)
    next_flow, next_error = _block_match_flow(next_frame, current, config)
    forward_backward_mismatch = np.linalg.norm(previous_flow + next_flow, axis=2)
    previous_local_residual = _local_flow_residual(previous_flow)
    next_local_residual = _local_flow_residual(next_flow)
    local_residual = np.maximum(previous_local_residual, next_local_residual)
    match_error = np.minimum(previous_error, next_error)
    temporal_error = np.maximum(previous_error, next_error)

    flow_score = _normalize_map(local_residual, high_percentile=95.0)
    mismatch_score = _normalize_map(forward_backward_mismatch, high_percentile=95.0)
    match_score = _normalize_map(match_error, high_percentile=95.0)
    temporal_score = _normalize_map(temporal_error, high_percentile=95.0)
    score_map = (
        (0.40 * flow_score)
        + (0.25 * mismatch_score)
        + (0.20 * match_score)
        + (0.15 * temporal_score)
    )
    mask = score_map >= max(0.35, config.threshold * 0.65)

    total_pixels = current.size
    regions: list[dict[str, object]] = []
    for component in _connected_components(mask):
        ys, xs = _component_pixel_indices(component, config.block_size, current.shape)
        if len(xs) == 0:
            continue
        x_min = int(xs.min())
        x_max = int(xs.max())
        y_min = int(ys.min())
        y_max = int(ys.max())
        width = x_max - x_min + 1
        height = y_max - y_min + 1
        area_pixels = len(xs)
        area_ratio = area_pixels / total_pixels

        if area_ratio < config.min_area_ratio:
            continue
        if area_ratio > config.max_area_ratio:
            continue
        if width < config.min_component_width or height < config.min_component_height:
            continue

        block_ys, block_xs = component
        region_score = float(np.mean(score_map[block_ys, block_xs]))
        peak_score = float(np.max(score_map[block_ys, block_xs]))
        score = min(1.0, (0.65 * region_score) + (0.35 * peak_score))
        regions.append(
            {
                "score": score,
                "bbox": [x_min, y_min, width, height],
                "area_pixels": area_pixels,
                "area_ratio": area_ratio,
                "flow_residual": float(np.mean(local_residual[block_ys, block_xs])),
                "flow_mismatch": float(np.mean(forward_backward_mismatch[block_ys, block_xs])),
                "match_error": float(np.mean(match_error[block_ys, block_xs])),
                "temporal_error": float(np.mean(temporal_error[block_ys, block_xs])),
            }
        )

    return sorted(regions, key=lambda item: float(item["score"]), reverse=True)


def _block_match_flow(
    reference: np.ndarray,
    current: np.ndarray,
    config: FlowVerifyConfig,
) -> tuple[np.ndarray, np.ndarray]:
    block_size = max(1, config.block_size)
    search_radius = max(0, config.search_radius)
    height, width = current.shape
    rows = height // block_size
    cols = width // block_size
    flows = np.zeros((rows, cols, 2), dtype=np.float32)
    errors = np.zeros((rows, cols), dtype=np.float32)

    for row in range(rows):
        y = row * block_size
        for col in range(cols):
            x = col * block_size
            tile = current[y : y + block_size, x : x + block_size]
            best_error = float("inf")
            best_shift = (0, 0)
            for shift_y in range(-search_radius, search_radius + 1):
                ref_y = y + shift_y
                if ref_y < 0 or ref_y + block_size > height:
                    continue
                for shift_x in range(-search_radius, search_radius + 1):
                    ref_x = x + shift_x
                    if ref_x < 0 or ref_x + block_size > width:
                        continue
                    error = float(np.mean(np.abs(tile - reference[ref_y : ref_y + block_size, ref_x : ref_x + block_size])))
                    if error < best_error:
                        best_error = error
                        best_shift = (shift_y, shift_x)
            flows[row, col] = best_shift
            errors[row, col] = 0.0 if best_error == float("inf") else best_error

    return flows, errors


def _local_flow_residual(flow: np.ndarray) -> np.ndarray:
    rows, cols, _channels = flow.shape
    residual = np.zeros((rows, cols), dtype=np.float32)
    for row in range(rows):
        row_start = max(0, row - 1)
        row_end = min(rows, row + 2)
        for col in range(cols):
            col_start = max(0, col - 1)
            col_end = min(cols, col + 2)
            neighborhood = flow[row_start:row_end, col_start:col_end].reshape(-1, 2)
            median_flow = np.median(neighborhood, axis=0)
            residual[row, col] = float(np.linalg.norm(flow[row, col] - median_flow))
    return residual


def _normalize_map(values: np.ndarray, *, high_percentile: float) -> np.ndarray:
    high = float(np.percentile(values, high_percentile))
    if high <= 1e-6:
        return np.zeros_like(values, dtype=np.float32)
    return np.clip(values / high, 0.0, 1.0).astype(np.float32)


def _frame_index_for_elapsed(frames: list[VideoFrame], elapsed_sec: float) -> int:
    base_time_ns = frames[0].log_time_ns
    target_time_ns = base_time_ns + int(elapsed_sec * 1_000_000_000)
    nearest = min(frames, key=lambda frame: abs(frame.log_time_ns - target_time_ns))
    return nearest.index


def _elapsed_sec(base_log_time_ns: int | None, frame: VideoFrame) -> float:
    if base_log_time_ns is None:
        return 0.0
    return (frame.log_time_ns - base_log_time_ns) / 1_000_000_000


def _flow_events(candidates: list[dict[str, object]]) -> list[dict[str, object]]:
    if not candidates:
        return []
    ordered = sorted(candidates, key=lambda item: int(item["frame_index"]))
    events: list[dict[str, object]] = []
    group: list[dict[str, object]] = [ordered[0]]
    for candidate in ordered[1:]:
        previous = group[-1]
        if int(candidate["frame_index"]) <= int(previous["frame_index"]) + 1:
            group.append(candidate)
            continue
        events.append(_flow_event(group))
        group = [candidate]
    events.append(_flow_event(group))
    return events


def _flow_event(group: list[dict[str, object]]) -> dict[str, object]:
    representative = max(group, key=lambda item: float(item["score"]))
    frames = [int(item["frame_index"]) for item in group]
    scores = [float(item["score"]) for item in group]
    event = dict(representative)
    event.update(
        {
            "event_frame_start": min(frames),
            "event_frame_end": max(frames),
            "event_frame_count": max(frames) - min(frames) + 1,
            "event_candidate_count": len(group),
            "event_max_score": max(scores),
            "event_mean_score": sum(scores) / len(scores),
        }
    )
    return event


def _export_flow_snapshot(
    export_dir: Path | None,
    frame: VideoFrame,
    region_index: int,
    region: dict[str, object],
    analysis_size: tuple[int, int],
) -> str | None:
    if export_dir is None:
        return None
    topic_dir = export_dir / _safe_topic_name(frame.topic)
    topic_dir.mkdir(parents=True, exist_ok=True)
    path = topic_dir / f"frame_{frame.index:06d}_flow_region_{region_index:02d}_score_{float(region['score']):.3f}.jpg"
    with Image.open(io.BytesIO(frame.jpeg)) as image:
        preview = image.convert("RGB")
        bbox = tuple(int(value) for value in region["bbox"])
        _draw_scaled_bbox(preview, bbox, analysis_size)
        preview.save(path, format="JPEG", quality=95)
    return str(path)


def _empty_payload(mcap_path: str | Path, topic: str, config: FlowVerifyConfig) -> dict[str, object]:
    return {
        "mcap_path": str(mcap_path),
        "topic": topic,
        "config": {
            "resize": list(config.resize),
            "block_size": config.block_size,
            "search_radius": config.search_radius,
            "window_frames": config.window_frames,
            "threshold": config.threshold,
        },
        "summary": {"frames": 0, "decoded_frames": 0, "decode_errors": 0, "candidates": 0, "events": 0},
        "candidates": [],
        "events": [],
    }


def payload_to_json(payload: dict[str, object], *, indent: int = 2) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=indent)
