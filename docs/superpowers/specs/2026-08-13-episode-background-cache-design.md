# Episode 后台持续缓存设计

## 目标

将 Flow QC Job 的数据缓存从“整批下载、整批校验完成后才可打开”改为“按 Episode 顺序后台持续缓存”。Job 仍以 Asset 为领取和结果提交单位；每个 Episode 在自身文件下载和 SHA-256 校验完成后立即可索引、回放和审核。后台继续处理剩余 Episode，直到本 Job 覆盖范围完整缓存。

本版本不自动或主动驱逐原始 Episode 缓存。已验证的缓存、工作区标注和质检进度均保留，直至用户明确清理整个本地工作区。

## 固定约束

- Flow 的 `asset_manifest` 和 `asset_manifest_sha256` 始终是完整资产清单；Job 的 `episodes` 仅定义本次审核范围。
- 仅允许缓存 Job 覆盖且已关闭、具有固定 manifest 的 Episode。每个 Episode 的 `relative_path`、`primary_file`、校验和必须与完整清单及 NAS 上的 `asset_manifest.json` 一致。
- 每个文件继续使用 `.partial` 续传，文件完成后做 SHA-256 校验。已验证 Episode 不重复下载；重启后从已记录状态与残留 partial 文件恢复。
- Job 仍只能一次提交本次 Job 范围中全部、不重复的 Episode 结论。缓存未完整或任一 Episode 尚未审核时拒绝提交。
- 不修改 Flow、DataCollector、NAS 原始资产布局、完整 manifest 摘要算法或 QC 结果原子发布协议。

## 缓存状态和文件布局

每个 Job 在 `platform-cache/ready/<job_code>/` 下从初始化起就拥有 `.qc-cache.json`。状态文件保存完整 manifest 摘要、总字节数、每个 Job Episode 的文件清单、累计字节数和以下独立状态：

`not_cached → caching → ready`，失败时为 `failed`。`ready` 仅在该 Episode 的所有文件都已完成 SHA-256 校验后写入。Job 聚合状态为 `caching`、`partially_ready`、`cache_ready` 或 `failed`；`cache_ready` 仅在全部 Episode 为 `ready` 时成立。

每个 Episode 的实际文件仍位于其原相对路径下，例如 `ready/<job_code>/<asset_dir>/episodes/episode_000001/`。未完成文件以 `.partial` 结尾；这样每条 Episode 的续传不影响已就绪 Episode 的回放。完整 `asset_manifest.json` 在 Job 初始化时复制并校验，用于后续每条 Episode 的来源验证。

## 后台流程

1. 领取 Job 后，QC 验证 Flow Job、完整 NAS manifest 及本次 Episode 范围，建立持久 Job 状态。
2. 单个后台 worker 按 Flow Job 的 Episode 顺序处理；每次仅为当前 Episode 预留其文件大小加保留余量，而非提前要求双倍整批空间。
3. 一个 Episode 成为 `ready` 后，立即调用现有工作区扫描和映射逻辑，使其出现在本地任务中并可打开。worker 随即继续下一个 Episode。
4. 发生暂时性读取/网络/校验失败时，保留 partial 与失败原因，按有限退避重试；其它 Episode 仍可继续缓存。无法恢复时 Job 保持 `failed`，用户可通过现有“继续缓存”操作恢复；绝不删除已验证文件或标注。
5. 全部 Episode 完成后才向 Flow 报告 `cache_ready`（100%）。在此之前持续报告累计字节和进度，并将本地事件推送给 Web/Electron UI。

磁盘不足不会删除任何已缓存 Episode。worker 把当前 Episode 标为失败/等待空间，保留 partial 和可恢复状态；释放空间后可从同一 Job 继续。

## 工作区与界面契约

`WebApplication._cache_platform_job()` 需要接受“Episode 已就绪”回调：首次就绪时建立本地 Flow task，后续就绪时刷新同一 task 并保存 Flow Episode 到本地 Episode 映射。`/api/platform/jobs` 继续返回 `local_task_id`、`local_caching` 和累计进度，并新增每个 Episode 的缓存摘要，以便任务中心显示“已缓存 N/M”。

本地 Episode 列表只包含已验证就绪的 Episode；未缓存 Episode 不暴露为可回放项目。任务打开后，UI 可审核当前可用 Episode，同时显示后台缓存仍在进行。播放缓存仍由现有 `prepare_episode_cache()` 管理，与原始 Episode 下载状态分离。

## 错误处理与恢复

- Job manifest、Episode 范围、文件安全路径、大小或摘要不一致：拒绝该 Episode/Job，不产生可用本地 Episode。
- 单 Episode 校验失败：删除该坏的完整目标文件而保留其他 Episode 与 Job 状态；下次重试从该 Episode 重新下载。
- 进程重启：读取 `.qc-cache.json`，跳过已验证 Episode，续传 `not_cached`/`caching`/`failed` Episode；工作区已保存的映射不重复创建。
- Flow 结果同步失败：沿用现有 `pending_result` 机制，且禁止任何自动清理。

## 验收与测试

测试必须覆盖：

1. 两个 Episode 的 Job 在第一个 Episode 校验完成后即可获得本地任务和可用映射，第二个仍可后台缓存。
2. Job 最终仅在两个 Episode 都校验完成后报告 `cache_ready=100%`；单 Episode 状态和累计进度正确。
3. 重启/再次调用缓存时复用第一个已验证 Episode，并从第二个 Episode 的 partial 继续，不重复复制。
4. 某个 Episode 失败不删除已就绪 Episode 或工作区进度；恢复后缓存可完成。
5. 缓存尚未完整时提交被拒绝；完整缓存且所有 Episode 有结论时保留现有提交与 NAS Evidence 行为。
6. 全部现有 Flow 平台缓存、Web、工作区回归继续通过。
