import os
from pathlib import Path
import subprocess
import sys

from episode_qc import cli


def test_cli_help(capsys):
    try:
        cli.main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0

    assert "Quality-control helpers" in capsys.readouterr().out


def test_platform_submit_moves_workspace_annotations_to_direct_facts(
    monkeypatch, tmp_path, capsys
):
    """Catches labeled CLI submissions hiding Flow facts inside local metadata."""
    submitted = []

    class FakeManager:
        def __init__(self, cache_root):
            assert cache_root == tmp_path / "qc-cache"

        def local_episode_mappings(self, job_code):
            assert job_code == "QCJ-CLI-LABELS"
            return [
                {"episode_id": "FLOW-EP-1", "local_episode_id": "local-ep-1"},
                {"episode_id": "FLOW-EP-2", "local_episode_id": "local-ep-2"},
            ]

        def submit_result(self, client, job, *, episode_results, result):
            submitted.append(
                {
                    "client": client,
                    "job": job,
                    "episode_results": episode_results,
                    "result": result,
                }
            )
            return {"status": "completed"}

    client = object()
    job = {"code": "QCJ-CLI-LABELS"}
    annotation = {"annotation_id": "ann-cli-1", "label_code": "body_sway"}
    monkeypatch.setattr(cli, "QualityCacheManager", FakeManager)
    monkeypatch.setattr(cli, "_platform_client", lambda args: client)
    monkeypatch.setattr(cli, "_platform_job", lambda current_client, job_code: job)
    monkeypatch.setattr(
        cli,
        "episode_detail",
        lambda db_path, episode_id: (
            {
                "episode": {
                    "quality_decision": "pass_with_labels",
                    "annotation_count": 1,
                    "review_status": "completed",
                    "reviewer_name": "Reviewer",
                },
                "annotations": [annotation],
            }
            if episode_id == "local-ep-1"
            else {
                "episode": {
                    "quality_decision": "pass",
                    "annotation_count": 0,
                    "review_status": "completed",
                    "reviewer_name": "Reviewer",
                },
                "annotations": [],
            }
        ),
    )

    assert (
        cli.main(
            [
                "platform-submit",
                "--username",
                "reviewer",
                "--password",
                "secret",
                "QCJ-CLI-LABELS",
                str(tmp_path / "qc-cache"),
                str(tmp_path / "workspace.db"),
            ]
        )
        == 0
    )

    assert submitted == [
        {
            "client": client,
            "job": job,
            "episode_results": [
                {
                    "episode_id": "FLOW-EP-1",
                    "decision": "pass_with_labels",
                    "annotation_count": 1,
                    "annotations": [annotation],
                    "result": {
                        "local_episode_id": "local-ep-1",
                        "review_status": "completed",
                        "reviewer_name": "Reviewer",
                    },
                },
                {
                    "episode_id": "FLOW-EP-2",
                    "decision": "pass",
                    "annotation_count": 0,
                    "result": {
                        "local_episode_id": "local-ep-2",
                        "review_status": "completed",
                        "reviewer_name": "Reviewer",
                    },
                }
            ],
            "result": {"episode_count": 2},
        }
    ]
    assert '"status": "completed"' in capsys.readouterr().out


def test_web_cli_accepts_multiple_lan_hosts():
    args = cli.build_parser().parse_args(
        [
            "web",
            "--host",
            "0.0.0.0",
            "--public-host",
            "192.168.123.222",
            "--public-host",
            "10.1.11.155",
            "--port",
            "8765",
            "--standalone",
            "--no-token",
        ]
    )

    assert args.host == "0.0.0.0"
    assert args.public_host == ["192.168.123.222", "10.1.11.155"]
    assert args.port == 8765
    assert args.standalone is True
    assert args.no_token is True


def test_cli_does_not_load_image_detection_by_default():
    source_root = Path(__file__).resolve().parents[1] / "src"
    environment = os.environ | {"PYTHONPATH": str(source_root)}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import episode_qc.cli; "
                "blocked = {'episode_qc.stale_region', 'episode_qc.flow_verify', 'numpy', 'PIL'}; "
                "print(','.join(sorted(blocked.intersection(sys.modules))))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.stdout.strip() == ""


def test_workspace_scan_preserves_smb_uri(monkeypatch, tmp_path, capsys):
    captured = {}

    def fake_scan(db_path, root_path, *, profile_path):
        captured["db_path"] = db_path
        captured["root_path"] = root_path
        captured["profile_path"] = profile_path
        return {"root_path": root_path, "discovered": 0, "ready": 0, "failed": 0}

    monkeypatch.setattr(cli, "scan_data_source", fake_scan)
    uri = "smb://nas.local/datasets/%E5%90%AB%20%E7%A9%BA%E6%A0%BC"

    assert cli.main(["workspace-scan", str(tmp_path / "workspace.db"), uri]) == 0

    assert captured["root_path"] == uri
    assert captured["profile_path"] is None
    assert '"discovered": 0' in capsys.readouterr().out


