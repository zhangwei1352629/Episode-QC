from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path
import sys
import traceback
from typing import Any

from episode_qc import __version__
from episode_qc.annotation import annotation_payload_to_json, export_annotation_frame, index_annotation_folder
from episode_qc.mcap_video import list_image_topics
from episode_qc.playback import prepare_episode_cache, public_cache_manifest
from episode_qc.workspace import (
    delete_annotation,
    episode_detail,
    export_workspace,
    import_label_schema,
    initialize_workspace,
    preview_label_schema,
    redo_annotation_change,
    save_annotation,
    scan_data_source,
    undo_annotation_change,
    update_episode_review,
    update_workspace_settings,
    workspace_state,
)


DEFAULT_IMAGE_TOPIC = "/camera/ego_head/image/jpeg"
DEFAULT_CLI_DETECTOR = "camera-tearing"
DETECTOR_CHOICES = (
    "camera-tearing",
    "localized-corruption",
    "temporal-tearing",
    "spatial-tearing",
    "stale-region",
    "all",
)

# Keep parser defaults lightweight. The image anomaly modules import NumPy and
# Pillow, so loading their dataclasses here would penalize every short-lived
# workspace command even when detection is not used.
FLOW_DEFAULTS = {
    "window_frames": 8,
    "threshold": 0.72,
    "block_size": 8,
    "search_radius": 8,
}
STALE_REGION_DEFAULTS = {
    "threshold": 0.72,
    "tile_size": 8,
    "history_size": 3,
    "min_change": 0.08,
    "max_stale_delta": 0.035,
    "min_area_ratio": 0.01,
    "max_area_ratio": 0.35,
    "min_rectangularity": 0.55,
    "max_persistence_frames": 12,
    "min_persistence_score": 0.45,
    "min_motion_residual": 0.018,
    "border_motion_residual_multiplier": 1.5,
    "local_match_radius": 8,
    "tearing_gap_scan_ratio": 1.35,
    "tearing_gap_scan_window": 5,
    "tearing_cluster_gap_ratio": 1.25,
    "tearing_cluster_gap_count": 2,
    "tearing_cluster_window": 8,
    "min_tearing_seed_score": 0.58,
    "min_tearing_event_frames": 3,
    "min_spatial_tearing_event_frames": 3,
    "max_spatial_tearing_temporal_change": 0.018,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="episode-qc",
        description="Quality-control helpers for episode MCAP datasets.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command")

    index_parser = subparsers.add_parser(
        "index-folder",
        help="Index MCAP files and JPEG image topics for manual annotation.",
    )
    index_parser.add_argument("root_path", type=Path)
    index_parser.add_argument(
        "--json",
        type=Path,
        default=None,
        help="Write JSON results to this path instead of the text summary.",
    )
    index_parser.set_defaults(func=_cmd_index_folder)

    export_frame_parser = subparsers.add_parser(
        "export-frame",
        help="Export one JPEG topic frame for manual annotation preview.",
    )
    export_frame_parser.add_argument("mcap_path", type=Path)
    export_frame_parser.add_argument("--topic", required=True, help="Image topic to export.")
    export_frame_parser.add_argument("--frame", type=int, required=True, dest="frame_index")
    export_frame_parser.add_argument("--output", type=Path, required=True, dest="output_path")
    export_frame_parser.add_argument(
        "--json",
        type=Path,
        default=None,
        help="Write JSON frame metadata to this path instead of the text summary.",
    )
    export_frame_parser.set_defaults(func=_cmd_export_frame)

    topics_parser = subparsers.add_parser(
        "topics",
        help="List compressed JPEG image topics in an MCAP file.",
    )
    topics_parser.add_argument("mcap_path", type=Path)
    topics_parser.set_defaults(func=_cmd_topics)

    detect_parser = subparsers.add_parser(
        "detect-stale-region",
        help="Detect local stale-region artifacts in MCAP JPEG image topics.",
    )
    detect_parser.add_argument("mcap_path", type=Path)
    detect_parser.add_argument(
        "--topic",
        action="append",
        dest="topics",
        help=f"Image topic to scan. Defaults to {DEFAULT_IMAGE_TOPIC}. Can be provided multiple times.",
    )
    detect_parser.add_argument(
        "--all-topics",
        action="store_true",
        help="Scan all compressed JPEG image topics instead of the default ego-head topic.",
    )
    _add_detection_options(detect_parser)
    detect_parser.set_defaults(func=_cmd_detect_stale_region)

    flow_parser = subparsers.add_parser(
        "verify-flow",
        help="Run block optical-flow residual verification around one frame/time window.",
    )
    flow_parser.add_argument("mcap_path", type=Path)
    flow_parser.add_argument(
        "--topic",
        default=DEFAULT_IMAGE_TOPIC,
        help=f"Image topic to scan. Defaults to {DEFAULT_IMAGE_TOPIC}.",
    )
    flow_center = flow_parser.add_mutually_exclusive_group(required=True)
    flow_center.add_argument(
        "--frame",
        type=int,
        dest="center_frame",
        help="Center frame index on the selected topic.",
    )
    flow_center.add_argument(
        "--elapsed",
        type=float,
        dest="elapsed_sec",
        help="Elapsed seconds from the first selected-topic frame log time.",
    )
    flow_parser.add_argument(
        "--window-frames",
        type=int,
        default=FLOW_DEFAULTS["window_frames"],
        help="Number of frames before and after the center frame to verify.",
    )
    flow_parser.add_argument(
        "--threshold",
        type=float,
        default=FLOW_DEFAULTS["threshold"],
        help="Flow residual candidate threshold.",
    )
    flow_parser.add_argument(
        "--block-size",
        type=int,
        default=FLOW_DEFAULTS["block_size"],
        help="Block size in pixels for the numpy block-flow backend.",
    )
    flow_parser.add_argument(
        "--search-radius",
        type=int,
        default=FLOW_DEFAULTS["search_radius"],
        help="Maximum local block search radius in pixels.",
    )
    flow_parser.add_argument(
        "--resize",
        default="160x90",
        help="Analysis resolution as WIDTHxHEIGHT.",
    )
    flow_parser.add_argument(
        "--json",
        type=Path,
        default=None,
        help="Write JSON results to this path instead of the text summary.",
    )
    flow_parser.add_argument(
        "--export-dir",
        type=Path,
        default=None,
        help="Export candidate JPEG frames to this directory.",
    )
    flow_parser.set_defaults(func=_cmd_verify_flow)

    folder_parser = subparsers.add_parser(
        "scan-folder",
        help="Recursively scan all .mcap files under a folder.",
    )
    folder_parser.add_argument("root_path", type=Path)
    folder_parser.add_argument(
        "--topic",
        action="append",
        dest="topics",
        help=f"Image topic to scan. Defaults to {DEFAULT_IMAGE_TOPIC}. Can be provided multiple times.",
    )
    folder_parser.add_argument(
        "--all-topics",
        action="store_true",
        help="Scan all compressed JPEG image topics instead of the default ego-head topic.",
    )
    folder_parser.add_argument(
        "--jobs",
        type=int,
        default=min(4, os.cpu_count() or 1),
        help="Number of MCAP files to scan in parallel.",
    )
    _add_detection_options(folder_parser)
    folder_parser.set_defaults(func=_cmd_scan_folder)

    workspace_init_parser = subparsers.add_parser("workspace-init", help="Create or open a V1 SQLite workspace.")
    workspace_init_parser.add_argument("db_path", type=Path)
    workspace_init_parser.add_argument("--name", default="Mocap QC 工作区")
    workspace_init_parser.add_argument("--reviewer", default="")
    workspace_init_parser.set_defaults(func=_cmd_workspace_init)

    workspace_state_parser = subparsers.add_parser("workspace-state", help="Return the complete V1 workspace state.")
    workspace_state_parser.add_argument("db_path", type=Path)
    workspace_state_parser.set_defaults(func=_cmd_workspace_state)

    workspace_settings_parser = subparsers.add_parser("workspace-settings", help="Update workspace display settings.")
    workspace_settings_parser.add_argument("db_path", type=Path)
    workspace_settings_parser.add_argument("--name")
    workspace_settings_parser.add_argument("--reviewer")
    workspace_settings_parser.add_argument("--last-episode-id")
    workspace_settings_parser.set_defaults(func=_cmd_workspace_settings)

    workspace_scan_parser = subparsers.add_parser("workspace-scan", help="Recursively import or rescan an Episode source.")
    workspace_scan_parser.add_argument("db_path", type=Path)
    # Keep URI strings intact: Path("smb://server/share") collapses one slash.
    workspace_scan_parser.add_argument("root_path")
    workspace_scan_parser.add_argument("--profile", type=Path)
    workspace_scan_parser.set_defaults(func=_cmd_workspace_scan)

    workspace_episode_parser = subparsers.add_parser("workspace-episode", help="Return one Episode, its streams and annotations.")
    workspace_episode_parser.add_argument("db_path", type=Path)
    workspace_episode_parser.add_argument("episode_id")
    workspace_episode_parser.set_defaults(func=_cmd_workspace_episode)

    workspace_prepare_parser = subparsers.add_parser("workspace-prepare", help="Build read-only playback caches for an Episode.")
    workspace_prepare_parser.add_argument("db_path", type=Path)
    workspace_prepare_parser.add_argument("episode_id")
    workspace_prepare_parser.add_argument("cache_root", type=Path)
    workspace_prepare_parser.add_argument("--force", action="store_true")
    workspace_prepare_parser.add_argument("--mode", choices=("priority", "full"), default="full")
    workspace_prepare_parser.set_defaults(func=_cmd_workspace_prepare)

    label_preview_parser = subparsers.add_parser("label-schema-preview", help="Validate and preview a label schema import.")
    label_preview_parser.add_argument("db_path", type=Path)
    label_preview_parser.add_argument("schema_path", type=Path)
    label_preview_parser.set_defaults(func=_cmd_label_schema_preview)

    label_import_parser = subparsers.add_parser("label-schema-import", help="Validate, import and activate a label schema.")
    label_import_parser.add_argument("db_path", type=Path)
    label_import_parser.add_argument("schema_path", type=Path)
    label_import_parser.set_defaults(func=_cmd_label_schema_import)

    annotation_save_parser = subparsers.add_parser("annotation-save", help="Create or update an autosaved annotation.")
    annotation_save_parser.add_argument("db_path", type=Path)
    annotation_save_parser.add_argument("--payload", required=True)
    annotation_save_parser.add_argument("--annotation-id")
    annotation_save_parser.add_argument("--session-id", default="desktop")
    annotation_save_parser.set_defaults(func=_cmd_annotation_save)

    annotation_delete_parser = subparsers.add_parser("annotation-delete", help="Soft-delete an annotation.")
    annotation_delete_parser.add_argument("db_path", type=Path)
    annotation_delete_parser.add_argument("annotation_id")
    annotation_delete_parser.add_argument("--session-id", default="desktop")
    annotation_delete_parser.set_defaults(func=_cmd_annotation_delete)

    annotation_undo_parser = subparsers.add_parser("annotation-undo", help="Undo the last session annotation change.")
    annotation_undo_parser.add_argument("db_path", type=Path)
    annotation_undo_parser.add_argument("--session-id", default="desktop")
    annotation_undo_parser.set_defaults(func=_cmd_annotation_undo)

    annotation_redo_parser = subparsers.add_parser("annotation-redo", help="Redo the last undone session annotation change.")
    annotation_redo_parser.add_argument("db_path", type=Path)
    annotation_redo_parser.add_argument("--session-id", default="desktop")
    annotation_redo_parser.set_defaults(func=_cmd_annotation_redo)

    review_parser = subparsers.add_parser("episode-review", help="Autosave Episode review state and playhead.")
    review_parser.add_argument("db_path", type=Path)
    review_parser.add_argument("episode_id")
    review_parser.add_argument("--status")
    review_parser.add_argument("--decision")
    review_parser.add_argument("--reviewer")
    review_parser.add_argument("--playhead-ns", type=int)
    review_parser.set_defaults(func=_cmd_episode_review)

    export_parser = subparsers.add_parser("workspace-export", help="Atomically export V1 annotations, episodes and schema.")
    export_parser.add_argument("db_path", type=Path)
    export_parser.add_argument("output_parent", type=Path)
    export_parser.add_argument("--episode-id", action="append", dest="episode_ids")
    export_parser.add_argument("--completed-only", action="store_true")
    export_parser.set_defaults(func=_cmd_workspace_export)

    web_parser = subparsers.add_parser(
        "web",
        help="Run the local Episode QC Web app and optionally open it in a browser.",
    )
    web_parser.add_argument("--port", type=int, default=0, help="Local TCP port; defaults to an available port.")
    web_parser.add_argument("--workspace-root", type=Path, help="Override the local workspace directory.")
    web_parser.add_argument("--no-browser", action="store_true", help="Print the URL without opening a browser.")
    web_parser.set_defaults(func=_cmd_web)

    worker_parser = subparsers.add_parser(
        "worker",
        help="Run a persistent JSON-lines command worker for the desktop app.",
    )
    worker_parser.set_defaults(func=_cmd_worker)
    return parser


