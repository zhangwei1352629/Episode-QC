# QC 自动缓存清理恢复 Implementation Plan

**状态：** 已实施，本机验证通过（2026-08-13）。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 安全恢复 Episode-QC Web 服务的过期平台缓存自动清理，并在下载每个 Episode 前回收已同步且超过 24 小时的缓存。

**Architecture:** 保持 `QualityCacheManager.evict_expired()` 作为唯一删除逻辑。Web 层拥有可停止的单线程调度器，启动时执行一次、之后每小时执行；`cache_job()` 在每个 Episode 的空间校验前调用相同的安全清理。

**Tech Stack:** Python 3.11、标准库 threading/logging、pytest。

## Global Constraints

- 固定保留期 24 小时、固定周期 1 小时，不增加环境变量。
- 只删除 `ready/<job_code>` 中已同步且过期的缓存。
- 不删除 partial、results-pending、未同步、部分就绪或失败可恢复的 Job。
- 关闭时必须停止并 join 清理线程；清理异常不能终止 Web 服务。

---

### Task 1: 在 Episode 空间检查前恢复安全清理

**Files:**
- Modify: `src/episode_qc/platform_workflow.py:426-432`
- Modify: `tests/test_platform_workflow.py:1079-1113`

**Interface:** `QualityCacheManager.cache_job()` 在每一个新 Episode 的 `_ensure_disk_space()` 前调用一次 `evict_expired()`。

- [ ] **Step 1: 以正向行为替换旧的禁止清理测试**

```python
def test_cache_job_evicts_expired_caches_before_episode_space_check(tmp_path, monkeypatch):
    manager = QualityCacheManager(tmp_path / "cache", reserve_bytes=0)
    calls = []
    monkeypatch.setattr(manager, "evict_expired", lambda: calls.append("evict") or {})
    monkeypatch.setattr(manager, "_ensure_disk_space", lambda _: calls.append("space"))
    manager.cache_job(FakeFlowClient(job), job)
    assert calls == ["evict", "space"]
```

- [ ] **Step 2: 验证 RED**

Run: `uv run pytest tests/test_platform_workflow.py::test_cache_job_evicts_expired_caches_before_episode_space_check -q`  
Expected: FAIL，因为当前只调用 `_ensure_disk_space`。

- [ ] **Step 3: 最小实现**

在每个 Episode 的 retry 路径中紧邻空间检查前调用 `self.evict_expired()`。不改变空间不足异常或任何 evict 安全判断。

- [ ] **Step 4: 验证缓存回归**

Run: `uv run pytest tests/test_platform_workflow.py -q`  
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add src/episode_qc/platform_workflow.py tests/test_platform_workflow.py
git commit -m "fix: reclaim expired QC cache before download"
```

### Task 2: 新增可停止的 Web 清理调度器

**Files:**
- Modify: `src/episode_qc/web_server.py:WebApplication, create_web_server, server_close`
- Modify: `tests/test_web_server.py:88-127`

**Interfaces:**
- 私有 `PlatformCacheCleanup(manager_factory, interval_seconds=3600)` 有 `start()`、`run_once(source)`、`close()`。
- `WebApplication` 对本工作区 `platform-cache` 启动一个调度器；`close()` 在关闭 executor 前 join 它。

- [ ] **Step 1: 写失败的启动/关闭和异常不影响 HTTP 测试**

```python
def test_web_application_starts_and_closes_platform_cache_cleanup(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(web_server, "PlatformCacheCleanup", FakeCleanup(calls))
    with running_server(tmp_path):
        assert calls == ["start"]
    assert calls == ["start", "close"]

def test_cleanup_failure_does_not_stop_the_web_server(tmp_path, caplog, monkeypatch):
    monkeypatch.setattr(QualityCacheManager, "evict_expired", raise_runtime_error)
    with running_server(tmp_path) as (_, base_url):
        assert request_json(base_url + "/api/health")["ok"] is True
    assert "platform cache cleanup failed" in caplog.text
```

- [ ] **Step 2: 验证 RED**

Run: `uv run pytest tests/test_web_server.py -k cleanup -q`  
Expected: FAIL，因为不存在调度器且旧测试断言其不存在。

- [ ] **Step 3: 最小调度实现**

使用 daemon Thread 和 Event.wait(3600)。`start()` 同步执行有界的 startup 扫描再启动线程；线程每小时运行。每次新建 manager、记录来源/扫描/删除/释放/失败摘要，并捕获所有异常。关闭时 set event 后有限等待 join。

- [ ] **Step 4: 验证 Web 与缓存回归**

Run: `uv run pytest tests/test_web_server.py tests/test_platform_workflow.py -q`  
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add src/episode_qc/web_server.py tests/test_web_server.py tests/test_platform_workflow.py
git commit -m "feat: restore periodic QC cache cleanup"
```

### Task 3: 同步文档并进行完整验证

**Files:**
- Modify: `README.md:platform cache section`
- Modify: `docs/superpowers/specs/2026-08-10-qc-cache-auto-cleanup-design.md`
- Modify: `docs/superpowers/specs/2026-08-13-episode-background-cache-design.md`
- Modify: `docs/superpowers/plans/2026-08-10-qc-cache-auto-cleanup.md`
- Modify: `docs/superpowers/plans/2026-08-13-episode-background-cache.md`

- [ ] **Step 1: 更新已确认的策略**

记录启动/每小时/缓存前调度、24 小时留存和所有不可删除状态。替换旧的“绝不自动驱逐”表述，但保留“不删除 partial 或未同步结果”的安全边界；只勾选由本次测试证明的事项。

- [ ] **Step 2: 运行完整包验证**

Run: `uv run pytest -q`  
Expected: PASS。

- [ ] **Step 3: Commit**

```bash
git add README.md docs/superpowers/specs docs/superpowers/plans
git commit -m "docs: record restored QC cleanup policy"
```
