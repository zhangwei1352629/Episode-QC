# Episode QC 用户手册

Episode QC 是一套面向 Episode MCAP 数据集的本地 Web 质检与命令行工具。推荐由常驻 Python 服务提供工作区、MCAP 和二进制帧 API，再通过本机浏览器访问；原 Electron 入口暂时保留作为兼容方案。

软件支持递归导入 Episode、相机与 Mocap 同步回放、人工区间/时间点/整条标注、可版本化标签库、质检结论自动保存、撤销/恢复以及 JSONL/CSV 原子导出。源数据始终按只读方式访问，播放缓存和标注结果保存在独立工作区中。

## 1. 功能概览

- 递归发现并索引 `episode_*` 目录中的 MCAP 文件；
- 动态识别相机 Topic，并同步显示多路 JPEG 画面；
- 解析 `mocap_human_motion.raw_v1`，使用宇树官方 G1 29DOF URDF 模型显示可旋转、缩放和选关节的三维姿态；
- 支持区间、时间点和整条 Episode 三种标注范围；
- 支持 YAML、JSON、CSV 标签库的校验、预览、导入和版本管理；
- 自动保存质检员、播放位置、标注和 Episode 结论；
- 支持标注撤销、恢复、软删除及结果批量导出；
- 提供局部画面撕裂、残留区域和光流异常的命令行检测工具；
- 对源 MCAP、元数据和配置快照保持只读。

## 2. 环境要求

- Python：`3.11` 或 `3.12`；
- Node.js：`20` 或更高版本（仅重新构建 Three.js 页面或使用 Electron 时需要）；
- Python 包管理器：`uv`；
- 浏览器：当前主要使用 Chrome/Chromium 验证；
- 桌面环境：Linux 已完成浏览器和 Electron 两种入口验证。

如尚未安装 `uv`：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

安装或更新 Python 环境：

```bash
uv sync --dev
```

如需修改 Three.js 页面或继续使用 Electron，再安装前端依赖：

```bash
npm install
```

## 3. 启动本地 Web 软件（推荐）

运行以下命令会启动只监听 `127.0.0.1` 的 Python 服务、生成随机访问令牌并自动打开浏览器：

```bash
uv run episode-qc web
```

也可以使用启动脚本：

```bash
./scripts/dev-web.sh
```

不自动打开浏览器、指定端口或工作区：

```bash
uv run episode-qc web --no-browser --port 8765
uv run episode-qc web --workspace-root /path/to/workspace
```

启动终端会打印带临时令牌的访问地址。服务默认不接受局域网连接；请勿将令牌链接发送给其他人。关闭浏览器标签页不会立即停止后台缓存，返回启动终端按 `Ctrl+C` 可退出服务。

Web 模式下，“添加数据目录”“导入标签库”和“导出结果”会要求输入服务所在电脑上的绝对路径。MCAP 始终由 Python 后端直接读取，不会上传到浏览器。

### 3.1 Electron 兼容入口

Electron 版本仍可启动：

```bash
npm run dev:electron
```

如果电脑没有可用的系统 Node.js，可使用项目提供的本地 Node.js 启动脚本：

```bash
./scripts/dev-electron.sh
```

建议 Web 和 Electron 同一时间只启动一个入口，避免重复执行缓存任务。

## 4. 数据目录要求

推荐的数据结构如下：

```text
数据根目录/
├── episode_000001/
│   ├── episode.mcap
│   ├── metadata.yaml
│   └── config_snapshot.yaml
├── episode_000002/
│   ├── episode.mcap
│   ├── metadata.yaml
│   └── config_snapshot.yaml
└── ...
```

其中：

- `episode.mcap` 是主要数据文件；
- `metadata.yaml`、`summary.yaml` 或 `episode_summary.yaml` 可作为 Episode 元数据；
- `config_snapshot.yaml` 或 `config.yaml` 可作为采集配置快照；
- 元数据和配置文件可以缺省，但 MCAP 需要带有可读取的 Summary；
- 实际目录和 Topic 匹配规则由 Data Profile 控制，示例见 [`data_profile_v1.example.yaml`](mocap_qc_v1_design_bundle/data_profile_v1.example.yaml)。

## 5. 浏览器与 Electron 使用流程

### 5.1 打开工作区

软件启动时会自动创建或打开默认 SQLite 工作区。默认位置为：

```text
~/.config/episode-qc/workspaces/default/
```

