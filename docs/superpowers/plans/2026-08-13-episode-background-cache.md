# Episode 后台持续缓存 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Episode-QC 在一个 Flow QC Job 内按 Episode 后台持续下载、校验和索引，并在整批完成前允许审核已就绪 Episode，且从不自动驱逐缓存。

**Architecture:** `QualityCacheManager` 将完整 Asset manifest 验证与逐 Episode 文件计划分离，在已持久化的 Job 状态中记录每个 Episode 的下载/校验进度。Web 层在每条 Episode 就绪时增量扫描并保存映射；Flow 保持 `in_progress` 状态同时接收后续缓存进度，避免正在审核的任务被回退为 `caching`。

**Tech Stack:** Python 3.12、pytest、Django 5、SQLite 测试库、pathlib、SHA-256、现有 Flow REST API 与 Episode-QC Web/Electron UI。

## Global Constraints

- Flow Job 仍按 Asset 领取、按 Job 覆盖 Episode 集合提交；提交必须包含全部且仅包含这些 Episode。
- 始终校验完整 `asset_manifest` 与 NAS `asset_manifest.json`，但只逐条处理 Job 覆盖的 Episode。
- 每个文件使用 `.partial` 续传并在 Episode 进入 `ready` 前完成 SHA-256 校验。
- 不自动调用 `evict()`、`evict_expired()` 或删除任何已验证缓存；不改 DataCollector、NAS 布局或 QC 结果发布协议。
- 仅在 Job 的所有 Episode 都缓存就绪且都已完成本地审核时提交结果。
- 直接在用户明确授权的 `Episode-QC/master` 与 `Episode-Flow/main` 修改；保留各仓库已有未跟踪文件。

---

### Task 1: Flow 保持审核状态并接受后台缓存进度

**Files:**
- Modify: `/home/zw/workspace/Episode-Flow/operations/services.py:906-936`
- Modify: `/home/zw/workspace/Episode-Flow/operations/tests/test_quality_facts.py`

**Interfaces:**
- Consumes: `update_qc_cache(job, reviewer, values)` 和 `QualityControlJob.Status`。
- Produces: `in_progress` Job 接收到 `caching`/`cache_ready` 上报时只更新缓存字段，不改变 Job 状态或 Episode 的 `qc_in_progress` 状态。

- [x] **Step 1: 写入失败的状态机回归测试**

在 `test_quality_facts.py` 的 Job 生命周期测试组添加：先把 Job 通过 `start_qc_work()` 置为 `in_progress`，再调用 `update_qc_cache()` 上报 `status="caching"`、进度 50；断言 Job 仍为 `in_progress`、`cache_progress == 50`，并断言覆盖 Episode 仍为 `qc_in_progress`。再上报 `cache_ready`，断言 Job 仍为 `in_progress` 且进度为 100。

- [x] **Step 2: 验证 RED**

Run:

```bash
env DJANGO_DB_BACKEND=sqlite /home/zw/workspace/Episode-Flow/.venv/bin/python manage.py test operations.tests.test_quality_facts -v 1 --keepdb --noinput
```

Expected: 新测试失败，因为当前实现将 Job 改回 `caching` 或 `cache_ready`。

- [x] **Step 3: 最小化修复**

在 `update_qc_cache()` 中保留上报的 `cache_progress`、`cached_bytes`、`cache_workstation` 和错误字段；若数据库中 Job 已为 `IN_PROGRESS` 而上报状态为 `CACHING` 或 `CACHE_READY`，不覆盖 `job.status`。其它状态迁移保持原样。

- [x] **Step 4: 验证 GREEN**

Run the command from Step 2. Expected: all quality-facts tests pass.

- [x] **Step 5: Commit**

```bash
git -C /home/zw/workspace/Episode-Flow add operations/services.py operations/tests/test_quality_facts.py
git -C /home/zw/workspace/Episode-Flow commit -m "fix: retain QC review state during cache updates"
```

### Task 2: 将 QC Job 缓存改为逐 Episode 持久化队列

**Files:**
- Modify: `src/episode_qc/platform_workflow.py:203-540, 726-1028`
- Modify: `tests/test_platform_workflow.py`