def _add_detection_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--detector",
        choices=DETECTOR_CHOICES,
        default=DEFAULT_CLI_DETECTOR,
        help="Detector to run. Default is camera-tearing, optimized for ego-head local tearing artifacts.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of frames to scan per topic.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=STALE_REGION_DEFAULTS["threshold"],
        help="Candidate score threshold.",
    )
    parser.add_argument(
        "--tile-size",
        type=int,
        default=STALE_REGION_DEFAULTS["tile_size"],
        help="Tile size in pixels for local stale-region analysis.",
    )
    parser.add_argument(
        "--history-size",
        type=int,
        default=STALE_REGION_DEFAULTS["history_size"],
        help="Number of previous frames to compare against for stale content.",
    )
    parser.add_argument(
        "--min-change",
        type=float,
        default=STALE_REGION_DEFAULTS["min_change"],
        help="Minimum current-to-next local change required inside a stale region.",
    )
    parser.add_argument(
        "--max-stale-delta",
        type=float,
        default=STALE_REGION_DEFAULTS["max_stale_delta"],
        help="Maximum previous-to-current local difference for stale pixels.",
    )
    parser.add_argument(
        "--min-area-ratio",
        type=float,
        default=STALE_REGION_DEFAULTS["min_area_ratio"],
        help="Minimum region area as a fraction of the analyzed frame.",
    )
    parser.add_argument(
        "--max-area-ratio",
        type=float,
        default=STALE_REGION_DEFAULTS["max_area_ratio"],
        help="Maximum region area as a fraction of the analyzed frame.",
    )
    parser.add_argument(
        "--min-rectangularity",
        type=float,
        default=STALE_REGION_DEFAULTS["min_rectangularity"],
        help="Minimum connected-region fill ratio inside its bounding box.",
    )
    parser.add_argument(
        "--max-persistence-frames",
        type=int,
        default=STALE_REGION_DEFAULTS["max_persistence_frames"],
        help="Maximum number of following frames to keep tracking a localized corruption event.",
    )
    parser.add_argument(
        "--min-persistence-score",
        type=float,
        default=STALE_REGION_DEFAULTS["min_persistence_score"],
        help="Minimum score needed to continue a tracked localized corruption event.",
    )
    parser.add_argument(
        "--min-motion-residual",
        type=float,
        default=STALE_REGION_DEFAULTS["min_motion_residual"],
        help="Minimum local residual after small-motion matching; raise it to reject normal camera motion edges.",
    )
    parser.add_argument(
        "--border-motion-residual-multiplier",
        type=float,
        default=STALE_REGION_DEFAULTS["border_motion_residual_multiplier"],
        help="Extra residual multiplier for regions touching left/top/right image borders.",
    )
    parser.add_argument(
        "--local-match-radius",
        type=int,
        default=STALE_REGION_DEFAULTS["local_match_radius"],
        help="Pixel radius used to explain local changes as ordinary motion.",
    )
    parser.add_argument(
        "--gap-window",
        type=int,
        default=None,
        help="Decode only frames around sequence/time gaps. Defaults to 12 for fast camera-tearing scans; use 0 to scan every frame.",
    )
    parser.add_argument(
        "--tearing-gap-ratio",
        type=float,
        default=STALE_REGION_DEFAULTS["tearing_gap_scan_ratio"],
        help="Soft time-gap ratio that opens a short temporal-tearing scan window.",
    )
    parser.add_argument(
        "--tearing-gap-window",
        type=int,
        default=STALE_REGION_DEFAULTS["tearing_gap_scan_window"],
        help="Number of following frames decoded after a soft temporal-tearing time gap.",
    )
    parser.add_argument(
        "--tearing-cluster-gap-ratio",
        type=float,
        default=STALE_REGION_DEFAULTS["tearing_cluster_gap_ratio"],
        help="Moderate time-gap ratio used for clustered tearing scan windows.",
    )
    parser.add_argument(
        "--tearing-cluster-gap-count",
        type=int,
        default=STALE_REGION_DEFAULTS["tearing_cluster_gap_count"],
        help="Number of moderate time gaps required in the recent cluster window.",
    )
    parser.add_argument(
        "--tearing-cluster-window",
        type=int,
        default=STALE_REGION_DEFAULTS["tearing_cluster_window"],
        help="Number of recent intervals used for clustered moderate-gap triggering.",
    )
    parser.add_argument(
        "--min-tearing-seed-score",
        type=float,
        default=STALE_REGION_DEFAULTS["min_tearing_seed_score"],
        help="Minimum score for a temporal-tearing seed before continuous-frame promotion.",
    )
    parser.add_argument(
        "--min-tearing-event-frames",
        type=int,
        default=STALE_REGION_DEFAULTS["min_tearing_event_frames"],
        help="Continuous temporal-tearing seed count required before reporting a bad-frame event.",
    )
    parser.add_argument(
        "--min-spatial-tearing-event-frames",
        type=int,
        default=STALE_REGION_DEFAULTS["min_spatial_tearing_event_frames"],
        help="Continuous spatial-tearing seed count required before reporting a persistent edge tearing event.",
    )
    parser.add_argument(
        "--max-spatial-tearing-temporal-change",
        type=float,
        default=STALE_REGION_DEFAULTS["max_spatial_tearing_temporal_change"],
        help="Maximum adjacent-frame change for persistent spatial tearing.",
    )
    parser.add_argument(
        "--resize",
        default="160x90",
        help="Analysis resolution as WIDTHxHEIGHT.",
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        help="Write JSON results to this path instead of the text summary.",
    )
    parser.add_argument(
        "--export-dir",
        type=Path,
        default=None,
        help="Export candidate JPEG frames to this directory.",
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    return args.func(args)


def _cmd_topics(args: argparse.Namespace) -> int:
    topics = list_image_topics(args.mcap_path)
    if not topics:
        print("No foxglove.CompressedImage JPEG topics found.")
        return 1

    for topic in topics:
        print(f"{topic.message_count:>7}  {topic.name}")
    return 0


def _cmd_index_folder(args: argparse.Namespace) -> int:
    payload = index_annotation_folder(args.root_path)
    if args.json:
        _write_json_payload(args.json, payload)
    else:
        summary = payload["summary"]
        print(
            "Indexed "
            f"{summary['scanned_files']}/{summary['files']} MCAP files; "
            f"{summary['topics']} topics; {summary['frames']} frames."
        )
        if summary["failed_files"]:
            print(f"Failed files: {summary['failed_files']}")
    return 0


def _cmd_export_frame(args: argparse.Namespace) -> int:
    payload = export_annotation_frame(
        args.mcap_path,
        topic=args.topic,
        frame_index=args.frame_index,
        output_path=args.output_path,
    )
    if args.json:
        _write_json_payload(args.json, payload)
    else:
        print(
            f"Exported {payload['topic']} frame {payload['frame_index']} "
            f"to {payload['output_path']}"
        )
    return 0


def _print_json(value: object) -> int:
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


def _cmd_workspace_init(args: argparse.Namespace) -> int:
    return _print_json(initialize_workspace(args.db_path, name=args.name, reviewer_name=args.reviewer))


def _cmd_workspace_state(args: argparse.Namespace) -> int:
    return _print_json(workspace_state(args.db_path))


def _cmd_workspace_settings(args: argparse.Namespace) -> int:
    return _print_json(
        update_workspace_settings(
            args.db_path,
            name=args.name,
            reviewer_name=args.reviewer,
            last_episode_id=args.last_episode_id,
        )
    )


def _cmd_workspace_scan(args: argparse.Namespace) -> int:
    return _print_json(scan_data_source(args.db_path, args.root_path, profile_path=args.profile))


def _cmd_workspace_episode(args: argparse.Namespace) -> int:
    return _print_json(episode_detail(args.db_path, args.episode_id))


def _cmd_workspace_prepare(args: argparse.Namespace) -> int:
    manifest = prepare_episode_cache(
        args.db_path,
        args.episode_id,
        args.cache_root,
        force=args.force,
        mode=args.mode,
    )
    public = public_cache_manifest(manifest)
    public["manifest_path"] = manifest["manifest_path"]
    public["reused"] = manifest["reused"]
    return _print_json(public)


def _cmd_label_schema_preview(args: argparse.Namespace) -> int:
    return _print_json(preview_label_schema(args.db_path, args.schema_path))


def _cmd_label_schema_import(args: argparse.Namespace) -> int:
    return _print_json(import_label_schema(args.db_path, args.schema_path))


def _cmd_annotation_save(args: argparse.Namespace) -> int:
    payload = json.loads(args.payload)
    if not isinstance(payload, dict):
        raise ValueError("annotation payload 必须是 JSON 对象")
    return _print_json(
        save_annotation(
            args.db_path,
            payload,
            annotation_id=args.annotation_id,
            session_id=args.session_id,
        )
    )


def _cmd_annotation_delete(args: argparse.Namespace) -> int:
    return _print_json(delete_annotation(args.db_path, args.annotation_id, session_id=args.session_id))


def _cmd_annotation_undo(args: argparse.Namespace) -> int:
    return _print_json(undo_annotation_change(args.db_path, session_id=args.session_id))


def _cmd_annotation_redo(args: argparse.Namespace) -> int:
    return _print_json(redo_annotation_change(args.db_path, session_id=args.session_id))


def _cmd_episode_review(args: argparse.Namespace) -> int:
    return _print_json(
        update_episode_review(
            args.db_path,
            args.episode_id,
            review_status=args.status,
            quality_decision=args.decision,
            reviewer_name=args.reviewer,
            last_playhead_ns=args.playhead_ns,
        )
    )


def _cmd_workspace_export(args: argparse.Namespace) -> int:
    return _print_json(
        export_workspace(
            args.db_path,
            args.output_parent,
            episode_ids=args.episode_ids,
            completed_only=args.completed_only,
        )
    )


def _cmd_web(args: argparse.Namespace) -> int:
    from episode_qc.web_server import serve_web_app

    serve_web_app(
        port=args.port,
        workspace_root=args.workspace_root,
        open_browser=not args.no_browser,
    )
    return 0


def _cmd_worker(_args: argparse.Namespace) -> int:
    return serve_worker(sys.stdin, sys.stdout)


def serve_worker(input_stream, output_stream) -> int:
    """Execute CLI argument lists over a line-delimited JSON protocol."""
    for line in input_stream:
        request_id: object = None
        captured_stdout = io.StringIO()
        captured_stderr = io.StringIO()
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("worker 请求必须是 JSON 对象")
            request_id = request.get("id")
            command_args = request.get("args")
            if not isinstance(command_args, list) or not all(isinstance(item, str) for item in command_args):
                raise ValueError("worker args 必须是字符串数组")
            if command_args and command_args[0] in {"worker", "web"}:
                raise ValueError(f"worker 不能启动长驻命令: {command_args[0]}")
            with redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):
                exit_code = main(command_args)
            if exit_code != 0:
                raise RuntimeError(f"episode-qc exited with code {exit_code}")
            response = {
                "id": request_id,
                "ok": True,
                "stdout": captured_stdout.getvalue(),
                "stderr": captured_stderr.getvalue(),
            }
        except (Exception, SystemExit) as exc:
            details = captured_stderr.getvalue()
            if not isinstance(exc, SystemExit):
                details += "".join(traceback.format_exception(exc))
            response = {
                "id": request_id,
                "ok": False,
                "error": str(exc),
                "stderr": details,
            }
        output_stream.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
        output_stream.flush()
    return 0