主要内容包括：

```text
workspace.db    标注、进度、标签库和数据源索引
cache/          从源 MCAP 生成的只读播放缓存
```

如需指定其他位置，可在启动前设置：

```bash
export EPISODE_QC_WORKSPACE_ROOT=/path/to/workspace
npm run dev
```

### 5.2 添加数据目录

1. 单击右上角“添加数据目录”；
2. Web 模式输入包含一个或多个 `episode_*` 的绝对路径；Electron 模式使用目录选择器；
3. 软件递归查找 MCAP，并读取每条 Episode 的时长、Topic 和消息数量；
4. 导入结束后，左侧会显示 Episode 总数、已完成数量和导入异常数量；
5. 可通过搜索框和状态下拉框筛选 Episode。

重新添加同一路径会执行重扫。源文件发生变化时，相应播放缓存会标记为需要重新生成。

### 5.3 打开和回放 Episode

单击左侧 Episode 后，软件会先读取元信息。首次打开时优先生成默认头部相机和 Policy 实际执行姿态（`controller_context.body_q`）缓存，让画面尽快可用；其余相机、Mocap、Policy 目标姿态（`final_q_target`）和 SOMA 动作在后台补齐。完整缓存完成后会自动切换，以后再次打开会直接复用。

常用操作：

| 操作 | 鼠标或快捷键 |
|---|---|
| 播放/暂停 | `Space` |
| 上一条 Episode | `P` |
| 下一条 Episode | `N` |
| 前进/后退一帧 | `←` / `→` |
| 前进/后退一秒 | `Shift + ←` / `Shift + →` |
| 设置选区起点 | `I` |
| 设置选区终点 | `O` |
| 放大当前相机 | 双击相机或按 `F` |
| 旋转 Mocap 视角 | 在 G1 机器人区域拖动 |
| 缩放 Mocap 视角 | 在 G1 机器人区域滚轮滚动 |
| 选择标注关节 | 单击 G1 机器人关节 |

播放倍速支持 `0.25×`、`0.5×`、`1×`、`1.5×` 和 `2×`。拖动全局时间轴可直接跳转；勾选“循环选区”后，播放会在选定区间内循环。

三维视图加载项目内置的宇树官方 `g1_29dof.urdf` 及其 STL 网格；模型来源和许可证见 [`SOURCE.md`](app/renderer/assets/unitree-g1-29dof/SOURCE.md)。

### 5.4 导入标签库

工作区没有激活标签库时，软件会自动导入默认示例：

- [`label_schema_v1.example.yaml`](mocap_qc_v1_design_bundle/label_schema_v1.example.yaml)

手动导入步骤：

1. 单击右上角“导入标签库”；
2. Web 模式输入 YAML、JSON 或 CSV 的绝对路径；Electron 模式使用文件选择器；
3. 查看新增、更新、不变和保留标签的预览结果；
4. 确认后导入并激活新版本。

洗衣机任务标签模板提供以下格式：

- [YAML 标签模板](mocap_qc_v1_design_bundle/label_templates/washing_machine_task_qc_v1.yaml)：支持完整结构，可直接导入；
- [JSON 标签模板](mocap_qc_v1_design_bundle/label_templates/washing_machine_task_qc_v1.json)：支持完整结构，可直接导入；
- [CSV 标签模板](mocap_qc_v1_design_bundle/label_templates/washing_machine_task_qc_v1.csv)：适合表格维护，可直接导入；
- [XLSX 编辑模板](mocap_qc_v1_design_bundle/label_templates/washing_machine_task_qc_v1.xlsx)：适合使用 Excel 编辑，当前版本暂不支持直接导入。

洗衣机模板包含以下 9 个标签：

- `extra_foot_adjustment`：脚步调整较多；
- `retry_pick_clothes`：重新拿取衣物；
- `clothes_drop`：衣物掉落；
- `retry_open_door`：重新打开洗衣机门；
- `retry_place_clothes`：重新放入衣物；
- `clothes_caught_in_door`：衣物被门夹住；
- `long_pause_overheat`：过热导致长时间停顿；
- `body_sway`：身体明显晃动；
- `minor_collision`：轻微碰撞。

### 5.5 创建人工标注