**Interfaces:**
- Consumes: `QualityCacheManager.cache_job(client, job, progress_callback, episode_ready_callback)`、完整 Flow Job manifest 和 `FakeFlowClient`。
- Produces: 版本化 `.qc-cache.json`，包含 `episodes` 映射（每条 `status`、`cached_bytes`、`total_bytes`、`error`、`primary_files`），并在每条验证完成时调用 `episode_ready_callback`。

- [x] **Step 1: 写入逐 Episode 就绪的失败测试**

新增 `test_cache_job_publishes_first_ready_episode_before_later_episode`：构造两个 Episode，使用 callback 记录事件；在复制第二个 Episode 前断言 callback 已收到第一个 Episode、状态文件第一个为 `ready` 且第二个不是 `ready`，并断言最终 Job 状态为 `cache_ready`、两个 Episode 都为 `ready`。

- [x] **Step 2: 验证 RED**

Run:

```bash
/home/zw/workspace/Episode-QC/.venv/bin/pytest tests/test_platform_workflow.py -k publishes_first_ready_episode -q
```

Expected: FAIL，因为当前 `cache_job()` 仅在整批缓存完成后写状态和回调。

- [x] **Step 3: 写入重启复用的失败测试**

新增 `test_cache_job_reuses_verified_episode_and_resumes_partial_later_episode`：第一次缓存时在第二条 Episode 复制过程中注入可恢复异常；确认第一条为 `ready`、第二条保留 `.partial`。再次调用 `cache_job()` 后断言第一条文件的复制函数未被再次调用，第二条完成校验，且最终进度为 100%。

- [x] **Step 4: 验证第二个 RED**

Run:

```bash
/home/zw/workspace/Episode-QC/.venv/bin/pytest tests/test_platform_workflow.py -k 'publishes_first_ready_episode or resumes_partial_later_episode' -q
```

Expected: FAIL，因为当前实现使用整批临时目录并不保存逐 Episode 状态。

- [x] **Step 5: 最小化实现缓存计划与状态恢复**

把 `_manifest_file_specs()` 的完整 manifest 与路径验证保留；新增内部按 Job Episode 分组的文件计划。`cache_job()` 在 `ready/<job_code>` 创建并原子写入初始 `.qc-cache.json`，复制并校验 `asset_manifest.json` 后顺序处理每条 Episode。每条完成后：更新累计字节和该 Episode `ready` 状态、写状态文件、上报聚合进度并调用 callback。重试时跳过已验证 Episode，继续不完整文件的 `.partial`。不要调用 `evict_expired()`；磁盘检查以当前 Episode 所需字节为单位。

- [x] **Step 6: 写入未完整缓存不可提交的失败测试**

新增 `test_submit_result_rejects_job_until_every_episode_is_cached`：创建只让第一条 Episode 进入 `ready` 的 state，传入两条审核结果，断言 `submit_result()` 抛出“尚未完整缓存”错误且不发布 NAS 结果。

- [x] **Step 7: 验证 RED 后补齐提交保护**

Run:

```bash
/home/zw/workspace/Episode-QC/.venv/bin/pytest tests/test_platform_workflow.py -k submit_result_rejects_job_until_every_episode_is_cached -q
```

Expected: FAIL，因为当前状态文件存在即可进入提交路径。然后在 `submit_result()` 明确要求 `cache_complete is True` 和所有持久 Episode 状态为 `ready`。

- [x] **Step 8: 验证 GREEN 和旧缓存兼容**

Run:

```bash
/home/zw/workspace/Episode-QC/.venv/bin/pytest tests/test_platform_workflow.py -q
```

Expected: all platform workflow tests pass, including existing full-cache, partial-job and pending-result tests.

- [x] **Step 9: Commit**

```bash
git add src/episode_qc/platform_workflow.py tests/test_platform_workflow.py
git commit -m "feat: cache Flow QC jobs episode by episode"
```

### Task 3: 增量索引、后台审核和无自动驱逐 UI 行为