def _cmd_detect_stale_region(args: argparse.Namespace) -> int:
    topics = _selected_topics(args)
    config = _build_config(args, export_dir=args.export_dir)
    result = scan_mcap_for_stale_regions(
        args.mcap_path,
        topics=topics,
        config=config,
        max_frames_per_topic=args.limit,
    )

    if args.json:
        if str(args.json) == "-":
            print(result.to_json())
        else:
            args.json.write_text(result.to_json() + "\n", encoding="utf-8")
    else:
        _print_detection_summary(result)

    return 0


def _write_json_payload(path: Path, payload: dict[str, object]) -> None:
    text = annotation_payload_to_json(payload)
    if str(path) == "-":
        print(text)
    else:
        path.write_text(text + "\n", encoding="utf-8")


def _cmd_verify_flow(args: argparse.Namespace) -> int:
    from episode_qc.flow_verify import FlowVerifyConfig

    config = FlowVerifyConfig(
        resize=_parse_resize(args.resize),
        block_size=args.block_size,
        search_radius=args.search_radius,
        window_frames=args.window_frames,
        threshold=args.threshold,
        export_dir=args.export_dir,
    )
    payload = verify_mcap_flow_window(
        args.mcap_path,
        topic=args.topic,
        center_frame=args.center_frame,
        elapsed_sec=args.elapsed_sec,
        config=config,
    )

    if args.json:
        if str(args.json) == "-":
            print(payload_to_json(payload))
        else:
            args.json.write_text(payload_to_json(payload) + "\n", encoding="utf-8")
    else:
        _print_flow_summary(payload)

    return 0