def test_detect_stale_region_defaults_to_ego_head_topic(monkeypatch, capsys):
    captured = {}

    class FakeResult:
        candidates = []

        def to_json(self):
            return "{}"

    def fake_scan(mcap_path, *, topics, config, max_frames_per_topic):
        captured["topics"] = topics
        return FakeResult()

    monkeypatch.setattr(cli, "scan_mcap_for_stale_regions", fake_scan)

    assert cli.main(["detect-stale-region", "episode.mcap", "--json", "-"]) == 0

    assert captured["topics"] == [cli.DEFAULT_IMAGE_TOPIC]
    assert "{}" in capsys.readouterr().out


def test_index_folder_outputs_annotation_json(monkeypatch, tmp_path, capsys):
    def fake_index(root_path):
        return {
            "root": str(root_path),
            "summary": {"files": 1, "scanned_files": 1, "failed_files": 0, "topics": 2, "frames": 12},
            "files": [],
        }

    monkeypatch.setattr(cli, "index_annotation_folder", fake_index)

    assert cli.main(["index-folder", str(tmp_path), "--json", "-"]) == 0

    output = capsys.readouterr().out
    assert '"topics": 2' in output
    assert '"frames": 12' in output


def test_export_frame_passes_annotation_arguments(monkeypatch, tmp_path, capsys):
    captured = {}
    output_path = tmp_path / "frame.jpg"

    def fake_export(mcap_path, *, topic, frame_index, output_path):
        captured["mcap_path"] = mcap_path
        captured["topic"] = topic
        captured["frame_index"] = frame_index
        captured["output_path"] = output_path
        return {"topic": topic, "frame_index": frame_index, "output_path": str(output_path)}

    monkeypatch.setattr(cli, "export_annotation_frame", fake_export)

    assert (
        cli.main(
            [
                "export-frame",
                "episode.mcap",
                "--topic",
                "/camera/ego_head/image/jpeg",
                "--frame",
                "8",
                "--output",
                str(output_path),
                "--json",
                "-",
            ]
        )
        == 0
    )

    assert captured["topic"] == "/camera/ego_head/image/jpeg"
    assert captured["frame_index"] == 8
    assert captured["output_path"] == output_path
    assert '"frame_index": 8' in capsys.readouterr().out


def test_detect_stale_region_all_topics_uses_none_topic_filter(monkeypatch):
    captured = {}

    class FakeResult:
        candidates = []

        def to_json(self):
            return "{}"

    def fake_scan(mcap_path, *, topics, config, max_frames_per_topic):
        captured["topics"] = topics
        return FakeResult()

    monkeypatch.setattr(cli, "scan_mcap_for_stale_regions", fake_scan)

    assert cli.main(["detect-stale-region", "episode.mcap", "--all-topics", "--json", "-"]) == 0

    assert captured["topics"] is None


def test_verify_flow_uses_default_ego_head_topic(monkeypatch, capsys):
    captured = {}

    def fake_verify(mcap_path, *, topic, center_frame, elapsed_sec, config):
        captured["topic"] = topic
        captured["center_frame"] = center_frame
        captured["elapsed_sec"] = elapsed_sec
        captured["window_frames"] = config.window_frames
        return {"summary": {"frames": 3, "decoded_frames": 3, "decode_errors": 0, "candidates": 0, "events": 0}, "events": []}

    monkeypatch.setattr(cli, "verify_mcap_flow_window", fake_verify)

    assert cli.main(["verify-flow", "episode.mcap", "--frame", "12", "--window-frames", "4"]) == 0

    assert captured == {
        "topic": cli.DEFAULT_IMAGE_TOPIC,
        "center_frame": 12,
        "elapsed_sec": None,
        "window_frames": 4,
    }
    assert "Verified 3 decoded frames" in capsys.readouterr().out


def test_scan_folder_outputs_aggregate_json(monkeypatch, tmp_path, capsys):
    first = tmp_path / "episode_000001" / "episode.mcap"
    second = tmp_path / "episode_000002" / "episode.mcap"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes(b"")
    second.write_bytes(b"")

    def fake_scan_one(arguments):
        mcap_path = arguments[0]
        return {
            "path": str(mcap_path),
            "episode": mcap_path.parent.name,
            "ok": True,
            "result": {
                "summary": {"frames": 3, "decoded_frames": 3, "decode_errors": 0, "topics": 1, "candidates": 1},
                "candidates": [
                    {
                        "topic": "/camera/ego_head/image/jpeg",
                        "frame_index": 1,
                        "score": 0.9,
                        "bbox": [1, 2, 3, 4],
                    }
                ],
            },
        }

    monkeypatch.setattr(cli, "_scan_one_mcap_to_dict", fake_scan_one)

    assert cli.main(["scan-folder", str(tmp_path), "--jobs", "1", "--json", "-"]) == 0

    output = capsys.readouterr().out
    assert '"files": 2' in output
    assert '"candidates": 2' in output
    assert "episode_000001" in output
    assert "episode_000002" in output
