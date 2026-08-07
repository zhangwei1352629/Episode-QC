from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import sys
from typing import Iterable
from urllib.parse import unquote, urlsplit


@dataclass(frozen=True)
class SmbLocation:
    server: str
    share: str
    relative_parts: tuple[str, ...]

    @property
    def uri(self) -> str:
        suffix = "/".join(self.relative_parts)
        return f"smb://{self.server}/{self.share}{f'/{suffix}' if suffix else ''}"


def resolve_source_directory(
    value: str | os.PathLike[str],
    *,
    runtime_dir: str | os.PathLike[str] | None = None,
    mountinfo_path: str | os.PathLike[str] = "/proc/self/mountinfo",
) -> Path:
    """Resolve a local directory, file URI, SMB URI, or UNC path.

    Python's pathlib cannot read ``smb://`` URIs directly. On Linux desktop
    systems an authenticated SMB share is normally exposed through GVFS under
    ``$XDG_RUNTIME_DIR/gvfs``. System-wide CIFS mounts are discovered through
    ``/proc/self/mountinfo`` as well.
    """

    raw = os.fspath(value).strip()
    if not raw:
        raise ValueError("请输入数据源目录")

    if raw.lower().startswith("file:"):
        return _resolve_file_uri(raw)

    location = _parse_smb_location(raw)
    if location is None:
        return _require_local_directory(Path(raw).expanduser())

    candidates = list(
        _smb_mount_candidates(
            location,
            runtime_dir=runtime_dir,
            mountinfo_path=mountinfo_path,
        )
    )
    for mount_root in candidates:
        candidate = _join_within_mount(mount_root, location.relative_parts)
        try:
            if candidate.is_dir():
                return candidate.resolve()
        except OSError as exc:
            raise FileNotFoundError(f"NAS 目录无法访问: {location.uri}（{exc}）") from exc

    if candidates:
        mapped = "、".join(str(_join_within_mount(root, location.relative_parts)) for root in candidates)
        raise FileNotFoundError(f"NAS 目录不存在或无权访问: {location.uri}；已挂载位置: {mapped}")
    raise FileNotFoundError(
        f"NAS 共享尚未挂载或当前进程不可访问: smb://{location.server}/{location.share}。"
        "请先在文件管理器中打开并登录该共享，或输入已挂载的本地路径。"
    )


def resolve_target_directory(
    value: str | os.PathLike[str],
    *,
    runtime_dir: str | os.PathLike[str] | None = None,
    mountinfo_path: str | os.PathLike[str] = "/proc/self/mountinfo",
) -> Path:
    """Resolve a writable target path whose final directories may not exist yet."""

    raw = os.fspath(value).strip()
    if not raw:
        raise ValueError("请输入结果目标目录")
    if raw.lower().startswith("file:"):
        parsed = urlsplit(raw)
        if parsed.scheme.lower() != "file" or parsed.query or parsed.fragment:
            raise ValueError(f"不支持的结果 URI: {raw}")
        if parsed.netloc and parsed.netloc.lower() != "localhost":
            raise ValueError("远程 file URI 不受支持；NAS 请使用 smb:// 地址")
        return Path(unquote(parsed.path)).expanduser().resolve()

    location = _parse_smb_location(raw)
    if location is None:
        return Path(raw).expanduser().resolve()
    candidates = list(
        _smb_mount_candidates(
            location,
            runtime_dir=runtime_dir,
            mountinfo_path=mountinfo_path,
        )
    )
    if not candidates:
        raise FileNotFoundError(
            f"NAS 共享尚未挂载或当前进程不可访问: "
            f"smb://{location.server}/{location.share}"
        )
    return _join_within_mount(candidates[0], location.relative_parts)


def _resolve_file_uri(raw: str) -> Path:
    parsed = urlsplit(raw)
    if parsed.scheme.lower() != "file":
        raise ValueError(f"不支持的数据源 URI: {raw}")
    if parsed.query or parsed.fragment:
        raise ValueError("file URI 不能包含查询参数或片段")
    if parsed.netloc and parsed.netloc.lower() != "localhost":
        raise ValueError("远程 file URI 不受支持；NAS 请使用 smb:// 地址")
    return _require_local_directory(Path(unquote(parsed.path)).expanduser())


def _require_local_directory(path: Path) -> Path:
    resolved = path.resolve()
    try:
        is_directory = resolved.is_dir()
    except OSError as exc:
        raise FileNotFoundError(f"数据源目录无法访问: {resolved}（{exc}）") from exc
    if not is_directory:
        if str(path).lower().startswith("smb:/"):
            raise FileNotFoundError(f"SMB 地址格式无效: {path}；请使用 smb://服务器/共享/目录")
        raise FileNotFoundError(f"数据源目录不存在: {resolved}")
    return resolved


