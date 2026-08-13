# QC 本地缓存定期自动清理设计

**日期：** 2026-08-10  
**状态：** 已恢复，已验证（2026-08-13）  
**范围：** Episode-QC 本地平台资产缓存（`platform-cache`）

## 目标

防止已完成质检任务的本地原始资产持续堆积并耗尽 QC 工作站磁盘，同时保持现有“结果未同步不得删除”的数据安全边界。

默认保留期为 **24 小时**：只要质检结果已完整发布到 NAS 且 Flow 回写成功，缓存从成功同步时刻起保留 24 小时；到期后由 QC Web 服务自动删除。

## 现状

`QualityCacheManager.evict(job_code)` 要求缓存状态文件 `.qc-cache.json` 的 `result_synced` 为真，才允许删除 `ready/<job_code>`。2026-08-13 已恢复 Web 服务启动、每小时和每个 Episode 磁盘检查前的自动过期回收；`episode-qc platform-evict` 仍保留为人工运维入口。

缓存布局如下：

```text
<cache_root>/
├── downloading/<job_code>.partial/    # 正在下载或可续传，不自动删除
├── ready/<job_code>/                  # 完整缓存；仅此处存在自动清理候选
└── results-pending/<job_code>/        # 结果尚待同步，不自动删除
```

## 设计

### 清理资格

`ready/<job_code>` 只有在同时满足以下条件时才可删除：

1. 目录中存在且能解析 `.qc-cache.json`；
2. `result_synced` 严格为 `true`；
3. `result_synced_at` 是有效的 UTC ISO-8601 时间；
4. 当前 UTC 时间已不早于 `result_synced_at + 24 小时`。

不符合任一条件的目录均保留。特别是下载中的 `.partial`、`results-pending`、缺少状态文件、状态文件损坏、未同步结果、未来时间戳以及小于 24 小时的已同步缓存，都不自动删除。

### 同步时间事实

`QualityCacheManager.submit_result()` 在 Flow 成功接收完整 QC 结果后，除现有的 `result_synced=true`、结果 ID、摘要与路径外，写入：

```json
{
  "result_synced": true,
  "result_synced_at": "2026-08-10T12:34:56+00:00"
}
```

该时间只在成功回写 Flow 后写入，不能在 NAS 临时发布成功或本地生成结果时提前写入。旧缓存没有该字段时一律不自动删除，仍可使用既有手工 `platform-evict` 清理。

### 清理接口

在 `QualityCacheManager` 增加一个可独立测试的公共方法：

```python
def evict_expired(
    self,
    *,
    now: datetime | None = None,
    retention: timedelta = timedelta(days=1),
) -> dict[str, object]:
```

返回值至少包含：`scanned_jobs`、`evicted_jobs`、`skipped_jobs`、`failed_jobs` 与 `freed_bytes`。方法只遍历 `ready/` 的一级子目录，验证每个候选的状态文件后使用现有受保护删除路径或同等安全检查删除目录。单个目录的读取、解析或删除失败必须记录到 `failed_jobs` 并继续扫描其他任务。

`now` 由测试注入；生产调用不传时使用 `datetime.now(timezone.utc)`。所有日期时间统一转换为 UTC，避免 Windows 本地时区和夏令时影响保留期判断。

### 调度与空间不足重试

QC Web 服务在 `serve_web_app()` 生命周期中创建一个仅管理该工作区 `platform-cache` 的后台清理循环：

1. 服务启动后立即执行一次 `evict_expired()`；
2. 之后每 1 小时执行一次；
3. Web 服务关闭时设置停止事件并等待清理线程退出；
4. 每次运行将摘要输出到服务日志；异常捕获后仅输出错误，不让清理线程或 Web 服务退出。

在 `QualityCacheManager.cache_job()` 的磁盘空间检查前，也先执行一次 `evict_expired()`，然后按现有“源数据大小 2 倍 + 预留空间”规则检查可用容量。这样任务到来时不会因过期完成缓存尚未到下一小时扫描而失败。

若清理后空间仍不足，缓存任务必须继续返回现有磁盘空间不足错误；不得删除 24 小时内的已同步缓存，也不得删除任何未同步或下载中的数据。

### 配置与可观察性

首版固定保留 **1 天**和固定检查周期 **1 小时**，不新增环境变量或页面设置，避免每台工作站出现不一致的安全策略。

日志应包含执行来源（启动、周期、缓存前）、扫描数量、删除任务数、释放字节数和失败任务编号/原因；不记录 NAS 凭据、令牌或文件内容。正常情况下跳过的目录只汇总计数，避免日志噪声。

## 非目标

- 不清理工作区 SQLite、播放派生缓存、标签和标注历史；它们属于独立留存策略。
- 不删除任何 `downloading/*.partial` 或 `results-pending/*` 内容。
- 不在磁盘压力下突破 24 小时保留期，也不采用“按最旧文件删除”的不透明策略。
- 不新增中心调度器、Windows 计划任务或需要常驻管理员权限的服务。
- 不改变手动 `platform-evict` 的安全检查与使用方式。

## 测试与验收

1. 过期且已同步的 ready 缓存会被删除，返回释放字节数和任务编号。
2. 未同步、缺少时间戳、时间戳无效、未来时间戳和未满 24 小时的 ready 缓存保留不动。
3. `.partial` 与 `results-pending` 即使很旧也不会被自动清理。
4. 单个状态文件损坏或目录删除失败不阻断其余候选清理，并反映在失败结果中。
5. Flow 成功提交结果后写入 UTC `result_synced_at`；提交失败不写入该字段。
6. Web 服务启动立即执行清理、关闭时停止后台线程；周期调度不应阻断 HTTP 请求。
7. 缓存新任务前会先执行安全清理；空间仍不足时保留现有失败语义，不删未过期数据。

## 实施验证

- 2026-08-10 执行 `./scripts/test-all.sh`：Python 77 项通过；前端 Node 测试 18 项通过。