**Files:**
- Modify: `src/episode_qc/web_server.py:216-315, 600-725`
- Modify: `app/renderer/renderer.js:377-462`
- Modify: `tests/test_web_server.py`

**Interfaces:**
- Consumes: `cache_job(..., episode_ready_callback=...)`、`scan_data_source()`、`record_local_episodes()`、`start_review()`。
- Produces: 第一个 ready Episode 建立 Flow 本地任务，后续 ready Episode 增量扫描映射；`/api/platform/jobs` 提供 `cached_episode_count`、`total_episode_count`、`local_task_id` 和 `local_caching`；存在本地任务时 UI 可在后台缓存期间打开它。

- [x] **Step 1: 写入首次 Episode 可打开的失败 Web 测试**

新增 `test_platform_cache_indexes_ready_episode_while_job_still_caching`：使用两条 Episode 和同步可控的 fake manager，触发第一条 callback 后断言 `/api/platform/jobs` 返回 `local_task_id`、`local_caching is True`、`cached_episode_count == 1`、`total_episode_count == 2`；调用 `start_platform_job()` 应成功并只启动一次 Flow 工作时段。

- [x] **Step 2: 验证 RED**

Run:

```bash
/home/zw/workspace/Episode-QC/.venv/bin/pytest tests/test_web_server.py -k indexes_ready_episode_while_job_still_caching -q
```

Expected: FAIL，因为当前 Web 层只在 `cache_job()` 完成后扫描、映射和开始审核。

- [x] **Step 3: 实现增量回调和聚合摘要**

在 `_cache_platform_job()` 的 Episode ready callback 内对当前 ready 根执行 `scan_data_source()`，按已就绪的 `relative_path` 更新 `record_local_episodes()`；第一次成功映射后启动一次 `start_review()`。保持后台 worker 在 `_platform_jobs` 中直至整批完成，且发生后续 Episode 错误时保留已创建 task。`_platform_payload()` 从缓存状态读取并暴露已缓存/总 Episode 数。

- [x] **Step 4: 移除自动驱逐并调整任务中心动作**

删除 `WebApplication` 对 `PlatformCacheCleanupLoop` 的启动和关闭调用，移除 `cache_job()` 开始时调用 `evict_expired()` 的逻辑。渲染器优先处理 `local_task_id`：即使 `local_caching` 为真也显示“打开任务”；进度文案显示 `已缓存 N/M` 和累计百分比。保留已有显式 CLI 清理命令，不从任何后台路径调用它。

- [x] **Step 5: 验证 GREEN**

Run:

```bash
/home/zw/workspace/Episode-QC/.venv/bin/pytest tests/test_web_server.py tests/test_platform_workflow.py -q
```

Expected: all selected tests pass; 更新或删除旧“定时自动清理启动”断言，使其改为确认启动时不会调用清理。

- [x] **Step 6: Commit**

```bash
git add src/episode_qc/web_server.py app/renderer/renderer.js tests/test_web_server.py
git commit -m "feat: review Episodes while QC cache continues"
```

### Task 4: 集成回归与交付验证

**Files:**
- Modify: `docs/superpowers/specs/2026-08-13-episode-background-cache-design.md` only if actual behavior differs from accepted design.

- [x] **Step 1: 运行 QC 完整回归**

Run:

```bash
/home/zw/workspace/Episode-QC/.venv/bin/pytest -q
```

Expected: exit 0 with no test failures.

- [x] **Step 2: 运行 Flow 完整回归**

Run:

```bash
env DJANGO_DB_BACKEND=sqlite /home/zw/workspace/Episode-Flow/.venv/bin/python manage.py test -v 1 --keepdb --noinput
```

Expected: exit 0 with no test failures.

- [x] **Step 3: 检查变更和提交边界**

Run:

```bash
git -C /home/zw/workspace/Episode-QC diff --check
git -C /home/zw/workspace/Episode-Flow diff --check
git -C /home/zw/workspace/Episode-QC status --short
git -C /home/zw/workspace/Episode-Flow status --short
```

Expected: no whitespace errors; only the user已有未跟踪文件或本计划明确提交的文件存在。