def _cmd_scan_folder(args: argparse.Namespace) -> int:
    root = args.root_path
    if not root.exists():
        raise FileNotFoundError(f"folder does not exist: {root}")

    mcap_paths = _find_mcap_paths(root)
    topics = _selected_topics(args)
    config = _build_config(args, export_dir=None)
    result = _scan_mcap_paths(
        mcap_paths,
        root=root if root.is_dir() else root.parent,
        topics=topics,
        config=config,
        max_frames_per_topic=args.limit,
        export_dir=args.export_dir,
        jobs=max(1, args.jobs),
    )

    if args.json:
        payload = json.dumps(result, ensure_ascii=False, indent=2)
        if str(args.json) == "-":
            print(payload)
        else:
            args.json.write_text(payload + "\n", encoding="utf-8")
    else:
        _print_folder_summary(result)

    return 0


def _selected_topics(args: argparse.Namespace) -> list[str] | None:
    return None if args.all_topics else (args.topics or [DEFAULT_IMAGE_TOPIC])


def _build_config(args: argparse.Namespace, *, export_dir: Path | None) -> Any:
    from episode_qc.stale_region import StaleRegionConfig

    gap_window = args.gap_window
    if gap_window is None:
        gap_window = 12 if args.detector in {DEFAULT_CLI_DETECTOR, "localized-corruption", "temporal-tearing"} else 0
    return StaleRegionConfig(
        detectors=_detectors_from_arg(args.detector),
        threshold=args.threshold,
        tile_size=args.tile_size,
        history_size=args.history_size,
        min_change=args.min_change,
        max_stale_delta=args.max_stale_delta,
        min_area_ratio=args.min_area_ratio,
        max_area_ratio=args.max_area_ratio,
        min_rectangularity=args.min_rectangularity,
        max_persistence_frames=args.max_persistence_frames,
        min_persistence_score=args.min_persistence_score,
        min_motion_residual=args.min_motion_residual,
        border_motion_residual_multiplier=args.border_motion_residual_multiplier,
        local_match_radius=args.local_match_radius,
        tearing_gap_scan_ratio=args.tearing_gap_ratio,
        tearing_gap_scan_window=args.tearing_gap_window,
        tearing_cluster_gap_ratio=args.tearing_cluster_gap_ratio,
        tearing_cluster_gap_count=args.tearing_cluster_gap_count,
        tearing_cluster_window=args.tearing_cluster_window,
        min_tearing_seed_score=args.min_tearing_seed_score,
        min_tearing_event_frames=args.min_tearing_event_frames,
        min_spatial_tearing_event_frames=args.min_spatial_tearing_event_frames,
        max_spatial_tearing_temporal_change=args.max_spatial_tearing_temporal_change,
        gap_scan_window=gap_window if gap_window > 0 else None,
        resize=_parse_resize(args.resize),
        export_dir=export_dir,
    )


