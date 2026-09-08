"""Annotation bounds, separate from the unmodified media playback timeline."""
from decimal import Decimal, InvalidOperation


def positive_duration_ns(value: object) -> int | None:
    try:
        seconds = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not seconds.is_finite() or seconds <= 0:
        return None
    value = int(seconds * Decimal(1_000_000_000))
    return value if value > 0 else None


def normalized_path(value: object) -> str:
    parts = [p for p in str(value or "").replace("\\", "/").split("/") if p not in {"", "."}]
    return "/".join(parts) if parts and ".." not in parts else ""


def annotation_timing(local_duration_ns, relative_path, job, *, requires_flow=False):
    local = max(0, int(local_duration_ns or 0))
    result = {"annotation_duration_ns": local, "flow_duration_ns": None, "annotation_timing_error": ""}
    if not requires_flow:
        return result
    result["annotation_duration_ns"] = None
    error = "Flow 时长信息缺失或 Episode 对应不唯一，请联网重新打开任务同步后再标注"
    path = normalized_path(relative_path)
    matches = []
    for item in (job.get("episodes") or []) if isinstance(job, dict) else []:
        if not isinstance(item, dict):
            continue
        base = normalized_path(item.get("relative_path"))
        primary = normalized_path(item.get("primary_file"))
        if path and base and path in {base, f"{base}/{primary}" if primary else base}:
            matches.append(item)
    if len(matches) == 1:
        duration = positive_duration_ns(matches[0].get("duration_seconds"))
        if duration is not None and local > 0:
            result.update(annotation_duration_ns=min(local, duration), flow_duration_ns=duration)
            return result
    result["annotation_timing_error"] = error
    return result


def annotation_time_error(annotation, limit: int) -> str:
    start = int(annotation.get("start_offset_ns") or 0)
    end = int(annotation.get("end_offset_ns") or 0)
    scope = annotation.get("scope")
    if start < 0 or end < start or end > limit or (scope == "time_range" and end == start) or (scope == "time_point" and end != start):
        label = annotation.get("label_code") or annotation.get("label_slug") or "未命名标注"
        return f"{label} 标注时间越界或区间无效: {start}..{end}ns，可标注终点 {limit / 1e9:.9f} 秒"
    return ""