def _parse_smb_location(raw: str) -> SmbLocation | None:
    normalized = raw
    if raw.startswith("\\\\"):
        normalized = "//" + raw[2:].replace("\\", "/")

    if normalized.lower().startswith("smb:/") and not normalized.lower().startswith("smb://"):
        raise ValueError(f"SMB 地址格式无效: {raw}；请使用 smb://服务器/共享/目录")

    if normalized.lower().startswith("smb://"):
        parsed = urlsplit(normalized)
        if parsed.query or parsed.fragment:
            raise ValueError("SMB 地址不能包含查询参数或片段")
        server = parsed.hostname or ""
        path_parts = [part for part in parsed.path.split("/") if part]
    elif normalized.startswith("//"):
        # Preserve an existing POSIX path beginning with // before treating it
        # as a UNC path.
        if Path(normalized).is_dir():
            return None
        unc_parts = [part for part in normalized[2:].split("/") if part]
        server = unc_parts[0] if unc_parts else ""
        path_parts = unc_parts[1:]
    else:
        return None

    if not server or not path_parts:
        raise ValueError(f"SMB 地址必须包含服务器和共享名: {raw}")
    decoded = tuple(_decode_smb_segment(part) for part in path_parts)
    return SmbLocation(server=unquote(server), share=decoded[0], relative_parts=decoded[1:])


def _decode_smb_segment(value: str) -> str:
    decoded = unquote(value)
    if not decoded or decoded in {".", ".."} or "/" in decoded or "\\" in decoded or "\0" in decoded:
        raise ValueError(f"SMB 地址包含无效路径段: {value}")
    return decoded


def _smb_mount_candidates(
    location: SmbLocation,
    *,
    runtime_dir: str | os.PathLike[str] | None,
    mountinfo_path: str | os.PathLike[str],
) -> Iterable[Path]:
    seen: set[Path] = set()
    for candidate in (
        *_gvfs_mounts(location, runtime_dir=runtime_dir),
        *_cifs_mounts(location, mountinfo_path=mountinfo_path),
        *_native_smb_mounts(location),
    ):
        try:
            normalized = candidate.resolve()
        except OSError:
            normalized = candidate.absolute()
        if normalized not in seen:
            seen.add(normalized)
            yield normalized


def _gvfs_mounts(location: SmbLocation, *, runtime_dir: str | os.PathLike[str] | None) -> list[Path]:
    if runtime_dir is None:
        configured = os.environ.get("XDG_RUNTIME_DIR")
        if configured:
            runtime_root = Path(configured)
        elif hasattr(os, "getuid"):
            runtime_root = Path("/run/user") / str(os.getuid())
        else:
            return []
    else:
        runtime_root = Path(runtime_dir)

    gvfs_root = runtime_root / "gvfs"
    try:
        entries = list(gvfs_root.iterdir())
    except OSError:
        return []
    matches: list[Path] = []
    for entry in entries:
        fields = _parse_gvfs_name(entry.name)
        if fields.get("server", "").casefold() != location.server.casefold():
            continue
        if fields.get("share", "").casefold() != location.share.casefold():
            continue
        matches.append(entry)
    return matches


def _parse_gvfs_name(name: str) -> dict[str, str]:
    if not name.startswith("smb-share:"):
        return {}
    fields: dict[str, str] = {}
    for item in name.removeprefix("smb-share:").split(","):
        key, separator, value = item.partition("=")
        if separator:
            fields[key] = unquote(value)
    return fields


def _cifs_mounts(location: SmbLocation, *, mountinfo_path: str | os.PathLike[str]) -> list[Path]:
    try:
        lines = Path(mountinfo_path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    matches: list[Path] = []
    for line in lines:
        before, separator, after = line.partition(" - ")
        if not separator:
            continue
        mount_fields = before.split()
        filesystem_fields = after.split()
        if len(mount_fields) < 5 or len(filesystem_fields) < 2:
            continue
        if filesystem_fields[0].casefold() not in {"cifs", "smb3"}:
            continue
        source = _unescape_mount_field(filesystem_fields[1]).replace("\\", "/")
        match = re.match(r"^//([^/]+)/([^/]+)", source)
        if not match:
            continue
        if match.group(1).casefold() != location.server.casefold():
            continue
        if unquote(match.group(2)).casefold() != location.share.casefold():
            continue
        matches.append(Path(_unescape_mount_field(mount_fields[4])))
    return matches


def _unescape_mount_field(value: str) -> str:
    return re.sub(
        r"\\([0-7]{3})",
        lambda match: chr(int(match.group(1), 8)),
        value,
    )


def _native_smb_mounts(location: SmbLocation) -> list[Path]:
    if os.name == "nt":
        # Callers append ``relative_parts`` after selecting a mount root.  The
        # native Windows candidate must therefore be the share root, otherwise
        # smb://server/share/a became \\server\share\a\a.
        return [Path(_windows_unc_root(location))]
    if sys.platform == "darwin":
        return [Path("/Volumes") / location.share]
    return []


def _windows_unc_root(location: SmbLocation) -> str:
    return f"\\\\{location.server}\\{location.share}"


def _join_within_mount(mount_root: Path, relative_parts: tuple[str, ...]) -> Path:
    root = mount_root.resolve()
    candidate = root.joinpath(*relative_parts).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("SMB 子目录超出共享根目录") from exc
    return candidate