def _detectors_from_arg(value: str) -> tuple[str, ...]:
    if value == "all":
        return ("stale_region", "localized_corruption", "temporal_tearing", "spatial_tearing")
    if value == "stale-region":
        return ("stale_region",)
    if value == "temporal-tearing":
        return ("temporal_tearing",)
    if value == "spatial-tearing":
        return ("spatial_tearing",)
    if value == "localized-corruption":
        return ("localized_corruption",)
    return ("localized_corruption", "temporal_tearing")


def _find_mcap_paths(root: Path) -> list[Path]:
    if root.is_file():
        return [root] if root.suffix == ".mcap" else []
    return sorted(path for path in root.rglob("*.mcap") if path.is_file())


def _scan_mcap_paths(
    mcap_paths: list[Path],
    *,
    root: Path,
    topics: list[str] | None,
    config: Any,
    max_frames_per_topic: int | None,
    export_dir: Path | None,
    jobs: int,
) -> dict[str, object]:
    scan_args = [
        (
            path,
            root,
            topics,
            config,
            max_frames_per_topic,
            _export_dir_for_mcap(export_dir, root, path),
        )
        for path in mcap_paths
    ]

    if jobs == 1 or len(scan_args) <= 1:
        files = [_scan_one_mcap_to_dict(arguments) for arguments in scan_args]
    else:
        files = []
        with ProcessPoolExecutor(max_workers=min(jobs, len(scan_args))) as executor:
            futures = [executor.submit(_scan_one_mcap_to_dict, arguments) for arguments in scan_args]
            for future in as_completed(futures):
                files.append(future.result())
        files.sort(key=lambda item: str(item["path"]))

    return _folder_scan_payload(root, files)