1. 在右侧选择标注范围：“区间”“时间点”或“整条”；
2. 区间标注可使用 `I`、`O` 设置起止时间，也可在标注轨道上拖动；
3. 如需指定目标，可单击某一路相机或 Mocap 关节；未选择具体目标时使用全局目标；
4. 可先填写备注，再单击标签；
5. 标注创建后立即写入工作区，无需手动保存；
6. 单击已有标注可修改时间、严重程度、处理建议和备注，或删除该标注。

撤销和恢复：

- 撤销：`Ctrl + Z`；
- 恢复：`Ctrl + Shift + Z`；
- 输入框、文本框或下拉框获得焦点时，普通标签快捷键会暂停响应。

### 5.6 设置 Episode 结论

右下角支持以下结论：

| 快捷键 | 结论 |
|---|---|
| `Alt + 1` | 通过 |
| `Alt + 2` | 有条件通过 |
| `Alt + 3` | 需裁剪 |
| `Alt + 4` | 需修复 |
| `Alt + 5` | 需重采 |
| `Alt + 6` | 废弃 |

也可将 Episode 标记为“待复核”。质检员姓名、播放位置和质量结论都会自动保存。

### 5.7 导出质检结果

1. 根据左侧搜索词和状态筛选需要导出的 Episode；
2. 单击右上角“导出结果”；
3. 选择输出父目录；
4. 软件按“导入数据目录名 + 标注结果标识 + 时间戳”创建目录，并原子写入完整结果。

例如，导入目录为 `20260717_dishwasher_yangqiyao2` 时，导出目录名类似：

```text
20260717_dishwasher_yangqiyao2_qc_annotations_20260801_153012_123456
```

导出目录包含：

```text
annotations.jsonl      标注记录，逐行 JSON
annotations.csv        标注表格
episodes.csv           Episode 状态和质量结论
label_schema.json      当前标签库快照
export_manifest.json   导出版本、数量、筛选条件和文件清单
```

## 6. 命令行画面质检

画面异常检测在桌面端默认禁用，常规启动不会加载 NumPy、Pillow 或检测模块。后续迭代或调试时如需临时启用桌面端入口，可在启动前设置：

```bash
export EPISODE_QC_ENABLE_IMAGE_DETECTION=1
npm run dev
```

命令行检测子命令仍可显式调用，并只在调用时延迟加载检测依赖。

所有命令可通过以下方式查看：

```bash
uv run episode-qc --help
```

### 6.1 查看相机 Topic

```bash
uv run episode-qc topics \
  20260717_dishwasher_yangqiyao2/episode_000050/episode.mcap
```

### 6.2 检测局部残留和画面撕裂

```bash
uv run episode-qc detect-stale-region \
  20260717_dishwasher_yangqiyao2/episode_000050/episode.mcap
```

默认只检查 `/camera/ego_head/image/jpeg`，使用针对头部相机局部撕裂优化的 `camera-tearing` 检测器，并优先解码相机序号或时间间隔异常附近的小窗口。检测结果属于人工复核候选，不应直接当作最终质量结论。

常用参数示例：

```bash
uv run episode-qc detect-stale-region path/to/episode.mcap \
  --topic /camera/ego_head/image/jpeg \
  --threshold 0.72 \
  --tile-size 8 \
  --history-size 3 \
  --min-change 0.08 \
  --max-stale-delta 0.035 \
  --min-rectangularity 0.55 \
  --max-persistence-frames 12 \
  --min-motion-residual 0.018 \
  --gap-window 12 \
  --limit 500 \
  --json stale-region-report.json \
  --export-dir qc-snapshots
```

需要慢速扫描全部帧和全部检测分支时：

```bash
uv run episode-qc detect-stale-region path/to/episode.mcap \
  --detector all \
  --gap-window 0
```

仅在明确需要时扫描全部 JPEG 相机 Topic：

```bash
uv run episode-qc detect-stale-region path/to/episode.mcap --all-topics
```

JSON 报告同时包含：

- `candidates`：逐帧候选；
- `events`：连续异常合并后的事件；
- `event_frame_start`、`event_frame_end`、`event_frame_count`：事件起止帧和连续帧数。

### 6.3 扫描整个数据目录

```bash
uv run episode-qc scan-folder \
  /home/zw/workspace/Episode-QC/20260717_dishwasher_yangqiyao2 \
  --jobs 4 \
  --json folder-qc-report.json \
  --export-dir folder-qc-snapshots
```

文件夹报告会在候选记录中补充 `episode` 和 `mcap_path`，便于按 Episode 汇总。

