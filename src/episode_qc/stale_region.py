from __future__ import annotations

import io
import json
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from pathlib import Path
from statistics import median
from typing import Iterable

import numpy as np
from PIL import Image

from episode_qc.mcap_video import VideoFrame, iter_video_frames


@dataclass(frozen=True)
class StaleRegionConfig:
    detectors: tuple[str, ...] = ("stale_region", "localized_corruption", "temporal_tearing")
    threshold: float = 0.72
    tile_size: int = 8
    history_size: int = 3
    min_change: float = 0.08
    max_stale_delta: float = 0.035
    min_temporal_contrast: float = 0.05
    min_global_shift: float = 1.0
    min_corruption_change: float = 0.02
    min_texture_increase: float = 0.014
    corruption_score_scale: float = 2.0
    max_persistence_frames: int = 12
    min_persistence_score: float = 0.45
    gap_scan_window: int | None = None
    min_gap_ratio: float = 1.5
    min_motion_residual: float = 0.018
    border_motion_residual_multiplier: float = 1.5
    local_match_radius: int = 8
    tearing_gap_scan_ratio: float = 1.35
    tearing_gap_scan_window: int = 5
    tearing_cluster_gap_ratio: float = 1.25
    tearing_cluster_gap_count: int = 2
    tearing_cluster_window: int = 8
    min_tearing_seed_score: float = 0.58
    min_tearing_event_frames: int = 3
    min_tearing_motion_residual: float = 0.04
    min_tearing_localized_change: float = 0.12
    min_tearing_texture_increase: float = 0.065
    min_tearing_area_ratio: float = 0.012
    max_tearing_area_ratio: float = 0.35
    tearing_track_iou: float = 0.22
    max_tearing_tracks: int = 8
    min_spatial_tearing_texture: float = 0.065
    min_spatial_tearing_jaggedness: float = 0.045
    min_spatial_tearing_texture_contrast: float = 0.018
    min_spatial_tearing_jaggedness_contrast: float = 0.018
    min_spatial_tearing_std: float = 0.10
    max_spatial_tearing_temporal_change: float = 0.018
    min_spatial_tearing_event_frames: int = 3
    spatial_tearing_border_margin_ratio: float = 0.20
    spatial_tearing_track_iou: float = 0.18
    min_area_ratio: float = 0.01
    max_area_ratio: float = 0.35
    min_component_width: int = 16
    min_component_height: int = 12
    max_aspect_ratio: float = 4.0
    min_rectangularity: float = 0.55
    surround_margin: int = 4
    min_surround_change: float = 0.025
    min_surround_contrast: float = 0.015
    max_regions_per_frame: int = 3
    resize: tuple[int, int] = (160, 90)
    export_dir: Path | None = None


@dataclass(frozen=True)
class StaleRegionScore:
    detector: str
    score: float
    bbox: tuple[int, int, int, int]
    area_pixels: int
    area_ratio: float
    rectangularity: float
    stale_delta: float
    compensated_change: float
    future_change: float
    previous_to_next_change: float
    temporal_contrast: float
    surround_change: float
    surround_contrast: float
    reference_lag: int
    localized_change: float
    texture_increase: float
    frame_gap_ratio: float
    sequence_gap: int
    motion_residual: float
    event_start_frame: int | None
    event_frame_offset: int
    similarity_score: float
    change_score: float
    contrast_score: float
    surround_score: float


@dataclass(frozen=True)
class StaleRegionCandidate:
    detector: str
    topic: str
    frame_index: int
    region_index: int
    log_time_ns: int
    publish_time_ns: int
    sequence: int
    timestamp_ns: int | None
    score: float
    bbox: tuple[int, int, int, int]
    area_pixels: int
    area_ratio: float
    rectangularity: float
    stale_delta: float
    compensated_change: float
    future_change: float
    previous_to_next_change: float
    temporal_contrast: float
    surround_change: float
    surround_contrast: float
    reference_lag: int
    localized_change: float
    texture_increase: float
    frame_gap_ratio: float
    sequence_gap: int
    motion_residual: float
    event_start_frame: int | None
    event_frame_offset: int
    snapshot_path: str | None = None


@dataclass
class TopicScanStats:
    frames: int = 0
    decoded_frames: int = 0
    decode_errors: int = 0
    candidates: int = 0