def _scan_one_mcap_to_dict(arguments: tuple[Path, Path, list[str] | None, Any, int | None, Path | None]):
    mcap_path, _root, topics, config, max_frames_per_topic, export_dir = arguments
    try:
        result = scan_mcap_for_stale_regions(
            mcap_path,
            topics=topics,
            config=replace(config, export_dir=export_dir),
            max_frames_per_topic=max_frames_per_topic,
        )
        return {
            "path": str(mcap_path),
            "episode": mcap_path.parent.name,
            "ok": True,
            "result": result.to_dict(),
        }
    except Exception as exc:
        return {
            "path": str(mcap_path),
            "episode": mcap_path.parent.name,
            "ok": False,
            "error": str(exc),
        }


def _folder_scan_payload(root: Path, files: list[dict[str, object]]) -> dict[str, object]:
    candidates: list[dict[str, object]] = []
    decoded_frames = 0
    frames = 0
    decode_errors = 0
    topic_count = 0

    for file_result in files:
        result = file_result.get("result")
        if not isinstance(result, dict):
            continue
        summary = result.get("summary", {})
        if isinstance(summary, dict):
            decoded_frames += int(summary.get("decoded_frames", 0) or 0)
            frames += int(summary.get("frames", 0) or 0)
            decode_errors += int(summary.get("decode_errors", 0) or 0)
            topic_count += int(summary.get("topics", 0) or 0)
        for candidate in result.get("candidates", []):
            enriched = dict(candidate)
            enriched["mcap_path"] = file_result["path"]
            enriched["episode"] = file_result["episode"]
            candidates.append(enriched)

    events = candidate_events_to_dicts(candidates)
    return {
        "root": str(root),
        "summary": {
            "files": len(files),
            "scanned_files": sum(1 for item in files if item.get("ok")),
            "failed_files": sum(1 for item in files if not item.get("ok")),
            "frames": frames,
            "decoded_frames": decoded_frames,
            "decode_errors": decode_errors,
            "topics": topic_count,
            "candidates": len(candidates),
            "events": len(events),
        },
        "files": files,
        "candidates": candidates,
        "events": events,
    }