### 6.4 光流二次验证

对已知帧或播放时间附近执行块光流残差验证：

```bash
uv run episode-qc verify-flow path/to/episode.mcap \
  --elapsed 153.556181911 \
  --window-frames 8 \
  --json flow-report.json \
  --export-dir flow-snapshots
```

当前后端使用无额外模型依赖的 NumPy 块匹配算法，适合对可疑区间执行本地二次检查。

### 6.5 已知正样本

局部画面异常样本：

```bash
uv run episode-qc detect-stale-region \
  20260717_dishwasher_yangqiyao2/episode_000003/episode.mcap \
  --json /tmp/episode000003-qc.json \
  --export-dir /tmp/episode000003-qc-snapshots
```

预期在 `/camera/ego_head/image/jpeg` 的 `2281-2293` 帧附近得到一个连续的 `localized_corruption` 事件。

光流验证样本：

```bash
uv run episode-qc verify-flow \
  /home/zw/Downloads/202060716_wangzhibo/episode_000002/episode.mcap \
  --elapsed 153.556181911 \
  --window-frames 8 \
  --json /tmp/wangzhibo-episode000002-flow.json \
  --export-dir /tmp/wangzhibo-episode000002-flow-snaps
```

该时间约对应 `/camera/ego_head/image/jpeg` 的第 `4601` 帧，预期得到覆盖约 `4594-4608` 帧的 `flow_block_residual` 事件。

### 6.6 参数调整建议

- 普通运动边缘被频繁误报时，提高 `--threshold`；
- JPEG 噪声或细纹理造成碎片区域时，提高 `--tile-size`；
- 残留画面可能延迟多帧时，提高 `--history-size`；
- 微小纹理变化造成误报时，提高 `--min-change`；
- 需要更严格判定“当前帧接近上一帧”时，降低 `--max-stale-delta`；
- 稀疏运动边缘仍占多数时，提高 `--min-rectangularity`；
- 普通相机运动被当成异常时，提高 `--min-motion-residual`；
- 图像左、上、右边界运动误报较多时，提高 `--border-motion-residual-multiplier`；
- 快速相机运动未被局部匹配解释时，提高 `--local-match-radius`；
- 默认 `--gap-window 12` 只解码帧间隔异常附近的数据；如需逐帧检查，设为 `--gap-window 0`；
- 对已知可疑时间优先使用 `verify-flow` 做较慢的二次检查。

## 7. 测试与验证

运行 Python 测试：

```bash
uv run pytest
```

运行桌面端测试：

```bash
npm test
```

运行全部测试：

```bash
./scripts/test-all.sh
```

使用真实数据执行 V1 导入、缓存、标注和导出垂直切片验证：

```bash
uv run python scripts/verify-v1-sample.py \
  20260717_dishwasher_yangqiyao2 \
  --profile mocap_qc_v1_design_bundle/data_profile_v1.example.yaml \
  --labels mocap_qc_v1_design_bundle/label_schema_v1.example.yaml
```

验证脚本在临时目录中创建工作区和播放缓存，结束后自动清理，不修改源数据。

## 8. 常见问题

### 首次打开 Episode 较慢

首次打开需要顺序读取 MCAP 并生成播放缓存，文件越大、相机越多，耗时越长。缓存完成后再次打开会明显加快。请确保工作区磁盘有足够空间。

### 相机画面没有显示

确认 MCAP 中存在压缩 JPEG Topic，并检查 Data Profile 的 `topic_patterns` 是否匹配实际 Topic。

### Mocap 骨架没有显示

当前播放适配器主要支持 `mocap_human_motion.raw_v1` 和 `link_pose_float32`。其他格式会保留在源数据中，但不会自动显示为骨架。

### XLSX 标签模板无法导入

当前版本仅直接导入 YAML、JSON 和 CSV。XLSX 用于 Excel 编辑；编辑完成后请另存为 CSV，或同步修改 YAML/JSON 文件后再导入。

### 软件或电脑出现明显卡顿

避免同时启动多个桌面实例，也不要并行打开多个大型 Episode 生成缓存。若卡顿重复出现，请记录发生时间、正在执行的操作和所选 Episode，以便结合系统日志定位。

### 数据是否会被修改

不会。Episode QC 对 MCAP、元数据和采集配置按只读方式访问。所有索引、缓存、标注、进度和导出记录都写入工作区或用户选择的导出目录。
