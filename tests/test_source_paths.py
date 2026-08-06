from __future__ import annotations

from pathlib import Path

import pytest

from episode_qc.source_paths import resolve_source_directory, resolve_target_directory
from episode_qc.workspace import scan_data_source


def test_resolves_percent_encoded_smb_uri_from_gvfs(tmp_path: Path):
    runtime_dir = tmp_path / "runtime"
    mount_root = runtime_dir / "gvfs" / "smb-share:server=NAS.local,share=Data Sets,user=tester"
    dataset = mount_root / "含 空格" / "dataset"
    dataset.mkdir(parents=True)

    resolved = resolve_source_directory(
        "smb://nas.local/Data%20Sets/%E5%90%AB%20%E7%A9%BA%E6%A0%BC/dataset",
        runtime_dir=runtime_dir,
        mountinfo_path=tmp_path / "missing-mountinfo",
    )

    assert resolved == dataset.resolve()


def test_resolves_unc_path_from_gvfs(tmp_path: Path):
    runtime_dir = tmp_path / "runtime"
    dataset = runtime_dir / "gvfs" / "smb-share:server=nas,share=datasets" / "group" / "sample"
    dataset.mkdir(parents=True)

    resolved = resolve_source_directory(
        r"\\nas\datasets\group\sample",
        runtime_dir=runtime_dir,
        mountinfo_path=tmp_path / "missing-mountinfo",
    )

    assert resolved == dataset.resolve()


def test_resolves_smb_uri_from_system_cifs_mount(tmp_path: Path):
    mount_root = tmp_path / "mounted datasets"
    dataset = mount_root / "team" / "sample"
    dataset.mkdir(parents=True)
    escaped_mount = str(mount_root).replace(" ", r"\040")
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        f"36 25 0:42 / {escaped_mount} rw,relatime - cifs //nas.local/datasets rw\n",
        encoding="utf-8",
    )

    resolved = resolve_source_directory(
        "smb://nas.local/datasets/team/sample",
        runtime_dir=tmp_path / "empty-runtime",
        mountinfo_path=mountinfo,
    )

    assert resolved == dataset.resolve()


def test_unmounted_smb_share_has_actionable_error(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="尚未挂载.*文件管理器"):
        resolve_source_directory(
            "smb://missing-nas.invalid/datasets/sample",
            runtime_dir=tmp_path / "empty-runtime",
            mountinfo_path=tmp_path / "missing-mountinfo",
        )


def test_smb_uri_rejects_parent_traversal(tmp_path: Path):
    with pytest.raises(ValueError, match="无效路径段"):
        resolve_source_directory(
            "smb://nas.local/datasets/%2E%2E/private",
            runtime_dir=tmp_path / "empty-runtime",
            mountinfo_path=tmp_path / "missing-mountinfo",
        )


def test_scan_data_source_accepts_smb_uri(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    runtime_dir = tmp_path / "runtime"
    dataset = runtime_dir / "gvfs" / "smb-share:server=nas.local,share=datasets" / "sample"
    dataset.mkdir(parents=True)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime_dir))

    result = scan_data_source(tmp_path / "workspace.db", "smb://nas.local/datasets/sample")

    assert result["requested_root_path"] == "smb://nas.local/datasets/sample"
    assert result["root_path"] == str(dataset.resolve())
    assert result["discovered"] == 0


def test_resolves_nonexistent_smb_result_target_from_existing_mount(tmp_path: Path):
    runtime_dir = tmp_path / "runtime"
    mount_root = runtime_dir / "gvfs" / "smb-share:server=nas.local,share=datasets"
    mount_root.mkdir(parents=True)

    resolved = resolve_target_directory(
        "smb://nas.local/datasets/episode-data/qc-results/AST-001/QCJ-001",
        runtime_dir=runtime_dir,
        mountinfo_path=tmp_path / "missing-mountinfo",
    )

    assert resolved == (
        mount_root / "episode-data" / "qc-results" / "AST-001" / "QCJ-001"
    ).resolve()
    assert not resolved.exists()