def _export_dir_for_mcap(base_export_dir: Path | None, root: Path, mcap_path: Path) -> Path | None:
    if base_export_dir is None:
        return None
    try:
        relative = mcap_path.relative_to(root)
    except ValueError:
        relative = Path(mcap_path.name)
    safe_parts = [_safe_path_part(part) for part in relative.with_suffix("").parts]
    return base_export_dir.joinpath(*safe_parts)


def _safe_path_part(value: str) -> str:
    return "".join(char if char.isalnum() or char in ("-", "_", ".") else "_" for char in value) or "mcap"


def scan_mcap_for_stale_regions(*args, **kwargs):
    """Load the disabled-by-default image detector only on explicit CLI use."""
    from episode_qc.stale_region import scan_mcap_for_stale_regions as scan

    return scan(*args, **kwargs)


def candidate_events_to_dicts(*args, **kwargs):
    from episode_qc.stale_region import candidate_events_to_dicts as build_events

    return build_events(*args, **kwargs)


def verify_mcap_flow_window(*args, **kwargs):
    from episode_qc.flow_verify import verify_mcap_flow_window as verify

    return verify(*args, **kwargs)


def payload_to_json(*args, **kwargs):
    from episode_qc.flow_verify import payload_to_json as serialize

    return serialize(*args, **kwargs)