@dataclass(frozen=True)
class ScanResult:
    candidates: list[StaleRegionCandidate]
    topics: dict[str, TopicScanStats]
    config: StaleRegionConfig

    def to_dict(self) -> dict[str, object]:
        candidate_dicts = [candidate_to_dict(candidate) for candidate in self.candidates]
        event_dicts = candidate_events_to_dicts(candidate_dicts)
        return {
            "config": {
                "detectors": list(self.config.detectors),
                "threshold": self.config.threshold,
                "tile_size": self.config.tile_size,
                "history_size": self.config.history_size,
                "min_change": self.config.min_change,
                "max_stale_delta": self.config.max_stale_delta,
                "min_temporal_contrast": self.config.min_temporal_contrast,
                "min_global_shift": self.config.min_global_shift,
                "min_corruption_change": self.config.min_corruption_change,
                "min_texture_increase": self.config.min_texture_increase,
                "corruption_score_scale": self.config.corruption_score_scale,
                "max_persistence_frames": self.config.max_persistence_frames,
                "min_persistence_score": self.config.min_persistence_score,
                "gap_scan_window": self.config.gap_scan_window,
                "min_gap_ratio": self.config.min_gap_ratio,
                "min_motion_residual": self.config.min_motion_residual,
                "border_motion_residual_multiplier": self.config.border_motion_residual_multiplier,
                "local_match_radius": self.config.local_match_radius,
                "tearing_gap_scan_ratio": self.config.tearing_gap_scan_ratio,
                "tearing_gap_scan_window": self.config.tearing_gap_scan_window,
                "tearing_cluster_gap_ratio": self.config.tearing_cluster_gap_ratio,
                "tearing_cluster_gap_count": self.config.tearing_cluster_gap_count,
                "tearing_cluster_window": self.config.tearing_cluster_window,
                "min_tearing_seed_score": self.config.min_tearing_seed_score,
                "min_tearing_event_frames": self.config.min_tearing_event_frames,
                "min_tearing_motion_residual": self.config.min_tearing_motion_residual,
                "min_tearing_localized_change": self.config.min_tearing_localized_change,
                "min_tearing_texture_increase": self.config.min_tearing_texture_increase,
                "min_tearing_area_ratio": self.config.min_tearing_area_ratio,
                "max_tearing_area_ratio": self.config.max_tearing_area_ratio,
                "tearing_track_iou": self.config.tearing_track_iou,
                "max_tearing_tracks": self.config.max_tearing_tracks,
                "min_spatial_tearing_texture": self.config.min_spatial_tearing_texture,
                "min_spatial_tearing_jaggedness": self.config.min_spatial_tearing_jaggedness,
                "min_spatial_tearing_texture_contrast": self.config.min_spatial_tearing_texture_contrast,
                "min_spatial_tearing_jaggedness_contrast": self.config.min_spatial_tearing_jaggedness_contrast,
                "min_spatial_tearing_std": self.config.min_spatial_tearing_std,
                "max_spatial_tearing_temporal_change": self.config.max_spatial_tearing_temporal_change,
                "min_spatial_tearing_event_frames": self.config.min_spatial_tearing_event_frames,
                "spatial_tearing_border_margin_ratio": self.config.spatial_tearing_border_margin_ratio,
                "spatial_tearing_track_iou": self.config.spatial_tearing_track_iou,
                "min_area_ratio": self.config.min_area_ratio,
                "max_area_ratio": self.config.max_area_ratio,
                "min_component_width": self.config.min_component_width,
                "min_component_height": self.config.min_component_height,
                "max_aspect_ratio": self.config.max_aspect_ratio,
                "min_rectangularity": self.config.min_rectangularity,
                "surround_margin": self.config.surround_margin,
                "min_surround_change": self.config.min_surround_change,
                "min_surround_contrast": self.config.min_surround_contrast,
                "max_regions_per_frame": self.config.max_regions_per_frame,
                "resize": list(self.config.resize),
                "export_dir": str(self.config.export_dir) if self.config.export_dir else None,
            },
            "summary": {
                "topics": len(self.topics),
                "frames": sum(stats.frames for stats in self.topics.values()),
                "decoded_frames": sum(stats.decoded_frames for stats in self.topics.values()),
                "decode_errors": sum(stats.decode_errors for stats in self.topics.values()),
                "candidates": len(self.candidates),
                "events": len(event_dicts),
            },
            "topics": {
                topic: {
                    "frames": stats.frames,
                    "decoded_frames": stats.decoded_frames,
                    "decode_errors": stats.decode_errors,
                    "candidates": stats.candidates,
                }
                for topic, stats in sorted(self.topics.items())
            },
            "candidates": candidate_dicts,
            "events": event_dicts,
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


@dataclass(frozen=True)
class _DecodedFrame:
    frame: VideoFrame
    image: np.ndarray
    interval_ns: int | None
    frame_gap_ratio: float
    sequence_gap: int


@dataclass
class _ActiveCorruptionTrack:
    bbox: tuple[int, int, int, int]
    start_frame_index: int
    last_frame_index: int


@dataclass(frozen=True)
class _TearingTrack:
    bbox: tuple[int, int, int, int]
    start_frame_index: int
    last_frame_index: int
    hit_count: int
    max_score: float
    has_timing_jitter: bool


def scan_mcap_for_stale_regions(
    mcap_path: str | Path,
    *,
    topics: Iterable[str] | None = None,
    config: StaleRegionConfig | None = None,
    max_frames_per_topic: int | None = None,
) -> ScanResult:
    active_config = config or StaleRegionConfig()
    if _should_split_spatial_scan(active_config):
        results: list[ScanResult] = []
        temporal_detectors = tuple(
            detector for detector in active_config.detectors if detector != "spatial_tearing"
        )
        if temporal_detectors:
            temporal_config = replace(active_config, detectors=temporal_detectors)
            results.append(
                _scan_mcap_once(
                    mcap_path,
                    topics=topics,
                    config=temporal_config,
                    max_frames_per_topic=max_frames_per_topic,
                )
            )
        spatial_config = replace(active_config, detectors=("spatial_tearing",), gap_scan_window=None)
        results.append(
            _scan_mcap_once(
                mcap_path,
                topics=topics,
                config=spatial_config,
                max_frames_per_topic=max_frames_per_topic,
            )
        )
        return _merge_scan_results(results, active_config)

    return _scan_mcap_once(
        mcap_path,
        topics=topics,
        config=active_config,
        max_frames_per_topic=max_frames_per_topic,
    )


def _scan_mcap_once(
    mcap_path: str | Path,
    *,
    topics: Iterable[str] | None,
    config: StaleRegionConfig,
    max_frames_per_topic: int | None,
) -> ScanResult:
    frames = iter_video_frames(
        mcap_path,
        topics=topics,
        max_frames_per_topic=max_frames_per_topic,
    )
    if config.gap_scan_window is not None and config.gap_scan_window > 0:
        frames = _iter_gap_window_frames(frames, config)
    return scan_frames_for_stale_regions(frames, config=config)


def _should_split_spatial_scan(config: StaleRegionConfig) -> bool:
    return False


def _merge_scan_results(results: list[ScanResult], config: StaleRegionConfig) -> ScanResult:
    candidates = sorted(
        (candidate for result in results for candidate in result.candidates),
        key=lambda candidate: (candidate.topic, candidate.frame_index, -candidate.score, candidate.detector),
    )
    topics: dict[str, TopicScanStats] = {}
    for result in results:
        for topic, stats in result.topics.items():
            merged = topics.setdefault(topic, TopicScanStats())
            merged.frames += stats.frames
            merged.decoded_frames += stats.decoded_frames
            merged.decode_errors += stats.decode_errors
            merged.candidates += stats.candidates
    return ScanResult(candidates=candidates, topics=topics, config=config)


def _iter_gap_window_frames(
    frames: Iterable[VideoFrame],
    config: StaleRegionConfig,
) -> Iterable[VideoFrame]:
    recent_count = max(2, config.history_size + 1, config.min_tearing_event_frames + 1)
    recent_frames: dict[str, deque[VideoFrame]] = defaultdict(lambda: deque(maxlen=recent_count))
    interval_history: dict[str, deque[int]] = defaultdict(lambda: deque(maxlen=30))
    moderate_gap_history: dict[str, deque[bool]] = defaultdict(
        lambda: deque(maxlen=max(1, config.tearing_cluster_window))
    )
    active_until: dict[str, int] = defaultdict(lambda: -1)
    last_frames: dict[str, VideoFrame] = {}
    emitted: set[tuple[str, int]] = set()

    for frame in frames:
        previous = last_frames.get(frame.topic)
        interval_ns = _frame_interval_ns(previous, frame) if previous else None
        gap_ratio = _frame_gap_ratio(interval_ns, interval_history[frame.topic])
        sequence_gap = frame.sequence - previous.sequence if previous else 1
        moderate_gap = previous is not None and gap_ratio >= config.tearing_cluster_gap_ratio
        clustered_moderate_gap = (
            moderate_gap
            and sum(moderate_gap_history[frame.topic]) >= max(0, config.tearing_cluster_gap_count - 1)
        )
        annotated_frame = replace(
            frame,
            source_interval_ns=interval_ns,
            source_frame_gap_ratio=gap_ratio,
            source_sequence_gap=sequence_gap,
        )
        hard_gap = previous is not None and (sequence_gap > 1 or gap_ratio >= config.min_gap_ratio)
        soft_tearing_gap = (
            previous is not None
            and (
                _detector_enabled(config, "temporal_tearing")
                or _detector_enabled(config, "spatial_tearing")
            )
            and (
                gap_ratio >= config.tearing_gap_scan_ratio
                or (_detector_enabled(config, "spatial_tearing") and clustered_moderate_gap)
            )
        )

        if hard_gap or soft_tearing_gap:
            window = config.gap_scan_window if hard_gap else config.tearing_gap_scan_window
            active_until[frame.topic] = max(
                active_until[frame.topic],
                frame.index + window + 1,
            )
            for recent in recent_frames[frame.topic]:
                key = (recent.topic, recent.index)
                if key not in emitted:
                    emitted.add(key)
                    yield recent

        if frame.index <= active_until[frame.topic]:
            key = (frame.topic, frame.index)
            if key not in emitted:
                emitted.add(key)
                yield annotated_frame

        recent_frames[frame.topic].append(annotated_frame)
        if interval_ns is not None:
            interval_history[frame.topic].append(interval_ns)
            moderate_gap_history[frame.topic].append(moderate_gap)
        last_frames[frame.topic] = annotated_frame


def scan_frames_for_stale_regions(
    frames: Iterable[VideoFrame],
    *,
    config: StaleRegionConfig | None = None,
) -> ScanResult:
    active_config = config or StaleRegionConfig()
    window_size = max(1, active_config.history_size) + 2
    windows: dict[str, deque[_DecodedFrame]] = defaultdict(lambda: deque(maxlen=window_size))
    last_decoded_frames: dict[str, _DecodedFrame] = {}
    interval_history: dict[str, deque[int]] = defaultdict(lambda: deque(maxlen=30))
    active_tracks: dict[str, list[_ActiveCorruptionTrack]] = defaultdict(list)
    tearing_tracks: dict[str, list[_TearingTrack]] = defaultdict(list)
    spatial_tearing_tracks: dict[str, list[_TearingTrack]] = defaultdict(list)
    stats: dict[str, TopicScanStats] = defaultdict(TopicScanStats)
    candidates: list[StaleRegionCandidate] = []

    if active_config.export_dir is not None:
        active_config.export_dir.mkdir(parents=True, exist_ok=True)

    for frame in frames:
        topic_stats = stats[frame.topic]
        topic_stats.frames += 1

        try:
            image = decode_jpeg_grayscale(frame.jpeg, active_config.resize)
        except Exception:
            topic_stats.decode_errors += 1
            continue

        topic_stats.decoded_frames += 1
        previous_decoded = last_decoded_frames.get(frame.topic)
        decoded_interval_ns = _frame_interval_ns(previous_decoded.frame, frame) if previous_decoded else None
        interval_ns = frame.source_interval_ns if frame.source_interval_ns is not None else decoded_interval_ns
        gap_ratio = (
            frame.source_frame_gap_ratio
            if frame.source_frame_gap_ratio is not None
            else _frame_gap_ratio(interval_ns, interval_history[frame.topic])
        )
        sequence_gap = (
            frame.source_sequence_gap
            if frame.source_sequence_gap is not None
            else (frame.sequence - previous_decoded.frame.sequence if previous_decoded else 1)
        )
        decoded = _DecodedFrame(
            frame=frame,
            image=image,
            interval_ns=interval_ns,
            frame_gap_ratio=gap_ratio,
            sequence_gap=sequence_gap,
        )

        window = windows[frame.topic]
        window.append(decoded)
        last_decoded_frames[frame.topic] = decoded
        if interval_ns is not None:
            interval_history[frame.topic].append(interval_ns)
        if len(window) < 3:
            continue

        current = window[-2]
        next_frame = window[-1]
        previous_frames = [decoded.image for decoded in list(window)[:-2]]
        regions = score_window(
            previous_frames,
            current.image,
            next_frame.image,
            config=active_config,
            frame_gap_ratio=current.frame_gap_ratio,
            next_frame_gap_ratio=next_frame.frame_gap_ratio,
            sequence_gap=current.sequence_gap,
            next_sequence_gap=next_frame.sequence_gap,
        )
        promoted_tearing_regions, tearing_tracks[current.frame.topic] = _update_tearing_tracks(
            tearing_tracks[current.frame.topic],
            regions,
            current.frame.index,
            active_config,
        )
        promoted_spatial_regions, spatial_tearing_tracks[current.frame.topic] = _update_spatial_tearing_tracks(
            spatial_tearing_tracks[current.frame.topic],
            regions,
            current.frame.index,
            active_config,
        )
        track_regions = _score_active_corruption_tracks(
            active_tracks[current.frame.topic],
            previous_frames[-1],
            current.image,
            next_frame.image,
            config=active_config,
        )
        regions = [
            region
            for region in regions
            if region.detector not in {"temporal_tearing", "spatial_tearing"}
        ]
        regions.extend(promoted_tearing_regions)
        regions.extend(promoted_spatial_regions)
        regions.extend(track_regions)
        regions.sort(key=lambda region: region.score, reverse=True)
        active_tracks[current.frame.topic] = _update_active_corruption_tracks(
            active_tracks[current.frame.topic],
            track_regions,
            current.frame.index,
            active_config,
        )

        emitted_regions: list[StaleRegionScore] = []
        for region_index, region in enumerate(regions[: active_config.max_regions_per_frame]):
            if region.detector in {"temporal_tearing", "spatial_tearing"} and region.event_start_frame is None:
                continue
            if region.score < active_config.threshold:
                continue

            event_start_frame = region.event_start_frame
            if event_start_frame is None and region.detector in {
                "localized_corruption",
                "temporal_tearing",
                "spatial_tearing",
            }:
                event_start_frame = current.frame.index

            snapshot_path = _export_snapshot(
                active_config.export_dir,
                current.frame,
                region_index,
                region,
                active_config.resize,
            )
            candidate = StaleRegionCandidate(
                detector=region.detector,
                topic=current.frame.topic,
                frame_index=current.frame.index,
                region_index=region_index,
                log_time_ns=current.frame.log_time_ns,
                publish_time_ns=current.frame.publish_time_ns,
                sequence=current.frame.sequence,
                timestamp_ns=current.frame.timestamp_ns,
                score=region.score,
                bbox=region.bbox,
                area_pixels=region.area_pixels,
                area_ratio=region.area_ratio,
                rectangularity=region.rectangularity,
                stale_delta=region.stale_delta,
                compensated_change=region.compensated_change,
                future_change=region.future_change,
                previous_to_next_change=region.previous_to_next_change,
                temporal_contrast=region.temporal_contrast,
                surround_change=region.surround_change,
                surround_contrast=region.surround_contrast,
                reference_lag=region.reference_lag,
                localized_change=region.localized_change,
                texture_increase=region.texture_increase,
                frame_gap_ratio=region.frame_gap_ratio,
                sequence_gap=region.sequence_gap,
                motion_residual=region.motion_residual,
                event_start_frame=event_start_frame,
                event_frame_offset=region.event_frame_offset,
                snapshot_path=snapshot_path,
            )
            candidates.append(candidate)
            topic_stats.candidates += 1
            emitted_regions.append(region)

        active_tracks[current.frame.topic] = _add_new_corruption_tracks(
            active_tracks[current.frame.topic],
            emitted_regions,
            current.frame.index,
            active_config,
        )

    return ScanResult(candidates=candidates, topics=dict(stats), config=active_config)


def score_triplet(
    previous: np.ndarray,
    current: np.ndarray,
    next_frame: np.ndarray,
    *,
    config: StaleRegionConfig | None = None,
) -> list[StaleRegionScore]:
    return score_window([previous], current, next_frame, config=config)


def score_window(
    previous_frames: Iterable[np.ndarray],
    current: np.ndarray,
    next_frame: np.ndarray,
    *,
    config: StaleRegionConfig | None = None,
    frame_gap_ratio: float = 1.0,
    next_frame_gap_ratio: float = 1.0,
    sequence_gap: int = 1,
    next_sequence_gap: int = 1,
) -> list[StaleRegionScore]:
    active_config = config or StaleRegionConfig()
    current_next_delta = np.abs(next_frame - current)
    previous_list = list(previous_frames)[-max(1, active_config.history_size) :]
    if not previous_list:
        return []

    regions: list[StaleRegionScore] = []
    if _detector_enabled(active_config, "stale_region"):
        regions.extend(
            _score_stale_regions(
                previous_list,
                current,
                next_frame,
                current_next_delta,
                config=active_config,
                frame_gap_ratio=frame_gap_ratio,
                sequence_gap=sequence_gap,
            )
        )

    localized_regions: list[StaleRegionScore] = []
    if _should_run_localized_corruption(active_config, frame_gap_ratio, sequence_gap):
        localized_regions = _score_localized_corruption(
                previous_list[-1],
                current,
                next_frame,
                config=active_config,
                frame_gap_ratio=frame_gap_ratio,
                sequence_gap=sequence_gap,
            )
        regions.extend(localized_regions)

    if _detector_enabled(active_config, "temporal_tearing"):
        regions.extend(
            _score_temporal_tearing(
                previous_list[-1],
                current,
                next_frame,
                localized_regions=localized_regions,
                config=active_config,
                frame_gap_ratio=frame_gap_ratio,
                next_frame_gap_ratio=next_frame_gap_ratio,
                sequence_gap=sequence_gap,
                next_sequence_gap=next_sequence_gap,
            )
        )

    if _detector_enabled(active_config, "spatial_tearing"):
        regions.extend(
            _score_spatial_tearing(
                previous_list[-1],
                current,
                next_frame,
                config=active_config,
                frame_gap_ratio=frame_gap_ratio,
                sequence_gap=sequence_gap,
            )
        )

    return sorted(regions, key=lambda region: region.score, reverse=True)


def _score_stale_regions(
    previous_list: list[np.ndarray],
    current: np.ndarray,
    next_frame: np.ndarray,
    current_next_delta: np.ndarray,
    *,
    config: StaleRegionConfig,
    frame_gap_ratio: float,
    sequence_gap: int,
) -> list[StaleRegionScore]:
    prev_current_stack = np.stack([np.abs(current - previous) for previous in previous_list])
    prev_next_stack = np.stack([np.abs(next_frame - previous) for previous in previous_list])
    compensated_stack = np.stack([_motion_compensated_delta(previous, current, config) for previous in previous_list])
    best_reference_index = np.argmin(prev_current_stack, axis=0)
    prev_current_delta = np.take_along_axis(prev_current_stack, best_reference_index[None, :, :], axis=0)[0]
    prev_next_delta = np.take_along_axis(prev_next_stack, best_reference_index[None, :, :], axis=0)[0]
    compensated_delta = np.take_along_axis(compensated_stack, best_reference_index[None, :, :], axis=0)[0]
    temporal_contrast = compensated_delta - prev_current_delta

    mask = _tile_candidate_mask(
        prev_current_delta,
        compensated_delta,
        temporal_contrast,
        config,
    )

    total_pixels = current.size
    regions: list[StaleRegionScore] = []
    for component in _connected_components(mask):
        ys, xs = _component_pixel_indices(component, config.tile_size, current.shape)
        area_pixels = len(xs)
        area_ratio = area_pixels / total_pixels
        x_min = int(xs.min())
        x_max = int(xs.max())
        y_min = int(ys.min())
        y_max = int(ys.max())
        width = x_max - x_min + 1
        height = y_max - y_min + 1

        if area_ratio < config.min_area_ratio:
            continue
        if area_ratio > config.max_area_ratio:
            continue
        if width < config.min_component_width or height < config.min_component_height:
            continue
        aspect_ratio = max(width / height, height / width)
        if aspect_ratio > config.max_aspect_ratio:
            continue
        rectangularity = area_pixels / (width * height)
        if rectangularity < config.min_rectangularity:
            continue

        stale_delta = float(np.mean(prev_current_delta[ys, xs]))
        compensated_change = float(np.mean(compensated_delta[ys, xs]))
        future_change = float(np.mean(current_next_delta[ys, xs]))
        previous_to_next_change = float(np.mean(prev_next_delta[ys, xs]))
        contrast = float(np.mean(temporal_contrast[ys, xs]))
        reference_lag = _mode_reference_lag(best_reference_index[ys, xs], len(previous_list))
        surround_change = _surround_mean(
            prev_current_delta,
            x_min,
            y_min,
            width,
            height,
            config.surround_margin,
        )
        surround_contrast = surround_change - stale_delta

        if surround_change < config.min_surround_change:
            continue
        if surround_contrast < config.min_surround_contrast:
            continue

        similarity_score = 1.0 - min(1.0, stale_delta / max(config.max_stale_delta, 1e-6))
        change_score = min(1.0, compensated_change / max(config.min_change * 2.0, 1e-6))
        contrast_score = min(1.0, contrast / max(config.min_temporal_contrast * 2.0, 1e-6))
        surround_score = min(1.0, surround_contrast / max(config.min_surround_contrast * 3.0, 1e-6))
        score = (
            (0.35 * similarity_score)
            + (0.20 * change_score)
            + (0.25 * contrast_score)
            + (0.20 * surround_score)
        )

        regions.append(
            StaleRegionScore(
                detector="stale_region",
                score=score,
                bbox=(x_min, y_min, width, height),
                area_pixels=area_pixels,
                area_ratio=area_ratio,
                rectangularity=rectangularity,
                stale_delta=stale_delta,
                compensated_change=compensated_change,
                future_change=future_change,
                previous_to_next_change=previous_to_next_change,
                temporal_contrast=contrast,
                surround_change=surround_change,
                surround_contrast=surround_contrast,
                reference_lag=reference_lag,
                localized_change=future_change,
                texture_increase=0.0,
                frame_gap_ratio=frame_gap_ratio,
                sequence_gap=sequence_gap,
                motion_residual=0.0,
                event_start_frame=None,
                event_frame_offset=0,
                similarity_score=similarity_score,
                change_score=change_score,
                contrast_score=contrast_score,
                surround_score=surround_score,
            )
        )

    return sorted(regions, key=lambda region: region.score, reverse=True)


def _score_localized_corruption(
    previous: np.ndarray,
    current: np.ndarray,
    next_frame: np.ndarray,
    *,
    config: StaleRegionConfig,
    frame_gap_ratio: float,
    sequence_gap: int,
) -> list[StaleRegionScore]:
    pair_delta = np.maximum(np.abs(current - previous), np.abs(next_frame - current))
    texture_increase = np.maximum(
        0.0,
        _laplacian_magnitude(current)
        - np.minimum(_laplacian_magnitude(previous), _laplacian_magnitude(next_frame)),
    )
    gap_boost = _localized_corruption_gap_boost(frame_gap_ratio, sequence_gap)
    mask = _localized_corruption_mask(pair_delta, texture_increase, config, gap_boost)

    total_pixels = current.size
    regions: list[StaleRegionScore] = []
    for component in _connected_components(mask):
        ys, xs = _component_pixel_indices(component, config.tile_size, current.shape)
        area_pixels = len(xs)
        area_ratio = area_pixels / total_pixels
        x_min = int(xs.min())
        x_max = int(xs.max())
        y_min = int(ys.min())
        y_max = int(ys.max())
        width = x_max - x_min + 1
        height = y_max - y_min + 1

        if area_ratio < active_min_area_ratio(config):
            continue
        if area_ratio > config.max_area_ratio:
            continue
        if width < config.min_component_width or height < config.min_component_height:
            continue
        aspect_ratio = max(width / height, height / width)
        if aspect_ratio > config.max_aspect_ratio:
            continue
        rectangularity = area_pixels / (width * height)
        if rectangularity < min(config.min_rectangularity, 0.25):
            continue

        localized_change = float(np.mean(pair_delta[ys, xs]))
        texture_delta = float(np.mean(texture_increase[ys, xs]))
        surround_change = _surround_mean(
            pair_delta,
            x_min,
            y_min,
            width,
            height,
            config.surround_margin,
        )
        surround_texture = _surround_mean(
            texture_increase,
            x_min,
            y_min,
            width,
            height,
            config.surround_margin,
        )
        surround_contrast = (localized_change - surround_change) + (texture_delta - surround_texture)

        if surround_contrast < 0.002 and sequence_gap <= 1 and frame_gap_ratio < 1.5:
            continue

        motion_residual = _local_motion_residual(
            previous,
            current,
            next_frame,
            (x_min, y_min, width, height),
            config.local_match_radius,
        )
        if motion_residual < config.min_motion_residual:
            continue
        if _touches_motion_sensitive_border((x_min, y_min, width, height), current.shape) and (
            motion_residual < config.min_motion_residual * config.border_motion_residual_multiplier
        ):
            continue

        change_score = min(1.0, localized_change / _corruption_change_denominator(config))
        texture_score = min(1.0, texture_delta / _texture_increase_denominator(config))
        area_score = min(1.0, area_ratio / 0.05)
        contrast_score = min(1.0, max(surround_contrast, 0.0) / 0.025)
        raw_score = (
            (0.35 * change_score)
            + (0.35 * texture_score)
            + (0.15 * area_score)
            + (0.15 * contrast_score)
        ) * gap_boost
        score = min(1.0, raw_score / max(config.corruption_score_scale, 1e-6))

        regions.append(
            StaleRegionScore(
                detector="localized_corruption",
                score=score,
                bbox=(x_min, y_min, width, height),
                area_pixels=area_pixels,
                area_ratio=area_ratio,
                rectangularity=rectangularity,
                stale_delta=0.0,
                compensated_change=0.0,
                future_change=float(np.mean(np.abs(next_frame - current)[ys, xs])),
                previous_to_next_change=float(np.mean(np.abs(next_frame - previous)[ys, xs])),
                temporal_contrast=surround_contrast,
                surround_change=surround_change,
                surround_contrast=surround_contrast,
                reference_lag=1,
                localized_change=localized_change,
                texture_increase=texture_delta,
                frame_gap_ratio=frame_gap_ratio,
                sequence_gap=sequence_gap,
                motion_residual=motion_residual,
                event_start_frame=None,
                event_frame_offset=0,
                similarity_score=0.0,
                change_score=change_score,
                contrast_score=contrast_score,
                surround_score=texture_score,
            )
        )

    return sorted(regions, key=lambda region: region.score, reverse=True)


def _score_temporal_tearing(
    previous: np.ndarray,
    current: np.ndarray,
    next_frame: np.ndarray,
    *,
    localized_regions: list[StaleRegionScore],
    config: StaleRegionConfig,
    frame_gap_ratio: float,
    next_frame_gap_ratio: float,
    sequence_gap: int,
    next_sequence_gap: int,
) -> list[StaleRegionScore]:
    pair_delta = np.maximum(np.abs(current - previous), np.abs(next_frame - current))
    texture_increase = np.maximum(
        0.0,
        _laplacian_magnitude(current)
        - np.minimum(_laplacian_magnitude(previous), _laplacian_magnitude(next_frame)),
    )
    timing_score = _tearing_timing_score(
        frame_gap_ratio,
        next_frame_gap_ratio,
        sequence_gap,
        next_sequence_gap,
        config,
    )
    mask = _temporal_tearing_mask(pair_delta, texture_increase, config, timing_score)

    total_pixels = current.size
    regions: list[StaleRegionScore] = []
    for component in _connected_components(mask):
        ys, xs = _component_pixel_indices(component, config.tile_size, current.shape)
        area_pixels = len(xs)
        area_ratio = area_pixels / total_pixels
        x_min = int(xs.min())
        x_max = int(xs.max())
        y_min = int(ys.min())
        y_max = int(ys.max())
        width = x_max - x_min + 1
        height = y_max - y_min + 1

        if area_ratio < config.min_tearing_area_ratio:
            continue
        if area_ratio > config.max_tearing_area_ratio:
            continue
        if width < config.min_component_width or height < config.min_component_height:
            continue
        rectangularity = area_pixels / (width * height)
        if rectangularity < 0.25:
            continue
        bbox = (x_min, y_min, width, height)
        if any(
            region.score >= config.threshold and _bbox_iou(bbox, region.bbox) >= 0.35
            for region in localized_regions
        ):
            continue

        localized_change = float(np.mean(pair_delta[ys, xs]))
        texture_delta = float(np.mean(texture_increase[ys, xs]))
        if localized_change < config.min_tearing_localized_change:
            continue
        if texture_delta < config.min_tearing_texture_increase:
            continue

        motion_residual = _local_motion_residual(
            previous,
            current,
            next_frame,
            bbox,
            config.local_match_radius,
        )
        if motion_residual < config.min_tearing_motion_residual:
            continue

        surround_change = _surround_mean(
            pair_delta,
            x_min,
            y_min,
            width,
            height,
            config.surround_margin,
        )
        surround_texture = _surround_mean(
            texture_increase,
            x_min,
            y_min,
            width,
            height,
            config.surround_margin,
        )
        surround_contrast = (localized_change - surround_change) + (texture_delta - surround_texture)

        change_score = min(1.0, localized_change / max(config.min_tearing_localized_change * 1.25, 1e-6))
        texture_score = min(1.0, texture_delta / max(config.min_tearing_texture_increase * 1.15, 1e-6))
        residual_score = min(1.0, motion_residual / max(config.min_tearing_motion_residual * 1.25, 1e-6))
        contrast_score = min(1.0, max(surround_contrast, 0.0) / 0.10)
        area_score = min(1.0, area_ratio / 0.08)
        border_score = 1.0 if _touches_any_border(bbox, current.shape) else 0.0
        score = min(
            1.0,
            (0.24 * change_score)
            + (0.22 * texture_score)
            + (0.24 * residual_score)
            + (0.12 * contrast_score)
            + (0.08 * area_score)
            + (0.08 * timing_score)
            + (0.02 * border_score),
        )

        if score < config.min_tearing_seed_score:
            continue

        regions.append(
            StaleRegionScore(
                detector="temporal_tearing",
                score=score,
                bbox=bbox,
                area_pixels=area_pixels,
                area_ratio=area_ratio,
                rectangularity=rectangularity,
                stale_delta=0.0,
                compensated_change=motion_residual,
                future_change=float(np.mean(np.abs(next_frame - current)[ys, xs])),
                previous_to_next_change=float(np.mean(np.abs(next_frame - previous)[ys, xs])),
                temporal_contrast=surround_contrast,
                surround_change=surround_change,
                surround_contrast=surround_contrast,
                reference_lag=1,
                localized_change=localized_change,
                texture_increase=texture_delta,
                frame_gap_ratio=frame_gap_ratio,
                sequence_gap=sequence_gap,
                motion_residual=motion_residual,
                event_start_frame=None,
                event_frame_offset=0,
                similarity_score=timing_score,
                change_score=change_score,
                contrast_score=contrast_score,
                surround_score=residual_score,
            )
        )

    return sorted(regions, key=lambda region: region.score, reverse=True)


def _score_spatial_tearing(
    previous: np.ndarray,
    current: np.ndarray,
    next_frame: np.ndarray,
    *,
    config: StaleRegionConfig,
    frame_gap_ratio: float,
    sequence_gap: int,
) -> list[StaleRegionScore]:
    pair_delta = np.maximum(np.abs(current - previous), np.abs(next_frame - current))
    texture = _laplacian_magnitude(current)
    jaggedness = _edge_jaggedness(current)
    mask = _spatial_tearing_mask(current, texture, jaggedness, config)

    total_pixels = current.size
    regions: list[StaleRegionScore] = []
    for component in _connected_components(mask):
        ys, xs = _component_pixel_indices(component, config.tile_size, current.shape)
        area_pixels = len(xs)
        area_ratio = area_pixels / total_pixels
        x_min = int(xs.min())
        x_max = int(xs.max())
        y_min = int(ys.min())
        y_max = int(ys.max())
        width = x_max - x_min + 1
        height = y_max - y_min + 1
        bbox = (x_min, y_min, width, height)

        if area_ratio < config.min_tearing_area_ratio:
            continue
        if area_ratio > min(config.max_tearing_area_ratio, 0.25):
            continue
        if width < config.min_component_width or height < config.min_component_height:
            continue
        rectangularity = area_pixels / (width * height)
        if rectangularity < 0.25:
            continue
        if not _near_spatial_tearing_border(bbox, current.shape, config):
            continue

        temporal_change = float(np.mean(pair_delta[ys, xs]))
        if temporal_change > config.max_spatial_tearing_temporal_change:
            continue

        local_texture = float(np.mean(texture[ys, xs]))
        local_jaggedness = float(np.mean(jaggedness[ys, xs]))
        local_std = float(np.std(current[ys, xs]))
        if local_texture < config.min_spatial_tearing_texture:
            continue
        if local_jaggedness < config.min_spatial_tearing_jaggedness:
            continue
        if local_std < config.min_spatial_tearing_std:
            continue

        surround_texture = _surround_mean(
            texture,
            x_min,
            y_min,
            width,
            height,
            config.surround_margin,
        )
        surround_jaggedness = _surround_mean(
            jaggedness,
            x_min,
            y_min,
            width,
            height,
            config.surround_margin,
        )
        texture_contrast = local_texture - surround_texture
        jaggedness_contrast = local_jaggedness - surround_jaggedness
        if texture_contrast < config.min_spatial_tearing_texture_contrast:
            continue
        if jaggedness_contrast < config.min_spatial_tearing_jaggedness_contrast:
            continue

        texture_score = min(1.0, texture_contrast / max(config.min_spatial_tearing_texture_contrast * 2.0, 1e-6))
        jaggedness_score = min(
            1.0,
            jaggedness_contrast / max(config.min_spatial_tearing_jaggedness_contrast * 2.0, 1e-6),
        )
        edge_score = min(1.0, local_jaggedness / max(config.min_spatial_tearing_jaggedness * 1.4, 1e-6))
        std_score = min(1.0, local_std / max(config.min_spatial_tearing_std * 1.5, 1e-6))
        stability_score = 1.0 - min(
            1.0,
            temporal_change / max(config.max_spatial_tearing_temporal_change, 1e-6),
        )
        border_score = 1.0 if _touches_any_border(bbox, current.shape) else 0.65
        score = min(
            1.0,
            (0.24 * texture_score)
            + (0.24 * jaggedness_score)
            + (0.16 * edge_score)
            + (0.16 * std_score)
            + (0.16 * stability_score)
            + (0.04 * border_score),
        )
        if score < config.min_tearing_seed_score:
            continue

        regions.append(
            StaleRegionScore(
                detector="spatial_tearing",
                score=score,
                bbox=bbox,
                area_pixels=area_pixels,
                area_ratio=area_ratio,
                rectangularity=rectangularity,
                stale_delta=temporal_change,
                compensated_change=0.0,
                future_change=float(np.mean(np.abs(next_frame - current)[ys, xs])),
                previous_to_next_change=float(np.mean(np.abs(next_frame - previous)[ys, xs])),
                temporal_contrast=texture_contrast + jaggedness_contrast,
                surround_change=surround_texture,
                surround_contrast=texture_contrast + jaggedness_contrast,
                reference_lag=1,
                localized_change=temporal_change,
                texture_increase=local_texture,
                frame_gap_ratio=frame_gap_ratio,
                sequence_gap=sequence_gap,
                motion_residual=temporal_change,
                event_start_frame=None,
                event_frame_offset=0,
                similarity_score=stability_score,
                change_score=edge_score,
                contrast_score=jaggedness_score,
                surround_score=texture_score,
            )
        )

    return sorted(regions, key=lambda region: region.score, reverse=True)


def _score_active_corruption_tracks(
    tracks: list[_ActiveCorruptionTrack],
    previous: np.ndarray,
    current: np.ndarray,
    next_frame: np.ndarray,
    *,
    config: StaleRegionConfig,
) -> list[StaleRegionScore]:
    if not tracks:
        return []

    pair_delta = np.maximum(np.abs(current - previous), np.abs(next_frame - current))
    texture_increase = np.maximum(
        0.0,
        _laplacian_magnitude(current)
        - np.minimum(_laplacian_magnitude(previous), _laplacian_magnitude(next_frame)),
    )
    regions: list[StaleRegionScore] = []
    for track in tracks:
        offset = track.last_frame_index - track.start_frame_index + 1
        if offset > config.max_persistence_frames:
            continue
        regions.append(
            _score_persistent_corruption_bbox(
                track.bbox,
                pair_delta,
                texture_increase,
                previous,
                current,
                next_frame,
                config=config,
                event_start_frame=track.start_frame_index,
                event_frame_offset=offset,
            )
        )

    return sorted(regions, key=lambda region: region.score, reverse=True)


def _score_persistent_corruption_bbox(
    bbox: tuple[int, int, int, int],
    pair_delta: np.ndarray,
    texture_increase: np.ndarray,
    previous: np.ndarray,
    current: np.ndarray,
    next_frame: np.ndarray,
    *,
    config: StaleRegionConfig,
    event_start_frame: int,
    event_frame_offset: int,
) -> StaleRegionScore:
    x, y, width, height = bbox
    x0 = max(0, x)
    y0 = max(0, y)
    x1 = min(current.shape[1], x + width)
    y1 = min(current.shape[0], y + height)
    if x0 >= x1 or y0 >= y1:
        x0, y0, x1, y1 = 0, 0, 1, 1

    region = np.s_[y0:y1, x0:x1]
    area_pixels = (y1 - y0) * (x1 - x0)
    area_ratio = area_pixels / current.size
    localized_change = float(np.mean(pair_delta[region]))
    texture_delta = float(np.mean(texture_increase[region]))
    surround_change = _surround_mean(pair_delta, x0, y0, x1 - x0, y1 - y0, config.surround_margin)
    surround_texture = _surround_mean(texture_increase, x0, y0, x1 - x0, y1 - y0, config.surround_margin)
    surround_contrast = (localized_change - surround_change) + (texture_delta - surround_texture)
    change_score = min(1.0, localized_change / max(config.min_corruption_change, 1e-6))
    texture_score = min(1.0, texture_delta / max(config.min_texture_increase, 1e-6))
    persistence_score = max(0.2, 1.0 - (event_frame_offset / max(config.max_persistence_frames + 1, 1)))
    contrast_score = min(1.0, max(surround_contrast, 0.0) / 0.025)
    score = min(
        1.0,
        (0.40 * change_score)
        + (0.40 * texture_score)
        + (0.10 * contrast_score)
        + (0.10 * persistence_score),
    )

    return StaleRegionScore(
        detector="localized_corruption",
        score=score,
        bbox=(x0, y0, x1 - x0, y1 - y0),
        area_pixels=area_pixels,
        area_ratio=area_ratio,
        rectangularity=1.0,
        stale_delta=0.0,
        compensated_change=0.0,
        future_change=float(np.mean(np.abs(next_frame - current)[region])),
        previous_to_next_change=float(np.mean(np.abs(next_frame - previous)[region])),
        temporal_contrast=surround_contrast,
        surround_change=surround_change,
        surround_contrast=surround_contrast,
        reference_lag=1,
        localized_change=localized_change,
        texture_increase=texture_delta,
        frame_gap_ratio=1.0,
        sequence_gap=1,
        motion_residual=_local_motion_residual(
            previous,
            current,
            next_frame,
            (x0, y0, x1 - x0, y1 - y0),
            config.local_match_radius,
        ),
        event_start_frame=event_start_frame,
        event_frame_offset=event_frame_offset,
        similarity_score=0.0,
        change_score=change_score,
        contrast_score=contrast_score,
        surround_score=texture_score,
    )


def _update_active_corruption_tracks(
    tracks: list[_ActiveCorruptionTrack],
    track_regions: list[StaleRegionScore],
    frame_index: int,
    config: StaleRegionConfig,
) -> list[_ActiveCorruptionTrack]:
    regions_by_start = {
        region.event_start_frame: region
        for region in track_regions
        if region.event_start_frame is not None
    }
    updated: list[_ActiveCorruptionTrack] = []
    for track in tracks:
        if frame_index - track.start_frame_index >= config.max_persistence_frames:
            continue
        region = regions_by_start.get(track.start_frame_index)
        if region is None or region.score < config.min_persistence_score:
            continue
        updated.append(
            _ActiveCorruptionTrack(
                bbox=region.bbox,
                start_frame_index=track.start_frame_index,
                last_frame_index=frame_index,
            )
        )
    return updated


def _add_new_corruption_tracks(
    tracks: list[_ActiveCorruptionTrack],
    emitted_regions: list[StaleRegionScore],
    frame_index: int,
    config: StaleRegionConfig,
) -> list[_ActiveCorruptionTrack]:
    if config.max_persistence_frames <= 0:
        return tracks

    updated = list(tracks)
    for region in emitted_regions:
        if region.detector != "localized_corruption" or region.event_start_frame is not None:
            continue
        if region.score < config.threshold:
            continue
        if any(_bbox_iou(region.bbox, track.bbox) > 0.25 for track in updated):
            continue
        updated.append(
            _ActiveCorruptionTrack(
                bbox=region.bbox,
                start_frame_index=frame_index,
                last_frame_index=frame_index,
            )
        )
    return updated


def decode_jpeg_grayscale(jpeg: bytes, resize: tuple[int, int]) -> np.ndarray:
    with Image.open(io.BytesIO(jpeg)) as image:
        grayscale = image.convert("L").resize(resize, Image.Resampling.BILINEAR)
        return np.asarray(grayscale, dtype=np.float32) / 255.0


def candidate_to_dict(candidate: StaleRegionCandidate) -> dict[str, object]:
    return {
        "detector": candidate.detector,
        "topic": candidate.topic,
        "frame_index": candidate.frame_index,
        "region_index": candidate.region_index,
        "log_time_ns": candidate.log_time_ns,
        "publish_time_ns": candidate.publish_time_ns,
        "sequence": candidate.sequence,
        "timestamp_ns": candidate.timestamp_ns,
        "score": candidate.score,
        "bbox": list(candidate.bbox),
        "area_pixels": candidate.area_pixels,
        "area_ratio": candidate.area_ratio,
        "rectangularity": candidate.rectangularity,
        "stale_delta": candidate.stale_delta,
        "compensated_change": candidate.compensated_change,
        "future_change": candidate.future_change,
        "previous_to_next_change": candidate.previous_to_next_change,
        "temporal_contrast": candidate.temporal_contrast,
        "surround_change": candidate.surround_change,
        "surround_contrast": candidate.surround_contrast,
        "reference_lag": candidate.reference_lag,
        "localized_change": candidate.localized_change,
        "texture_increase": candidate.texture_increase,
        "frame_gap_ratio": candidate.frame_gap_ratio,
        "sequence_gap": candidate.sequence_gap,
        "motion_residual": candidate.motion_residual,
        "event_start_frame": candidate.event_start_frame,
        "event_frame_offset": candidate.event_frame_offset,
        "snapshot_path": candidate.snapshot_path,
    }


def candidate_events_to_dicts(candidates: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[object, ...], list[dict[str, object]]] = defaultdict(list)
    for candidate in candidates:
        event_start = candidate.get("event_start_frame")
        if event_start is None:
            event_start = candidate.get("frame_index")
        key = (
            candidate.get("mcap_path"),
            candidate.get("episode"),
            candidate.get("topic"),
            candidate.get("detector"),
            event_start,
        )
        grouped[key].append(candidate)

    events: list[dict[str, object]] = []
    for key, group in grouped.items():
        representative = max(group, key=lambda candidate: float(candidate.get("score", 0.0) or 0.0))
        frames = sorted({int(candidate.get("frame_index", 0) or 0) for candidate in group})
        scores = [float(candidate.get("score", 0.0) or 0.0) for candidate in group]
        event_start_frame = int(representative.get("event_start_frame") or frames[0])
        event_end_frame = frames[-1]
        event = dict(representative)
        event.update(
            {
                "event_id": "|".join(str(part) for part in key if part is not None),
                "event_start_frame": event_start_frame,
                "event_frame_start": event_start_frame,
                "event_frame_end": event_end_frame,
                "event_frame_count": event_end_frame - event_start_frame + 1,
                "event_candidate_count": len(group),
                "event_max_score": max(scores) if scores else 0.0,
                "event_mean_score": (sum(scores) / len(scores)) if scores else 0.0,
            }
        )
        events.append(event)

    return sorted(
        events,
        key=lambda event: (
            str(event.get("mcap_path") or ""),
            str(event.get("topic") or ""),
            int(event.get("event_start_frame", 0) or 0),
            -float(event.get("event_max_score", 0.0) or 0.0),
        ),
    )


def _mode_reference_lag(best_reference_indices: np.ndarray, previous_count: int) -> int:
    counts = np.bincount(best_reference_indices.astype(np.int32).ravel(), minlength=previous_count)
    reference_index = int(np.argmax(counts))
    return previous_count - reference_index


def _detector_enabled(config: StaleRegionConfig, detector: str) -> bool:
    return detector in config.detectors or "all" in config.detectors


def _should_run_localized_corruption(
    config: StaleRegionConfig,
    frame_gap_ratio: float,
    sequence_gap: int,
) -> bool:
    if not _detector_enabled(config, "localized_corruption"):
        return False
    if set(config.detectors) != {"localized_corruption", "temporal_tearing"}:
        return True
    return sequence_gap > 1 or frame_gap_ratio >= config.min_gap_ratio


def _tile_candidate_mask(
    prev_current_delta: np.ndarray,
    compensated_delta: np.ndarray,
    temporal_contrast: np.ndarray,
    config: StaleRegionConfig,
) -> np.ndarray:
    height, width = prev_current_delta.shape
    tile_size = max(1, config.tile_size)
    rows = (height + tile_size - 1) // tile_size
    cols = (width + tile_size - 1) // tile_size
    mask = np.zeros((rows, cols), dtype=bool)

    for row in range(rows):
        y0 = row * tile_size
        y1 = min(height, y0 + tile_size)
        for col in range(cols):
            x0 = col * tile_size
            x1 = min(width, x0 + tile_size)
            tile = np.s_[y0:y1, x0:x1]
            stale_delta = float(np.mean(prev_current_delta[tile]))
            compensated_change = float(np.mean(compensated_delta[tile]))
            contrast = float(np.mean(temporal_contrast[tile]))
            mask[row, col] = (
                stale_delta <= config.max_stale_delta
                and compensated_change >= config.min_change
                and contrast >= config.min_temporal_contrast
            )

    return mask


def _localized_corruption_mask(
    pair_delta: np.ndarray,
    texture_increase: np.ndarray,
    config: StaleRegionConfig,
    gap_boost: float,
) -> np.ndarray:
    height, width = pair_delta.shape
    tile_size = max(1, config.tile_size)
    rows = (height + tile_size - 1) // tile_size
    cols = (width + tile_size - 1) // tile_size
    mask = np.zeros((rows, cols), dtype=bool)
    change_denominator = _corruption_change_denominator(config)
    texture_denominator = _texture_increase_denominator(config)

    for row in range(rows):
        y0 = row * tile_size
        y1 = min(height, y0 + tile_size)
        for col in range(cols):
            x0 = col * tile_size
            x1 = min(width, x0 + tile_size)
            tile = np.s_[y0:y1, x0:x1]
            localized_change = float(np.mean(pair_delta[tile]))
            texture_delta = float(np.mean(texture_increase[tile]))
            local_score = (
                (0.45 * localized_change / change_denominator)
                + (0.55 * texture_delta / texture_denominator)
            )
            mask[row, col] = (
                local_score * gap_boost >= 1.0
                and (
                    localized_change >= config.min_corruption_change
                    or texture_delta >= config.min_texture_increase
                )
            )

    return mask


def _spatial_tearing_mask(
    current: np.ndarray,
    texture: np.ndarray,
    jaggedness: np.ndarray,
    config: StaleRegionConfig,
) -> np.ndarray:
    height, width = current.shape
    tile_size = max(1, config.tile_size)
    rows = (height + tile_size - 1) // tile_size
    cols = (width + tile_size - 1) // tile_size
    mask = np.zeros((rows, cols), dtype=bool)

    for row in range(rows):
        y0 = row * tile_size
        y1 = min(height, y0 + tile_size)
        for col in range(cols):
            x0 = col * tile_size
            x1 = min(width, x0 + tile_size)
            bbox = (x0, y0, x1 - x0, y1 - y0)
            if not _near_spatial_tearing_border(bbox, current.shape, config):
                continue
            tile = np.s_[y0:y1, x0:x1]
            local_texture = float(np.mean(texture[tile]))
            local_jaggedness = float(np.mean(jaggedness[tile]))
            local_std = float(np.std(current[tile]))
            mask[row, col] = (
                local_texture >= config.min_spatial_tearing_texture
                and local_jaggedness >= config.min_spatial_tearing_jaggedness
                and local_std >= config.min_spatial_tearing_std * 0.75
            )

    return mask


def _laplacian_magnitude(image: np.ndarray) -> np.ndarray:
    magnitude = np.zeros_like(image)
    magnitude[1:-1, 1:-1] = np.abs(
        (4.0 * image[1:-1, 1:-1])
        - image[:-2, 1:-1]
        - image[2:, 1:-1]
        - image[1:-1, :-2]
        - image[1:-1, 2:]
    )
    return magnitude


def _edge_jaggedness(image: np.ndarray) -> np.ndarray:
    gradient_y = np.zeros_like(image)
    gradient_x = np.zeros_like(image)
    gradient_y[1:, :] = np.abs(image[1:, :] - image[:-1, :])
    gradient_x[:, 1:] = np.abs(image[:, 1:] - image[:, :-1])

    jaggedness = np.zeros_like(image)
    jaggedness[:, 1:] = np.abs(gradient_x[:, 1:] - gradient_x[:, :-1])
    jaggedness[1:, :] = np.maximum(
        jaggedness[1:, :],
        np.abs(gradient_y[1:, :] - gradient_y[:-1, :]),
    )
    return jaggedness


def _localized_corruption_gap_boost(frame_gap_ratio: float, sequence_gap: int) -> float:
    interval_boost = min(1.5, max(0.0, frame_gap_ratio - 1.0) * 0.7)
    sequence_boost = 0.35 if sequence_gap > 1 else 0.0
    return 1.0 + interval_boost + sequence_boost


def _tearing_timing_score(
    frame_gap_ratio: float,
    next_frame_gap_ratio: float,
    sequence_gap: int,
    next_sequence_gap: int,
    config: StaleRegionConfig,
) -> float:
    if sequence_gap > 1 or next_sequence_gap > 1:
        return 1.0

    delayed_score = max(0.0, frame_gap_ratio - 1.0) / max(config.tearing_gap_scan_ratio - 1.0, 1e-6)
    catchup_score = max(0.0, 1.0 - next_frame_gap_ratio) / 0.35
    return min(1.0, (0.75 * delayed_score) + (0.25 * catchup_score))


def _temporal_tearing_mask(
    pair_delta: np.ndarray,
    texture_increase: np.ndarray,
    config: StaleRegionConfig,
    timing_score: float,
) -> np.ndarray:
    height, width = pair_delta.shape
    tile_size = max(1, config.tile_size)
    rows = (height + tile_size - 1) // tile_size
    cols = (width + tile_size - 1) // tile_size
    mask = np.zeros((rows, cols), dtype=bool)
    change_denominator = max(config.min_tearing_localized_change, 1e-6)
    texture_denominator = max(config.min_tearing_texture_increase, 1e-6)

    for row in range(rows):
        y0 = row * tile_size
        y1 = min(height, y0 + tile_size)
        for col in range(cols):
            x0 = col * tile_size
            x1 = min(width, x0 + tile_size)
            tile = np.s_[y0:y1, x0:x1]
            localized_change = float(np.mean(pair_delta[tile]))
            texture_delta = float(np.mean(texture_increase[tile]))
            local_score = (
                (0.50 * localized_change / change_denominator)
                + (0.40 * texture_delta / texture_denominator)
                + (0.10 * timing_score)
            )
            mask[row, col] = (
                local_score >= 1.0
                and localized_change >= config.min_tearing_localized_change
                and texture_delta >= config.min_tearing_texture_increase
            )

    return mask


def _corruption_change_denominator(config: StaleRegionConfig) -> float:
    return max(config.min_corruption_change * 2.25, 1e-6)


def _texture_increase_denominator(config: StaleRegionConfig) -> float:
    return max(config.min_texture_increase * 2.15, 1e-6)


def active_min_area_ratio(config: StaleRegionConfig) -> float:
    return min(config.min_area_ratio, 0.006)


def _local_motion_residual(
    previous: np.ndarray,
    current: np.ndarray,
    next_frame: np.ndarray,
    bbox: tuple[int, int, int, int],
    radius: int,
) -> float:
    return min(
        _best_local_match_error(current, previous, bbox, radius),
        _best_local_match_error(current, next_frame, bbox, radius),
    )


def _best_local_match_error(
    current: np.ndarray,
    reference: np.ndarray,
    bbox: tuple[int, int, int, int],
    radius: int,
) -> float:
    x, y, width, height = bbox
    image_height, image_width = current.shape
    x0 = max(0, x)
    y0 = max(0, y)
    x1 = min(image_width, x + width)
    y1 = min(image_height, y + height)
    if x0 >= x1 or y0 >= y1:
        return 0.0

    tile = current[y0:y1, x0:x1]
    best = float("inf")
    search_radius = max(0, radius)
    for shift_y in range(-search_radius, search_radius + 1):
        ref_y0 = y0 + shift_y
        ref_y1 = y1 + shift_y
        if ref_y0 < 0 or ref_y1 > image_height:
            continue
        for shift_x in range(-search_radius, search_radius + 1):
            ref_x0 = x0 + shift_x
            ref_x1 = x1 + shift_x
            if ref_x0 < 0 or ref_x1 > image_width:
                continue
            error = float(np.mean(np.abs(tile - reference[ref_y0:ref_y1, ref_x0:ref_x1])))
            if error < best:
                best = error

    return 0.0 if best == float("inf") else best


def _touches_motion_sensitive_border(
    bbox: tuple[int, int, int, int],
    image_shape: tuple[int, int],
) -> bool:
    x, y, width, height = bbox
    image_height, image_width = image_shape
    return x <= 0 or y <= 0 or x + width >= image_width


def _touches_any_border(
    bbox: tuple[int, int, int, int],
    image_shape: tuple[int, int],
) -> bool:
    x, y, width, height = bbox
    image_height, image_width = image_shape
    return x <= 0 or y <= 0 or x + width >= image_width or y + height >= image_height


def _update_tearing_tracks(
    tracks: list[_TearingTrack],
    regions: list[StaleRegionScore],
    frame_index: int,
    config: StaleRegionConfig,
) -> tuple[list[StaleRegionScore], list[_TearingTrack]]:
    seeds = [
        region
        for region in regions
        if region.detector == "temporal_tearing" and region.score >= config.min_tearing_seed_score
    ]
    if not seeds:
        return [], []

    updated: list[_TearingTrack] = []
    promoted: list[StaleRegionScore] = []
    used_seed_indexes: set[int] = set()

    for track in tracks:
        if frame_index - track.last_frame_index > 1:
            continue

        best_index = None
        best_iou = 0.0
        for index, seed in enumerate(seeds):
            if index in used_seed_indexes:
                continue
            iou = _bbox_iou(track.bbox, seed.bbox)
            if iou > best_iou:
                best_index = index
                best_iou = iou

        if best_index is None or best_iou < config.tearing_track_iou:
            continue

        seed = seeds[best_index]
        used_seed_indexes.add(best_index)
        next_track = _TearingTrack(
            bbox=seed.bbox,
            start_frame_index=track.start_frame_index,
            last_frame_index=frame_index,
            hit_count=track.hit_count + 1,
            max_score=max(track.max_score, seed.score),
            has_timing_jitter=track.has_timing_jitter or _tearing_seed_has_timing_jitter(seed, config),
        )
        updated.append(next_track)
        if _should_promote_tearing_track(next_track, config):
            promoted.append(_promote_tearing_region(seed, next_track, frame_index, config))

    for index, seed in enumerate(seeds):
        if index in used_seed_indexes:
            continue
        updated.append(
            _TearingTrack(
                bbox=seed.bbox,
                start_frame_index=frame_index,
                last_frame_index=frame_index,
                hit_count=1,
                max_score=seed.score,
                has_timing_jitter=_tearing_seed_has_timing_jitter(seed, config),
            )
        )

    updated.sort(key=lambda track: (track.hit_count, track.max_score), reverse=True)
    return promoted, updated[: config.max_tearing_tracks]


def _tearing_seed_has_timing_jitter(region: StaleRegionScore, config: StaleRegionConfig) -> bool:
    return region.sequence_gap > 1 or region.frame_gap_ratio >= config.tearing_gap_scan_ratio


def _should_promote_tearing_track(track: _TearingTrack, config: StaleRegionConfig) -> bool:
    return (
        track.has_timing_jitter
        and track.hit_count >= config.min_tearing_event_frames
        and track.max_score >= config.min_tearing_seed_score
    )


def _promote_tearing_region(
    region: StaleRegionScore,
    track: _TearingTrack,
    frame_index: int,
    config: StaleRegionConfig,
) -> StaleRegionScore:
    return replace(
        region,
        score=min(1.0, max(config.threshold, region.score, track.max_score)),
        event_start_frame=track.start_frame_index,
        event_frame_offset=frame_index - track.start_frame_index,
    )


def _update_spatial_tearing_tracks(
    tracks: list[_TearingTrack],
    regions: list[StaleRegionScore],
    frame_index: int,
    config: StaleRegionConfig,
) -> tuple[list[StaleRegionScore], list[_TearingTrack]]:
    seeds = [
        region
        for region in regions
        if region.detector == "spatial_tearing" and region.score >= config.min_tearing_seed_score
    ]
    if not seeds:
        return [], []

    updated: list[_TearingTrack] = []
    promoted: list[StaleRegionScore] = []
    used_seed_indexes: set[int] = set()

    for track in tracks:
        if frame_index - track.last_frame_index > 1:
            continue

        best_index = None
        best_iou = 0.0
        for index, seed in enumerate(seeds):
            if index in used_seed_indexes:
                continue
            iou = _bbox_iou(track.bbox, seed.bbox)
            if iou > best_iou:
                best_index = index
                best_iou = iou

        if best_index is None or best_iou < config.spatial_tearing_track_iou:
            continue

        seed = seeds[best_index]
        used_seed_indexes.add(best_index)
        next_track = _TearingTrack(
            bbox=seed.bbox,
            start_frame_index=track.start_frame_index,
            last_frame_index=frame_index,
            hit_count=track.hit_count + 1,
            max_score=max(track.max_score, seed.score),
            has_timing_jitter=False,
        )
        updated.append(next_track)
        if next_track.hit_count >= config.min_spatial_tearing_event_frames:
            promoted.append(_promote_tearing_region(seed, next_track, frame_index, config))

    for index, seed in enumerate(seeds):
        if index in used_seed_indexes:
            continue
        updated.append(
            _TearingTrack(
                bbox=seed.bbox,
                start_frame_index=frame_index,
                last_frame_index=frame_index,
                hit_count=1,
                max_score=seed.score,
                has_timing_jitter=False,
            )
        )

    updated.sort(key=lambda track: (track.hit_count, track.max_score), reverse=True)
    return promoted, updated[: config.max_tearing_tracks]


def _near_spatial_tearing_border(
    bbox: tuple[int, int, int, int],
    image_shape: tuple[int, int],
    config: StaleRegionConfig,
) -> bool:
    x, y, width, height = bbox
    image_height, image_width = image_shape
    margin_x = int(image_width * config.spatial_tearing_border_margin_ratio)
    margin_y = int(image_height * config.spatial_tearing_border_margin_ratio)
    return (
        x <= margin_x
        or y <= margin_y
        or x + width >= image_width - margin_x
        or y + height >= image_height - margin_y
    )


def _bbox_iou(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> float:
    first_x, first_y, first_width, first_height = first
    second_x, second_y, second_width, second_height = second
    inter_x0 = max(first_x, second_x)
    inter_y0 = max(first_y, second_y)
    inter_x1 = min(first_x + first_width, second_x + second_width)
    inter_y1 = min(first_y + first_height, second_y + second_height)
    if inter_x0 >= inter_x1 or inter_y0 >= inter_y1:
        return 0.0

    intersection = (inter_x1 - inter_x0) * (inter_y1 - inter_y0)
    first_area = first_width * first_height
    second_area = second_width * second_height
    union = first_area + second_area - intersection
    if union <= 0:
        return 0.0
    return intersection / union


def _motion_compensated_delta(
    previous: np.ndarray,
    current: np.ndarray,
    config: StaleRegionConfig,
) -> np.ndarray:
    shift_y, shift_x = _estimate_translation(previous, current)
    if (shift_y**2 + shift_x**2) ** 0.5 < config.min_global_shift:
        return np.zeros_like(current)

    aligned_previous = _shift_image(previous, shift_y, shift_x)
    return np.where(np.isnan(aligned_previous), 0.0, np.abs(current - aligned_previous))


def _estimate_translation(reference: np.ndarray, target: np.ndarray) -> tuple[int, int]:
    reference_zero_mean = reference - float(np.mean(reference))
    target_zero_mean = target - float(np.mean(target))
    reference_fft = np.fft.fft2(reference_zero_mean)
    target_fft = np.fft.fft2(target_zero_mean)
    cross_power = target_fft * np.conj(reference_fft)
    cross_power /= np.abs(cross_power) + 1e-8
    correlation = np.fft.ifft2(cross_power).real
    peak_y, peak_x = np.unravel_index(np.argmax(correlation), correlation.shape)
    height, width = reference.shape

    if peak_y > height // 2:
        peak_y -= height
    if peak_x > width // 2:
        peak_x -= width

    return int(peak_y), int(peak_x)


def _shift_image(image: np.ndarray, shift_y: int, shift_x: int) -> np.ndarray:
    shifted = np.full_like(image, np.nan, dtype=np.float32)
    height, width = image.shape

    src_y0 = max(0, -shift_y)
    src_y1 = min(height, height - shift_y)
    dst_y0 = max(0, shift_y)
    dst_y1 = min(height, height + shift_y)
    src_x0 = max(0, -shift_x)
    src_x1 = min(width, width - shift_x)
    dst_x0 = max(0, shift_x)
    dst_x1 = min(width, width + shift_x)

    if src_y0 < src_y1 and src_x0 < src_x1:
        shifted[dst_y0:dst_y1, dst_x0:dst_x1] = image[src_y0:src_y1, src_x0:src_x1]

    return shifted


def _component_pixel_indices(
    component: tuple[np.ndarray, np.ndarray],
    tile_size: int,
    image_shape: tuple[int, int],
) -> tuple[np.ndarray, np.ndarray]:
    tile_ys, tile_xs = component
    height, width = image_shape
    ys: list[np.ndarray] = []
    xs: list[np.ndarray] = []

    for tile_y, tile_x in zip(tile_ys, tile_xs):
        y0 = int(tile_y) * tile_size
        y1 = min(height, y0 + tile_size)
        x0 = int(tile_x) * tile_size
        x1 = min(width, x0 + tile_size)
        grid_y, grid_x = np.mgrid[y0:y1, x0:x1]
        ys.append(grid_y.ravel())
        xs.append(grid_x.ravel())

    return np.concatenate(ys), np.concatenate(xs)


def _surround_mean(
    image: np.ndarray,
    x: int,
    y: int,
    width: int,
    height: int,
    margin: int,
) -> float:
    y0 = max(0, y - margin)
    y1 = min(image.shape[0], y + height + margin)
    x0 = max(0, x - margin)
    x1 = min(image.shape[1], x + width + margin)
    outer = image[y0:y1, x0:x1]
    if outer.size == 0:
        return 0.0

    mask = np.ones(outer.shape, dtype=bool)
    inner_y0 = y - y0
    inner_y1 = inner_y0 + height
    inner_x0 = x - x0
    inner_x1 = inner_x0 + width
    mask[inner_y0:inner_y1, inner_x0:inner_x1] = False
    if not np.any(mask):
        return 0.0
    return float(np.mean(outer[mask]))


def _connected_components(mask: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]]:
    height, width = mask.shape
    components: list[tuple[np.ndarray, np.ndarray]] = []
    true_points = set(zip(*np.nonzero(mask)))

    while true_points:
        start = true_points.pop()
        stack = [start]
        ys: list[int] = []
        xs: list[int] = []

        while stack:
            cy, cx = stack.pop()
            ys.append(cy)
            xs.append(cx)

            for neighbor in _neighbors(cy, cx, height, width):
                if neighbor not in true_points:
                    continue
                true_points.remove(neighbor)
                stack.append(neighbor)

        components.append((np.asarray(ys, dtype=np.int32), np.asarray(xs, dtype=np.int32)))

    return components


def _neighbors(y: int, x: int, height: int, width: int):
    if y > 0:
        yield y - 1, x
    if y + 1 < height:
        yield y + 1, x
    if x > 0:
        yield y, x - 1
    if x + 1 < width:
        yield y, x + 1


def _export_snapshot(
    export_dir: Path | None,
    frame: VideoFrame,
    region_index: int,
    score: StaleRegionScore,
    analysis_size: tuple[int, int],
) -> str | None:
    if export_dir is None:
        return None

    topic_dir = export_dir / _safe_topic_name(frame.topic)
    topic_dir.mkdir(parents=True, exist_ok=True)
    detector_name = _safe_topic_name(score.detector)
    path = topic_dir / f"frame_{frame.index:06d}_{detector_name}_region_{region_index:02d}_score_{score.score:.3f}.jpg"
    with Image.open(io.BytesIO(frame.jpeg)) as image:
        preview = image.convert("RGB")
        _draw_scaled_bbox(preview, score.bbox, analysis_size)
        preview.save(path, format="JPEG", quality=95)
    return str(path)


def _safe_topic_name(topic: str) -> str:
    return topic.strip("/").replace("/", "__") or "topic"


def _draw_scaled_bbox(
    image: Image.Image,
    bbox: tuple[int, int, int, int],
    analysis_size: tuple[int, int],
) -> None:
    from PIL import ImageDraw

    analysis_width, analysis_height = analysis_size
    if analysis_width <= 0 or analysis_height <= 0:
        return

    x, y, width, height = bbox
    scale_x = image.width / analysis_width
    scale_y = image.height / analysis_height
    rect = (
        int(x * scale_x),
        int(y * scale_y),
        int((x + width) * scale_x),
        int((y + height) * scale_y),
    )
    line_width = max(3, image.width // 320)
    draw = ImageDraw.Draw(image)
    draw.rectangle(rect, outline=(255, 32, 32), width=line_width)


def _frame_interval_ns(previous: VideoFrame, current: VideoFrame) -> int:
    return max(0, _frame_time_ns(current) - _frame_time_ns(previous))


def _frame_time_ns(frame: VideoFrame) -> int:
    if frame.timestamp_ns is not None:
        return frame.timestamp_ns
    return frame.publish_time_ns or frame.log_time_ns


def _frame_gap_ratio(interval_ns: int | None, history: deque[int]) -> float:
    if interval_ns is None or interval_ns <= 0 or len(history) < 5:
        return 1.0
    baseline_ns = median(history)
    if baseline_ns <= 0:
        return 1.0
    return interval_ns / baseline_ns
