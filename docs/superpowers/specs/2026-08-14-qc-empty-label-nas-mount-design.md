# QC 无标签任务兼容与本机 NAS 挂载设计

**日期：** 2026-08-14  
**范围：** 本机 Episode-QC、Episode-Flow 接口契约、本机 NAS 只读源数据访问  
**不包含：** 修改 10.1.10.159 的代码或服务、自动创建任务标签库、改写已有 Flow 任务的冻结标签快照、删除缓存或 NAS 数据

## 目标

修复本机 Episode-QC 缓存任务 `QCJ-20260813-00001` 在复制数据前报错“Flow 冻结标签引用不完整”的问题，并让本机能够读取 Flow 下发的 `/nas/data_collection/...` 源目录。

成功标准：

1. 没有冻结标签库的旧任务可继续缓存，并保持零标注兼容模式。
2. 真正的部分标签引用、错误摘要或错误 Schema 仍被拒绝。
3. Flow 对没有冻结标签库的任务返回一致、无歧义的空引用。
4. 本机重启后可自动按需挂载 NAS，共享根与 Flow 平台路径一致。
5. 真实任务至少进入 Episode 文件枚举或复制阶段，不再因空标签引用或源目录不存在失败。
6. 不删除现有失败缓存、工作区数据或 NAS 数据。

## 根因

Flow 当前在任务没有 `label_set_version` 时返回：

```json
{
  "label_set_id": null,
  "label_schema_version": null,
  "label_schema_hash": "",
  "label_schema": null
}
```

Episode-QC 的标签安装逻辑只把 `null` 当作未提供，因此把空字符串摘要视为唯一已提供字段并判定引用不完整。该校验发生在 `QualityCacheManager.cache_job()` 之前，所以任务保持 `0/3 Episode`、`0 bytes`。

此外，本机没有 `/nas/data_collection` 挂载。Flow 下发的源目录是平台路径 `/nas/data_collection/...`；159 上已确认其物理共享来源为 `//delta-ai-nas.local/datasets`。即使标签兼容问题消失，本机也会在源目录解析阶段失败。

## 标签引用契约

标签引用由以下四项组成：

- `label_set_id`
- `label_schema_version`
- `label_schema_hash`
- `label_schema`

Flow 在没有冻结标签库时对四项统一返回 JSON `null`。数据库中的 `label_schema_hash` 可继续保持空字符串，不做数据迁移；规范化仅发生在 API 序列化边界。

QC 按以下规则解释输入：

1. 三个字符串字段的 `null` 和 `""` 均视为未提供；`label_schema` 仅以 `null` 视为未提供。
2. 四项全部未提供时返回 `{"active": false}`，不创建或激活本地标签库。
3. 只要任一项已提供，四项都必须有效；缺项继续报“Flow 冻结标签引用不完整”。
4. 完整引用继续执行 64 位小写 SHA-256、规范 JSON 摘要、Schema 结构以及头部 ID/版本一致性校验。
5. 无冻结标签库只允许零标注兼容流程；已有标注但缺少完整引用时，现有提交校验继续拒绝。

该规则兼容当前 159 的旧响应，也使更新后的 Flow 响应具备明确契约。

## 本机 NAS 映射

本机新增 systemd automount：

- 共享来源：`//delta-ai-nas.local/datasets`
- 本机挂载点：`/nas/data_collection`
- 文件系统：CIFS 3.0
- 挂载权限：`ro`，QC 只读取原始资产，不通过该挂载写入或修改 NAS
- 身份映射：本机 `zw` 用户和组，UID/GID 均为 1000
- 凭据：复用 159 已配置的 NAS 凭据，保存为本机 root 所有、权限 `0600` 的独立文件
- 行为：网络就绪后按访问触发挂载，空闲后可自动卸载；系统启动不因 NAS 暂时离线而失败

挂载只解决 Flow 当前原始数据平台根。不会创建 `/nas/qc-results`，也不会创建、移动或删除 NAS 上的目录。QC 结果目录属于后续结果发布配置，不是本次缓存任务进入复制阶段的前置条件。

凭据传输不得把账号或密码写入仓库、命令日志或设计文档。远端若必须生成临时副本，只允许当前 SSH 用户读取，复制完成后立即删除；本机安装完成后只保留 root 可读的正式凭据文件。

## 数据流

1. QC 从 Flow 刷新任务。
2. Flow 将无标签任务的四个标签引用字段序列化为 `null`。
3. QC 将旧版空字符串或新版 `null` 统一规范为“没有冻结标签库”。
4. QC 跳过标签库安装，但保留零标注提交边界。
5. QC 领取或恢复任务，解析 `/nas/data_collection/...`。
6. systemd automount 将路径映射到 NAS 共享根。
7. QC 读取 manifest，按 Episode 顺序持续缓存并上报进度。

## 错误与回退

- 部分标签引用：立即失败并保留缓存状态，不降低校验强度。
- NAS DNS、认证、ACL 或网络失败：源目录解析给出明确错误，不创建本地空目录冒充挂载。
- 挂载配置失败：停用并删除本次新增 automount/mount 单元和本机凭据文件；不影响 159。
- 代码回退：Flow 和 QC 修改分别独立提交，可分别回退。旧 Flow 的空字符串仍由新 QC 兼容；新 Flow 的全 `null` 也可被现有无标签语义识别。
- 真实缓存重试不清理失败目录；继续使用现有可恢复状态。

## 测试与验收

### 自动化测试

1. QC 回归：输入与真实 Flow 一致的 `null/null/""/null`，返回未激活且不创建工作区数据库。
2. QC 回归：任一非空字段搭配缺失字段仍拒绝，证明部分引用保护未放宽。
3. QC 回归：完整合法快照仍安装；错误摘要仍拒绝。
4. Flow 回归：无 `label_set_version` 的 `QualityControlJobSerializer` 对四项全部返回 `null`。
5. Flow 回归：有冻结版本时仍返回完整 ID、版本、Schema 和从 Schema 计算的规范摘要。
6. 运行 Episode-QC 相关工作区/Web/平台缓存测试，以及 Episode-Flow 相关 API/标签事实测试。

### 系统验收

1. `systemd-analyze verify` 校验 mount 与 automount 单元。
2. 访问挂载点后，`findmnt -T /nas/data_collection` 必须显示 CIFS 来源 `//delta-ai-nas.local/datasets`。
3. 以 QC 进程用户只读列出目标资产目录，并确认三条 Episode 对应文件可见。
4. 重启本机 QC，使其加载新代码后对 `QCJ-20260813-00001` 执行继续缓存。
5. 确认标签错误消失，且任务至少完成 manifest 枚举；若数据校验本身失败，保留新的精确证据，不把它误报为本次修复成功。
6. 验收期间不删除现有工作区、失败缓存或 NAS 数据。
