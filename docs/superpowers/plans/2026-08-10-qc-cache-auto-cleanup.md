# QC 本地缓存定期自动清理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Episode-QC Web 服务中安全地自动清理结果已同步超过 24 小时的平台资产缓存，避免工作站磁盘被已完成任务占满。

**Architecture:** `QualityCacheManager` 成为缓存删除资格的唯一裁决者：成功回写 Flow 时记录 UTC 同步时间，随后只扫描 `ready/` 下带有效同步时间的完成缓存。Web 层只托管一个可停止的定时循环，并在新任务缓存前复用同一清理接口；任何不符合资格、损坏或删除失败的目录均保留。

**Tech Stack:** Python 3.11+、标准库 `datetime`/`threading`/`shutil`、pytest。

## Global Constraints

- 保留期固定为 24 小时，检查周期固定为 1 小时；不新增环境变量、页面设置或 Windows 计划任务。
- 只能删除 `platform-cache/ready/<job_code>`；绝不删除 `downloading/*.partial`、`results-pending/*`、播放缓存、工作区数据库或标注历史。
- 自动清理必须以 `result_synced is True` 和 UTC `result_synced_at` 为准；旧缓存缺少该字段时保留。
- Flow 回写成功前不得写入 `result_synced_at`；磁盘仍不足时必须保留原有报错语义，不能提前删除未满 24 小时的数据。
- 单个缓存读取、解析或删除出错不影响其余缓存，也不应让 Web 服务退出。

---

### Task 1: 记录同步时间并实现可审计的过期缓存回收

**Files:**
- Modify: `src/episode_qc/platform_workflow.py:13-18,218-247,338-472`
- Modify: `tests/test_platform_workflow.py:152-265`

**Interfaces:**
- Produces: `QualityCacheManager.evict_expired(*, now: datetime | None = None, retention: timedelta = timedelta(days=1)) -> dict[str, object]`。
- Produces: `.qc-cache.json` 中仅在成功 `client.submit_result(...)` 后出现的 `result_synced_at` UTC ISO-8601 字段。
- Consumes: 现有 `QualityCacheManager.evict(job_code)` 的“`result_synced` 为真才删除”安全规则。

- [ ] **Step 1: 写入失败测试：成功同步会记录 UTC 时间，失败同步不会记录**

在 `test_flow_job_is_fully_cached_verified_submitted_and_safely_evicted` 中，在 `cache.submit_result(...)` 之后读取 `ready/QCJ-001/.qc-cache.json` 并断言：

```python
state = json.loads((tmp_path / "qc-cache" / "ready" / job["code"] / ".qc-cache.json").read_text())
assert state["result_synced"] is True
assert datetime.fromisoformat(state["result_synced_at"]).tzinfo is not None
```

新增一个 fake client，其 `submit_result()` 抛出 `FlowClientError`；调用 `submit_result()` 后断言状态文件不存在 `result_synced_at`，且缓存目录仍在。

- [ ] **Step 2: 运行同步时间测试，确认因字段不存在而失败**

Run: `uv run pytest tests/test_platform_workflow.py::test_flow_job_is_fully_cached_verified_submitted_and_safely_evicted -q`  
Expected: FAIL，断言 `.qc-cache.json` 缺少 `result_synced_at`。

- [ ] **Step 3: 写入失败测试：只删除超过保留期且完整同步的 ready 缓存**

新增 `test_evict_expired_only_removes_completed_ready_caches`，在 `tmp_path / "cache"` 下创建：过期已同步、未满 24 小时已同步、未同步、无时间戳、无效时间戳和未来时间戳六个 `ready/<job>/.qc-cache.json`；同时创建很旧的 `downloading/QCJ-partial.partial` 与 `results-pending/QCJ-pending`。以固定 UTC `now` 调用：

```python
summary = manager.evict_expired(now=datetime(2026, 8, 10, 12, tzinfo=timezone.utc))
assert summary["evicted_jobs"] == ["QCJ-expired"]
assert summary["freed_bytes"] == expected_bytes
assert (cache_root / "ready" / "QCJ-recent").is_dir()
assert (cache_root / "downloading" / "QCJ-partial.partial").is_dir()
assert (cache_root / "results-pending" / "QCJ-pending").is_dir()
```