def _parse_resize(value: str) -> tuple[int, int]:
    try:
        width_text, height_text = value.lower().split("x", maxsplit=1)
        width = int(width_text)
        height = int(height_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("resize must look like WIDTHxHEIGHT") from exc

    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("resize dimensions must be positive")

    return width, height


def _print_detection_summary(result) -> None:
    payload = result.to_dict()
    summary = payload["summary"]
    print(
        "Scanned "
        f"{summary['decoded_frames']} decoded frames across {summary['topics']} topics; "
        f"found {summary['events']} events / {summary['candidates']} frame candidates."
    )
    events = payload.get("events", payload.get("candidates", []))
    if len(events) > 50:
        print("Showing first 50 events. Use --json for full results.")
    for candidate in events[:50]:
        x, y, width, height = candidate["bbox"]
        print(
            f"{candidate['detector']} {candidate['topic']} "
            f"frames={candidate.get('event_frame_start', candidate['frame_index'])}-"
            f"{candidate.get('event_frame_end', candidate['frame_index'])} "
            f"score={candidate['score']:.3f} "
            f"bbox={x},{y},{width},{height} area={candidate['area_ratio']:.3%} "
            f"rect={candidate['rectangularity']:.3f} lag={candidate['reference_lag']} "
            f"gap={candidate['frame_gap_ratio']:.2f} seq_gap={candidate['sequence_gap']} "
            f"event_start={candidate.get('event_start_frame')} count={candidate.get('event_frame_count', 1)}"
        )


def _print_folder_summary(result: dict[str, object]) -> None:
    summary = result["summary"]
    print(
        "Scanned "
        f"{summary['scanned_files']}/{summary['files']} MCAP files; "
        f"{summary['decoded_frames']} decoded frames; "
        f"found {summary['events']} events / {summary['candidates']} frame candidates."
    )
    if summary["failed_files"]:
        print(f"Failed files: {summary['failed_files']}")

    events = result.get("events", result.get("candidates", []))
    if len(events) > 50:
        print("Showing first 50 events. Use --json for full results.")
    for candidate in events[:50]:
        x, y, width, height = candidate["bbox"]
        print(
            f"{candidate['episode']} {candidate['detector']} {candidate['topic']} "
            f"frames={candidate.get('event_frame_start', candidate['frame_index'])}-"
            f"{candidate.get('event_frame_end', candidate['frame_index'])} "
            f"score={candidate['score']:.3f} "
            f"bbox={x},{y},{width},{height} event_start={candidate.get('event_start_frame')} "
            f"count={candidate.get('event_frame_count', 1)}"
        )


def _print_flow_summary(result: dict[str, object]) -> None:
    summary = result["summary"]
    print(
        "Verified "
        f"{summary['decoded_frames']} decoded frames; "
        f"found {summary['events']} events / {summary['candidates']} frame candidates."
    )
    events = result.get("events", result.get("candidates", []))
    for candidate in events[:50]:
        x, y, width, height = candidate["bbox"]
        print(
            f"{candidate['detector']} {candidate['topic']} "
            f"frames={candidate.get('event_frame_start', candidate['frame_index'])}-"
            f"{candidate.get('event_frame_end', candidate['frame_index'])} "
            f"score={candidate['score']:.3f} "
            f"bbox={x},{y},{width},{height} "
            f"flow_residual={candidate['flow_residual']:.3f} "
            f"match_error={candidate['match_error']:.5f}"
        )
