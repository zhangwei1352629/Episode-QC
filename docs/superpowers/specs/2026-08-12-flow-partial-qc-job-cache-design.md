# Flow 部分 QC Job 缓存设计

## 目标

让 Episode-QC 支持 Flow M3 创建的复检、抽检和仲裁任务：一个 Job 可以只覆盖同一资产中的部分 Episode。

## 固定契约

- `job.episodes` 是本次 Job 的覆盖范围，必须非空，且每个 Episode 必须出现在资产完整清单中。
- `job.asset_manifest` 与 `asset_manifest_sha256` 始终代表 NAS 上的完整资产清单，不能为了部分 Job 截断或重算摘要。
- Episode-QC 必须校验完整清单和 NAS 文件一致；同时只把 `job.episodes` 对应的文件下载进该 Job 的本地缓存。
- 提交逻辑继续根据已缓存的 Job Episode 生成结果；不修改 Flow、DataCollector、NAS 布局或结果上传路径。

## 实现

`QualityCacheManager._manifest_file_specs()` 将：

1. 保留完整资产清单的 `asset_id`、摘要和 NAS 文件校验；
2. 将原本的“Job Episode 集合必须等于完整清单集合”改为“Job Episode 集合必须是完整清单的非空子集”；
3. 对每个 Job Episode 继续核对 `relative_path`、`primary_file` 和 `checksum_sha256`；
4. 仅枚举这些 Job Episode 在完整清单中的文件，外加完整的 `asset_manifest.json`。

这样初检（Job 覆盖全部 Episode）维持原有行为；部分复检/抽检/仲裁只下载其覆盖的文件，且仍由同一份完整清单保护来源完整性。

## 测试

新增真实缓存回归：构造含两个 Episode 的完整 NAS 清单，并创建只覆盖其中一个 Episode 的 Job。验证缓存成功、状态中的 `episode_ids` 只有覆盖 Episode、本地缓存没有另一个 Episode 的文件；同时保留完整 `asset_manifest.json` 的校验。现有“任务 Episode 与清单字段不一致时拒绝”测试保持有效。

## 范围外

- 不显示或变更 `job_type` / `affects_current_result` 的界面文案；它们不影响 Episode-QC 的缓存和提交正确性。
- 不更改 Flow 的 Job serializer、DataCollector 的采集上传流程、NAS 完整清单格式或摘要算法。