新增 `test_evict_expired_continues_when_one_candidate_is_invalid_or_cannot_be_removed`：状态 JSON 损坏的目录必须保留并出现在 `failed_jobs`，另一个到期目录仍被删除。

- [ ] **Step 4: 运行过期回收测试，确认因 `evict_expired` 不存在而失败**

Run: `uv run pytest tests/test_platform_workflow.py -k 'evict_expired' -q`  
Expected: FAIL，`QualityCacheManager` 没有 `evict_expired`。

- [ ] **Step 5: 最小实现同步时间和过期回收**

在 `platform_workflow.py`：

```python
from datetime import datetime, timedelta, timezone

...

state["result_synced"] = True
state["result_synced_at"] = datetime.now(timezone.utc).isoformat()

def evict_expired(self, *, now=None, retention=timedelta(days=1)) -> dict[str, object]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    summary = {"scanned_jobs": 0, "evicted_jobs": [], "skipped_jobs": [], "failed_jobs": [], "freed_bytes": 0}
    ready_root = self.cache_root / "ready"
    if not ready_root.is_dir():
        return summary
    for job_root in sorted(path for path in ready_root.iterdir() if path.is_dir()):
        # 读取 .qc-cache.json；严格检查 result_synced、UTC 可解析时间与保留期。
        # 对合格项先计算目录内文件字节数，再调用 self.evict(job_code)。
        # 捕获每个候选的 OSError/JSONDecodeError/QualityCacheError，记录失败并继续。
        ...
    return summary
```

实现时将日期解析、目录大小和候选判定拆成私有辅助函数；只接受安全的一级 `job_code`，并让 `evict()` 保持为唯一真正删除 `ready/<job_code>` 的路径。

- [ ] **Step 6: 运行平台工作流测试，确认绿色**

Run: `uv run pytest tests/test_platform_workflow.py -q`  
Expected: PASS，包含现有安全手动清理测试和新增过期回收测试。

### Task 2: 在新资产缓存前执行安全回收

**Files:**
- Modify: `src/episode_qc/platform_workflow.py:218-247`
- Modify: `tests/test_platform_workflow.py`

**Interfaces:**
- Consumes: Task 1 的 `evict_expired()`。
- Preserves: `_ensure_disk_space(source_bytes)` 继续要求 `source_bytes * 2 + reserve_bytes`，错误文本与失败语义不变。

- [ ] **Step 1: 写入失败测试：磁盘检查前先触发一次过期回收**

新增 `test_cache_job_evicts_expired_before_checking_disk_space`。使用一个实际 `QualityCacheManager`，用 monkeypatch 包装 `evict_expired` 和 `_ensure_disk_space` 记录调用顺序，并让可用空间足够。调用 `cache_job(...)` 后断言：

```python
assert calls[:2] == ["evict_expired", "ensure_disk_space"]
```

测试还应保留一个 1 小时前的已同步 ready 缓存，并断言它没有被删除，证明磁盘压力路径不突破保留期。

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/test_platform_workflow.py::test_cache_job_evicts_expired_before_checking_disk_space -q`  
Expected: FAIL，`_ensure_disk_space` 先于不存在的/未调用的 `evict_expired` 执行。

- [ ] **Step 3: 在 `cache_job()` 中加入最小调用**

在解析 manifest、计算 `total_bytes` 并确认没有可复用 ready 缓存后，在 `_ensure_disk_space(total_bytes)` 前加入：

```python
try:
    self.evict_expired()
except Exception:
    # 自动回收为尽力而为；磁盘检查和正常缓存流程仍须继续。
    pass
self._ensure_disk_space(total_bytes)
```

异常日志由 Web 调度层负责；缓存路径不应吞掉 `_ensure_disk_space()` 本身抛出的 `QualityCacheError`。

- [ ] **Step 4: 运行定向测试与完整平台工作流测试**

Run: `uv run pytest tests/test_platform_workflow.py -q`  
Expected: PASS。

### Task 3: 增加可停止的 Web 服务清理循环

**Files:**
- Modify: `src/episode_qc/web_server.py:3-20,216-260,747-771,1175-1223`
- Modify: `tests/test_web_server.py:1-54`
- Modify: `README.md:368-436`

**Interfaces:**
- Produces: `PlatformCacheCleanupLoop(manager_factory, *, interval_seconds=3600.0, log=...)`，具有 `start()`、`run_once(reason)` 与 `close()`。
- Consumes: `EpisodeQcWebApplication._quality_cache_manager()` 和 `QualityCacheManager.evict_expired()`。
- Lifecycle: `EpisodeQcWebApplication` 在构造完成后启动循环，在 `close()` 中停止并 join；`EpisodeQcWebServer.server_close()` 已调用该 close 路径。

- [ ] **Step 1: 写入失败测试：启动会立即清理，关闭会停止周期线程**

在 `tests/test_web_server.py` 新增 `test_platform_cache_cleanup_loop_runs_at_startup_and_stops`。使用带 `threading.Event` 的 fake manager：

```python
calls = []
started = threading.Event()
class FakeManager:
    def evict_expired(self):
        calls.append("run")
        started.set()
        return {"scanned_jobs": 1, "evicted_jobs": ["QCJ-001"], "skipped_jobs": [], "failed_jobs": [], "freed_bytes": 1024}

loop = PlatformCacheCleanupLoop(lambda: FakeManager(), interval_seconds=0.01, log=lambda _: None)
loop.start()
assert started.wait(timeout=1)
loop.close()
assert not loop.is_running
```

新增一个 manager 抛出异常的测试，断言循环仍可 `close()`，且错误被传给 `log`；异常不得传播到 Web 服务。

- [ ] **Step 2: 运行循环测试并确认失败**

Run: `uv run pytest tests/test_web_server.py -k 'platform_cache_cleanup_loop' -q`  
Expected: FAIL，`PlatformCacheCleanupLoop` 尚不存在。

- [ ] **Step 3: 实现循环并接入 Application 生命周期**

在 `web_server.py` 使用 `threading.Event.wait()` 而不是 `time.sleep()`，确保关服不必等待一小时：

```python
class PlatformCacheCleanupLoop:
    def run_once(self, reason: str) -> dict[str, object] | None:
        try:
            result = self._manager_factory().evict_expired()
            self._log(f"QC 平台缓存清理[{reason}]：扫描 ...")
            return result
        except Exception as exc:
            self._log(f"QC 平台缓存清理[{reason}]失败：{exc}")
            return None

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            self.run_once("periodic")
```

`start()` 同步调用 `run_once("startup")` 后启动 daemon 线程。`EpisodeQcWebApplication.__init__` 用 `self._quality_cache_manager` 创建循环并立即启动；`close()` 先停止循环，再关闭两个 executor。生产间隔固定传 `3600.0`，测试仅在直接构造循环时传更短时间。

- [ ] **Step 4: 更新用户文档**

在 README 的“Flow 平台任务领取与大文件本地缓存”段落补充：已同步成功的 `platform-cache/ready` 默认保留 24 小时，启动和每小时自动清理；下载中、未同步、结果待回写缓存不自动删除；磁盘不足时会先进行一次同样的安全清理。

- [ ] **Step 5: 运行 Web 服务与平台工作流回归测试**

Run: `uv run pytest tests/test_web_server.py tests/test_platform_workflow.py -q`  
Expected: PASS，后台线程不遗留且现有 HTTP/缓存测试保持通过。

### Task 4: 全量验证与文档状态更新

**Files:**
- Modify: `docs/superpowers/specs/2026-08-10-qc-cache-auto-cleanup-design.md:4`

- [ ] **Step 1: 运行完整 Python 测试套件**

Run: `uv run pytest -q`  
Expected: PASS，0 failures。

- [ ] **Step 2: 运行前端语法/交互回归测试**

Run: `npm test`  
Expected: PASS，0 failures。

- [ ] **Step 3: 复查设计约束**

确认：自动清理只访问 `platform-cache/ready`；`result_synced_at` 仅在 Flow 成功回写后写入；24 小时内缓存、未同步缓存、损坏状态文件、`.partial` 与 `results-pending` 都未被删除；关服不会遗留清理线程。

- [ ] **Step 4: 更新规格状态**

将规格文档状态从“已确认，待实施”改为“已实施，已验证”，并在末尾记录实际测试命令与结果摘要。

