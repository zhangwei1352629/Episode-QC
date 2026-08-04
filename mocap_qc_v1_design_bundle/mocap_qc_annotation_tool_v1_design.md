# Mocap 数据人工质检与标注工具 V1 设计文档

> 文档版本：V1.0  
> 文档状态：可进入第一版开发  
> 目标平台：Ubuntu 本地工作站  
> 产品形态：本地桌面应用  
> 核心原则：人工检查为主、源数据只读、按文件夹批量导入、相机动态适配、标签可自定义导入、标签结果可结构化导出

---

## 1. 文档目的

本文档用于指导第一版 Mocap 数据人工质检与标注工具的产品、前端、后端和测试实现，覆盖：

- 产品范围与非目标；
- 数据目录与 Episode 识别；
- MCAP 数据索引；
- 动态相机发现与同步播放；
- Mocap 骨架回放；
- 人工区间标注；
- 自定义标签库导入；
- 质检结论与进度管理；
- 标签导出；
- 数据库、接口、缓存与错误处理；
- 第一版实施顺序和验收标准。

本文档优先服务于“先做出能稳定使用的第一版”，暂不追求复杂自动质量评分、AI 异常识别和云端多人协作。

---

## 2. 背景与已确认需求

### 2.1 使用场景

采集数据以文件夹和 Episode 形式存储，每个 Episode 中包含 MCAP、配置快照和可能存在的汇总文件。MCAP 内可同时包含：

- Mocap 人体动作；
- 重定向结果；
- 策略输入、观测和动作；
- 机器人或灵巧手命令、状态；
- 头部、胸部、腕部、全景等相机数据；
- 延迟诊断数据。

质检人员需要连续检查大量 Episode，主要通过同步观看 Mocap 骨架与相机画面判断数据是否可用，并对整条数据或具体时间区间添加标签。

### 2.2 已确认的产品要求

第一版必须支持：

1. 选择一个文件夹，递归导入其中多条 Episode 数据；
2. 一次导入的数据可以来自多个数据组、日期、采集员或任务目录；
3. 以人工检查为主要工作方式；
4. 同步查看 Mocap 与多个相机；
5. 相机数量和相机名称不固定，界面必须动态适配；
6. 支持整条 Episode、时间区间和时间点标签；
7. 支持对相机、关节、数据流或全局对象打标签；
8. 标签体系支持从外部文件自定义导入；
9. 支持标签库版本、导入预览和冲突校验；
10. 支持自动保存、恢复质检进度；
11. 支持导出标签、Episode 结论和标签定义快照；
12. 原始 MCAP 和数据目录默认只读，不在源目录中生成中间文件。

### 2.3 当前样例数据带来的设计约束

用户提供的 Episode 汇总样例具有以下特点：

- Episode 时长约 156.93 秒；
- `mocap_human_motion` 写入 15,691 条，按 Episode 时长计算约 99.99 Hz；
- 多个 G1 策略数据流写入 7,801 条，约 49.71 Hz；
- `soma_retarget_action` 写入 7,846 条，约 50.00 Hz；
- `inspire_command` 写入 6,550 条，约 41.74 Hz；
- 头部和胸部相机各写入 4,703 帧，约 29.97 Hz；
- `robot_state`、腕部相机、Pico 等流在该 Episode 中可能为 0；
- `task_description` 可能为空；
- 汇总文件中同时存在 Episode 写入数量和采集进程累计统计。

因此，第一版应遵循：

- 相机窗口只根据当前 Episode 实际存在的数据流生成；
- 数据流为 0 不等于错误，是否必需由数据 Profile 决定；
- 汇总文件用于快速预览，但最终流数量、时间范围和可播放性必须以 MCAP 当前 Episode 内容为准；
- 不能直接用进程累计的 `total_messages`、`sequence_gap_count` 给当前 Episode 下结论；
- 不应继续只保存无法解释的 `quality_grade: medium`，人工结论和标签必须结构化保存。

---

## 3. 产品定位

### 3.1 产品名称

暂定：`Mocap QC Annotator`

中文名称：**Mocap 数据人工质检与标注工具**

### 3.2 产品定位

本工具是一个运行在本地工作站上的批量人工质检桌面应用，负责：

```text
文件夹导入
  → Episode 索引
  → 多模态同步回放
  → 人工区间标注
  → Episode 质检结论
  → 标签结果导出
```

它不是：

- 数据采集软件；
- Mocap 自动修复工具；
- 训练数据管理全平台；
- 自动质量评分系统；
- 云端多人标注平台。

### 3.3 第一版核心价值

第一版需要解决的不是“自动判断所有问题”，而是：

> 让质检员更快地浏览多条数据，更准确地定位问题，并稳定输出结构化标签。

---

## 4. 第一版范围

### 4.1 V1 必须实现

- 本地工作区创建与打开；
- 文件夹递归扫描；
- 多 Episode 导入；
- MCAP Topic、时间范围和消息数量索引；
- Episode 列表、筛选和排序；
- 动态相机发现与多相机布局；
- JPEG 相机帧同步播放；
- Mocap 骨架 3D 播放；
- 全局时间轴；
- 播放、暂停、跳转、逐帧、倍速；
- 整条、区间、时间点标签；
- 相机、关节、数据流和全局目标标签；
- YAML、JSON、CSV 标签库导入；
- 标签导入预览和冲突处理；
- 标签搜索、分组、快捷键；
- 质检工作状态和质量结论；
- 自动保存与崩溃恢复；
- 每个任务导出单个 JSON 或 CSV 结果文件；
- JSON 结果内嵌标签定义快照和导出元数据；
- 安装包或可重复启动脚本。

### 4.2 V1 可选但建议实现

- 文件夹增量重扫；
- 未知 Topic 原始信息查看；
- 数据流时间覆盖轨道；
- 简单缺流、时间戳异常提示；
- 当前 Episode 书签；
- 最近打开工作区；
- 导出筛选条件保存。

### 4.3 V1 明确不做

- AI 动作异常识别；
- 自动通过或拒绝；
- 自动裁剪 MCAP；
- 自动修复骨架或视频；
- 多人实时协作和冲突合并；
- 登录、组织和权限系统；
- 云端上传和远程存储；
- 复杂统计看板；
- 训练集版本发布；
- Foxglove 插件集成；
- 全量动力学、碰撞和脚滑分析。

---

## 5. 用户与角色

### 5.1 V1 用户角色

V1 为本地单用户应用，不实现账号系统，但在设置中保存“当前质检员名称”。

主要角色：

- **质检员**：导入数据、播放、打标签、提交结论、导出结果；
- **标签管理员**：维护并导入标签库。V1 中可以是同一人。

### 5.2 核心用户任务

1. 选择一个数据根目录；
2. 查看工具识别出的所有 Episode；
3. 按未质检、采集员、日期、标签等筛选；
4. 打开一条 Episode；
5. 同步观看 Mocap 和所有有效相机；
6. 在异常开始和结束位置创建标签；
7. 选择异常对象、严重程度和处理建议；
8. 给整条 Episode 选择结论；
9. 自动进入下一条；
10. 批量导出标签结果。

---

## 6. 总体业务流程

```text
创建/打开工作区
      ↓
选择数据根目录
      ↓
递归扫描并发现 Episode
      ↓
快速读取汇总与 MCAP 元信息
      ↓
Episode 列表可见
      ↓
打开某个 Episode
      ↓
按需建立播放缓存与时间索引
      ↓
同步播放 Mocap 与动态相机
      ↓
人工创建、修改、删除标签
      ↓
选择 Episode 质量结论
      ↓
自动保存并进入下一条
      ↓
按范围导出标签和结论
```

---

## 7. 数据目录与导入设计

### 7.1 推荐目录结构

```text
dataset_root/
├── 20260716_wangjiaxu/
│   ├── episode_000001/
│   │   ├── episode.mcap
│   │   ├── config_snapshot.yaml
│   │   └── summary.yaml
│   ├── episode_000002/
│   │   ├── episode.mcap
│   │   ├── config_snapshot.yaml
│   │   └── summary.yaml
│   └── episode_000003/
├── 20260716_chenwenshuo/
│   ├── episode_000001/
│   └── episode_000002/
└── 20260717_wangjiaxu/
```

工具不依赖固定的上层目录命名，只要求能够识别 Episode 目录。

### 7.2 Episode 识别规则

默认识别规则：

- 目录中存在 `episode.mcap`；或
- 目录名符合 `episode_*` 且内部存在一个 `.mcap` 文件。

可通过数据 Profile 配置：

```yaml
import:
  episode_directory_patterns:
    - "episode_*"
    - "record_*"

  mcap_file_patterns:
    - "episode.mcap"
    - "*.mcap"

  sidecar_files:
    summary:
      - "summary.yaml"
      - "episode_summary.yaml"
    config:
      - "config_snapshot.yaml"
      - "config.yaml"
```

### 7.3 导入模式

支持：

- 导入单个 Episode 文件夹；
- 导入某个数据组文件夹；
- 导入数据集根目录；
- 后续对已导入根目录执行增量重扫。

### 7.4 导入阶段

#### 阶段 A：目录发现

只遍历文件系统并识别候选 Episode，不读取完整 MCAP。

输出：

- 候选 Episode 数量；
- MCAP 文件路径；
- 旁路文件路径；
- 文件大小、修改时间；
- 相对路径。

#### 阶段 B：快速元信息索引

优先读取 `summary.yaml`；随后读取 MCAP Summary/Channel 信息进行校验。

输出：

- 起止时间；
- 时长；
- Topic 列表；
- 编码；
- 消息数量；
- 可能的相机流；
- 可能的 Mocap 流；
- 导入状态。

#### 阶段 C：播放缓存准备

只在首次打开 Episode 时按需执行，建立：

- 相机 JPEG 帧索引；
- Mocap 帧索引；
- 时间轴覆盖信息；
- 必要的缩略图或解码缓存。

### 7.5 增量重扫

重扫时根据 Episode 指纹判断：

- 新增；
- 未变化；
- 文件已修改；
- 文件已移动；
- 源文件丢失。

推荐指纹：

```text
hash(
  root_source_id,
  relative_path,
  mcap_file_size,
  mcap_mtime_ns,
  mcap_start_time_ns,
  mcap_end_time_ns
)
```

V1 不要求计算整个 MCAP 的内容哈希，避免大文件扫描过慢。

### 7.6 重复数据处理

如果同一 MCAP 被重复导入：

- 同一根目录和相对路径：更新原记录；
- 不同路径但指纹一致：提示“可能重复”，默认保留两条独立来源；
- 已有标签不自动覆盖或删除。

### 7.7 源数据保护

默认规则：

- 不修改源 MCAP；
- 不修改原有 YAML；
- 不在 Episode 文件夹中创建缓存；
- 标签和缓存存放在应用工作区；
- 只有用户明确选择“复制标注到源目录”时才写入源目录，该功能不作为 V1 必需功能。

---

## 8. 工作区设计

### 8.1 工作区内容

一个工作区代表一组导入来源、质检进度、标签库和导出设置。

```text
workspace/
├── workspace.db
├── workspace.json
├── cache/
│   └── episodes/
├── imported_schemas/
├── exports/
└── logs/
```

实际默认路径建议：

```text
~/.local/share/mocap-qc/workspaces/<workspace-id>/
```

### 8.2 工作区字段

- 工作区名称；
- 当前质检员；
- 数据源根目录列表；
- 当前激活标签库；
- 最近打开 Episode；
- 播放速度；
- 视图布局；
- 快捷键设置；
- 导出默认目录。

---

## 9. 信息架构与页面

V1 包含四个主要页面：

1. **工作区首页**；
2. **人工质检工作台**；
3. **标签库管理**；
4. **设置与导出**。

### 9.1 工作区首页

功能：

- 新建工作区；
- 打开最近工作区；
- 添加数据根目录；
- 查看导入进度；
- 查看 Episode 统计；
- 进入质检工作台。

### 9.2 人工质检工作台

这是 V1 核心页面，播放和标注必须在同一页面完成。

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ 工作区 / 数据源 / 进度  上一条  下一条  播放  倍速  当前时间  自动保存状态 │
├──────────────────┬───────────────────────────────────────────────────────┤
│ Episode 列表      │ 主查看区                                               │
│ 搜索/筛选/排序    │ ┌─────────────────────┬───────────────────────────┐   │
│                  │ │ Mocap 3D            │ 动态相机区域               │   │
│ 未质检           │ │                     │ 1/2/4/多路自适应           │   │
│ 质检中           │ └─────────────────────┴───────────────────────────┘   │
│ 已完成           │                                                       │
│ 待复核           │ 数据流开关 / 相机选择 / 关节信息                       │
├──────────────────┴───────────────────────────────────────────────────────┤
│ 全局时间轴：流覆盖、当前时间、区间选择、标签轨道、缩放                     │
├──────────────────────────────────────────────────────────────────────────┤
│ 标签面板：常用 / 搜索 / 分组 / 属性 / 严重度 / 处理建议 / 备注             │
├──────────────────────────────────────────────────────────────────────────┤
│ Episode 结论：通过 / 有条件通过 / 需裁剪 / 需修复 / 需重采 / 废弃          │
└──────────────────────────────────────────────────────────────────────────┘
```

### 9.3 标签库管理

- 导入标签库；
- 下载模板；
- 导入预览；
- 查看冲突；
- 启用/停用标签；
- 查看标签库版本；
- 导出当前标签库；
- 绑定数据 Profile。

### 9.4 设置与导出

- 质检员名称；
- 工作区路径；
- 缓存上限；
- 默认播放速度；
- 快捷键；
- 数据 Profile；
- 导出范围、格式和目录。

---

## 10. Episode 列表设计

### 10.1 显示字段

| 字段 | 说明 |
|---|---|
| Episode | 目录名或元数据名称 |
| 数据组 | Episode 上一级数据目录 |
| 相对路径 | 相对数据源根目录的路径 |
| 时长 | MCAP 时间范围 |
| 相机数量 | 当前 Episode 有实际帧的相机数 |
| Mocap 状态 | 可解析、缺失、未知格式 |
| 导入状态 | 已发现、索引中、就绪、失败 |
| 质检状态 | 未开始、质检中、已完成、待复核 |
| 质量结论 | 通过、有条件通过、需裁剪、需修复、需重采、废弃 |
| 标签数 | 已确认标签数量 |
| 质检员 | 当前记录的质检人员 |
| 更新时间 | 最近标注或结论更新时间 |

### 10.2 筛选条件

- 数据源；
- 数据组；
- 日期；
- Episode 名称；
- 未质检/已质检；
- 质量结论；
- 标签编码或名称；
- 相机数量；
- 缺少 Mocap；
- 导入失败；
- 当前质检员。

### 10.3 排序

- 文件夹顺序；
- Episode 编号；
- 开始时间；
- 时长；
- 标签数量；
- 最近更新时间。

### 10.4 切换行为

切换 Episode 前：

1. 提交内存中尚未落库的更改；
2. 保存当前播放位置；
3. 保存 3D 视角和相机布局；
4. 加载下一条 Episode；
5. 恢复该 Episode 上次播放位置。

---

## 11. 播放控制设计

### 11.1 全局播放时钟

所有数据流都使用 Episode 相对时间：

```text
episode_time_ns = message_timestamp_ns - episode_start_time_ns
```

前端维护唯一的全局播放时钟，所有相机、Mocap 和时间轴都根据该时钟取最近帧。

### 11.2 播放功能

- 播放/暂停；
- 拖动跳转；
- 时间点点击跳转；
- 上一帧/下一帧；
- 向前/向后 1 秒；
- 播放速度：0.25×、0.5×、1×、1.5×、2×；
- 跳转到标签开始或结束；
- 循环播放当前选区；
- 切换 Episode 后保持默认倍速。

### 11.3 帧选择规则

对于给定播放时间 `t`：

```text
frame = argmin(abs(frame_timestamp - t))
```

同时配置最大允许偏差 `max_frame_skew_ms`。超过偏差时：

- 保留上一帧或显示“无同步帧”；
- 在相机角标显示实际偏差；
- 不自动将其判为异常。

### 11.4 播放结束行为

可配置：

- 停在末尾；
- 循环当前 Episode；
- 自动打开下一条未质检 Episode。

默认：停在末尾，不自动提交质量结论。

---

## 12. 动态相机设计

### 12.1 相机发现

相机流通过以下信息综合识别：

- Topic 路径匹配；
- MCAP 编码为 JPEG 或图片消息；
- Summary 中的 `camera_name`；
- 数据 Profile 中的流映射。

默认匹配：

```yaml
stream_mapping:
  camera:
    topic_patterns:
      - "/camera/*/image/jpeg"
      - "/camera/*/image/raw"
      - "*image_jpeg*"
      - "*camera*image*"
```

### 12.2 有效相机定义

当前 Episode 中满足以下条件时，生成相机窗口：

- MCAP 中存在对应 Topic；
- 当前 Episode 消息数量大于 0；
- 消息可解析或至少可识别编码。

配置中存在但当前 Episode 为 0 的相机，只在“数据流状态”中显示，不占用播放窗口。

### 12.3 相机名称

优先级：

1. 数据 Profile 显式名称；
2. Summary 的 `camera_name`；
3. Topic 中间段；
4. 原始 Topic。

例如：

```text
/camera/ego_head/image/jpeg → ego_head
/camera/ego_chest/image/jpeg → ego_chest
```

### 12.4 动态布局

- 0 路：主区域由 Mocap 3D 占满，并提示“当前 Episode 无有效相机”；
- 1 路：单画面；
- 2 路：左右布局；
- 3–4 路：2×2；
- 5–6 路：3×2；
- 7 路以上：默认 2×2 主窗口 + 相机切换栏，可切换自定义网格。

每个相机支持：

- 设为主相机；
- 全屏；
- 隐藏；
- 显示 Topic、帧时间和同步偏差；
- 对当前相机创建标签；
- 回到自适应布局。

### 12.5 相机帧缓存

V1 不将所有图片解码并驻留内存。

首次打开 Episode 时：

1. 扫描相机消息；
2. 将原始 JPEG 字节连续写入缓存文件；
3. 保存 `(timestamp_ns, offset, length)` 索引；
4. 播放时按时间二分查找并读取对应 JPEG；
5. 前端仅保留当前帧和小范围预取缓存。

推荐缓存结构：

```text
cache/episodes/<episode-id>/
├── stream_index.json
├── cameras/
│   ├── ego_head.frames
│   ├── ego_head.index.npy
│   ├── ego_chest.frames
│   └── ego_chest.index.npy
└── mocap/
```

该方式不重新编码 JPEG，减少准备时间和质量损失。

---

## 13. Mocap 3D 查看设计

### 13.1 统一帧模型

后端将不同 Mocap 消息转换为统一结构：

```python
MotionFrame:
    timestamp_ns: int
    joint_names: list[str]
    parent_indices: list[int]
    positions: float32[J, 3] | None
    rotations: float32[J, 4] | None
    validity: bool[J] | None
    confidence: float32[J] | None
    coordinate_frame: str | None
    units: str | None
```

### 13.2 V1 显示能力

- 关节球和骨骼连线；
- 地面网格；
- 坐标轴；
- 旋转、缩放、平移；
- 显示/隐藏关节名；
- 点击选择关节；
- 当前选中关节高亮；
- 创建标签时自动带入所选关节；
- 轨迹尾迹可开关；
- 异常标签区间内目标关节高亮。

### 13.3 多骨架叠加

如果数据 Profile 定义了解析器，V1 可选择显示：

- 人体原始 Mocap；
- SOMA 重定向目标；
- 策略动作姿态；
- 机器人状态。

每个图层可独立显示/隐藏。没有数据时不显示空图层。

### 13.4 未知 Mocap 消息格式

当前汇总样例只包含 Topic、编码、频率和数量，没有提供 `mocap_human_motion` 单条消息的字段结构。因此：

- 系统必须使用可插拔 Motion Adapter；
- 未识别格式时，Episode 仍可播放相机和打全局/相机标签；
- Mocap 区域显示“消息格式未适配”；
- 后续只需新增 Adapter，不修改主界面和标注模型。

第一版开始开发前，需要准备至少一条解码后的 Mocap 消息样例，用于实现首个 Adapter。

---

## 14. 数据 Profile 设计

数据 Profile 用来描述某类数据的目录、Topic 和消息适配方式，不负责定义质检标签。

### 14.1 示例

```yaml
profile:
  id: g1_soma_inspire_v1
  name: G1 + SOMA + Inspire
  version: "1.0.0"

import:
  episode_directory_patterns:
    - "episode_*"
  mcap_file_patterns:
    - "episode.mcap"

streams:
  - key: human_motion
    type: mocap
    topic_patterns:
      - "/mocap/human_motion"
    encoding: json
    adapter: human_motion_json_v1
    required: true

  - key: soma_retarget
    type: retarget
    topic_patterns:
      - "/soma/retarget/action"
    encoding: msgpack
    adapter: soma_retarget_msgpack_v1
    required: false

  - key: camera
    type: camera
    topic_patterns:
      - "/camera/*/image/jpeg"
    encoding: jpeg
    dynamic: true
    required: false

  - key: inspire_command
    type: hand
    topic_patterns:
      - "/inspire/command"
    encoding: json
    required: false

playback:
  master_clock: episode
  default_rate: 1.0
  max_frame_skew_ms:
    camera: 100
    mocap: 30
```

### 14.2 Profile 与实际数据的关系

Profile 只描述“预期”，实际播放窗口和可用图层仍以 MCAP 当前 Episode 数据为准。

---

## 15. 时间轴设计

### 15.1 时间轴轨道

建议至少包括：

- Mocap 数据覆盖；
- 每个相机覆盖；
- 重定向/机器人/手部等可选数据流覆盖；
- 标签轨道；
- 当前选区；
- 播放头。

### 15.2 核心交互

- 点击跳转；
- 拖动播放头；
- 滚轮缩放；
- Shift + 滚轮横向移动；
- 鼠标拖动选择时间区间；
- 拖动区间两端修改范围；
- 点击标签打开详情；
- 双击空白区域创建时间点标签；
- 开启“循环选区”；
- 标签重叠时按轨道堆叠显示。

### 15.3 时间精度

数据库统一使用纳秒整数。界面默认显示秒并保留 3 位小数。

```text
start_offset_ns
end_offset_ns
start_sec = start_offset_ns / 1e9
```

### 15.4 区间边界

- `start_offset_ns >= 0`；
- `end_offset_ns <= episode_duration_ns`；
- 时间点标签允许 `start == end`；
- 区间标签要求 `end > start`；
- 标签可以重叠；
- 同标签、同目标、同区间不会自动合并，避免破坏人工意图。

---

## 16. 标签体系设计

### 16.1 标签库概念

标签不是写死在代码中的枚举，而是可导入、可版本化的 Label Schema。

层级：

```text
标签库
  ├── 严重程度定义
  ├── 处理建议定义
  ├── 标签分组
  └── 标签定义
```

### 16.2 V1 支持的标签范围

`annotation_scopes`：

- `episode`：整条数据；
- `time_range`：时间区间；
- `time_point`：单个时间点。

`target_types`：

- `global`；
- `mocap`；
- `joint`；
- `camera`；
- `stream`；
- `retarget`；
- `robot`；
- `hand`。

### 16.3 标签字段

| 字段 | 必填 | 说明 |
|---|---:|---|
| `code` | 是 | 稳定唯一编码 |
| `name` | 是 | 显示名称 |
| `group` | 是 | 标签分组编码 |
| `description` | 否 | 定义和使用说明 |
| `enabled` | 否 | 默认 true |
| `annotation_scopes` | 是 | 可用于哪些标注范围 |
| `target_types` | 是 | 可用于哪些目标 |
| `default_severity` | 否 | 默认严重程度 |
| `default_action` | 否 | 默认处理建议 |
| `shortcut` | 否 | 快捷键 |
| `color` | 否 | 时间轴颜色 |
| `applicable_profiles` | 否 | 适用数据 Profile |
| `fields` | 否 | 自定义附加字段 |

### 16.4 标签编码规则

```regex
^[a-z][a-z0-9_]{2,63}$
```

例如：

```text
mocap_joint_jitter
camera_blur
task_incomplete
robot_not_following
```

标签一旦被正式标注使用，`code` 不允许直接修改；只能停用或由新标签替代。

### 16.5 严重程度

标签库可自定义严重程度。默认提供：

```text
minor      轻微
normal     一般
critical   严重
```

### 16.6 处理建议

默认提供：

```text
keep               保留
keep_with_label    保留但标记
trim               裁剪区间
repair             需要修复
recollect          需要重采
reject             整条废弃
review             待复核
```

### 16.7 自定义字段

V1 支持：

- `text`；
- `textarea`；
- `number`；
- `boolean`；
- `select`；
- `multi_select`；
- `joint_selector`；
- `camera_selector`；
- `stream_selector`。

示例：

```yaml
fields:
  - code: affected_joint
    name: 异常关节
    type: joint_selector
    required: true
    multiple: true

  - code: reason
    name: 原因
    type: select
    required: false
    options:
      - code: device_debug
        name: 设备调试
      - code: tracking_loss
        name: 追踪丢失
      - code: unknown
        name: 未知
```

---

## 17. 自定义标签导入

### 17.1 V1 支持格式

- YAML：推荐，支持完整结构；
- JSON：支持完整结构；
- CSV：适合通过表格维护标签列表。

XLSX 放入第二版，避免第一版同时维护复杂工作表解析。

### 17.2 YAML 示例

```yaml
schema:
  schema_type: annotation_label_schema
  schema_version: "1.0.0"
  label_set_id: mocap_qc_default
  label_set_name: Mocap 人工质检标签库
  language: zh-CN

severity_levels:
  - code: minor
    name: 轻微
    order: 1
  - code: normal
    name: 一般
    order: 2
  - code: critical
    name: 严重
    order: 3

actions:
  - code: keep
    name: 保留
  - code: trim
    name: 裁剪区间
  - code: recollect
    name: 需要重采
  - code: reject
    name: 整条废弃

groups:
  - code: mocap
    name: Mocap 问题
    order: 1
  - code: camera
    name: 相机问题
    order: 2
  - code: collection
    name: 采集过程问题
    order: 3

labels:
  - code: mocap_joint_jitter
    name: 关节抖动
    group: mocap
    description: 单个或多个关节出现明显高频抖动
    enabled: true
    annotation_scopes:
      - time_range
      - time_point
    target_types:
      - mocap
      - joint
    default_severity: normal
    default_action: trim
    shortcut: Q
    color: "#F59E0B"
    fields:
      - code: affected_joint
        name: 异常关节
        type: joint_selector
        required: true
        multiple: true

  - code: camera_blur
    name: 画面模糊
    group: camera
    description: 相机画面无法清晰识别动作或目标
    enabled: true
    annotation_scopes:
      - episode
      - time_range
    target_types:
      - camera
    default_severity: normal
    default_action: keep
    shortcut: W
    color: "#8B5CF6"
```

### 17.3 CSV 字段

```csv
code,name,group,description,enabled,annotation_scopes,target_types,default_severity,default_action,shortcut,color,applicable_profiles
mocap_joint_jitter,关节抖动,mocap,关节出现明显高频抖动,true,time_range|time_point,mocap|joint,normal,trim,Q,#F59E0B,g1_soma_inspire_v1
camera_blur,画面模糊,camera,画面无法清晰识别,true,episode|time_range,camera,normal,keep,W,#8B5CF6,
```

CSV 多值字段用 `|` 分隔。V1 的 CSV 不支持复杂 `fields`，复杂标签应使用 YAML 或 JSON。

### 17.4 导入流程

```text
选择标签文件
   ↓
解析格式
   ↓
结构与引用校验
   ↓
计算新增、更新、无变化和冲突
   ↓
展示导入预览
   ↓
选择冲突策略
   ↓
确认导入
   ↓
生成新标签库版本
```

### 17.5 校验项

- 标签库 ID 和版本存在；
- 标签编码格式正确；
- 标签编码不重复；
- 分组存在；
- 严重程度存在；
- 处理建议存在；
- `annotation_scopes` 合法；
- `target_types` 合法；
- 快捷键未与保留快捷键冲突；
- 标签间快捷键不冲突；
- 颜色为合法十六进制；
- 自定义字段编码不重复；
- `select` 选项完整；
- Profile 引用存在或允许延后绑定。

### 17.6 冲突策略

对于相同 `label_set_id + label code`：

- 更新现有标签；
- 跳过；
- 只导入新增标签；
- 复制成新的标签库 ID。

默认：

```text
相同 code → 预览差异后更新
新 code   → 新增
导入文件中缺失的旧标签 → 保留，不删除
```

### 17.7 已使用标签保护

已经存在标注记录的标签：

- 可以修改名称、描述、颜色、默认严重程度、默认处理建议和快捷键；
- 不允许直接修改 `code`；
- 不允许物理删除；
- 可以停用；
- 可以指定替代标签。

### 17.8 标签库版本

采用语义化版本：

- 描述、颜色修改：补丁版本；
- 新增标签：次版本；
- 语义或结构变化：主版本。

每个标注保存创建时使用的标签库 ID 和版本。

---

## 18. 人工标注交互

### 18.1 创建区间标签

1. 在时间轴拖选区间；
2. 点击标签或按标签快捷键；
3. 系统根据当前上下文自动选择目标：
   - 当前选中相机 → camera；
   - 当前选中关节 → joint；
   - 否则 → global；
4. 展示严重程度、处理建议和自定义字段；
5. 保存；
6. 标签立即显示在时间轴并自动落库。

### 18.2 创建整条标签

在标签面板切换到“整条 Episode”，选择支持 `episode` 范围的标签。

### 18.3 创建时间点标签

- 双击时间轴；或
- 当前播放位置按快捷键；
- 保存 `start_offset_ns == end_offset_ns`。

### 18.4 修改标签

- 拖动区间左右边界；
- 在详情面板修改标签、目标、严重程度、处理建议、备注和字段；
- 删除；
- 复制到当前时间；
- 跳转到上一/下一标签。

### 18.5 自动保存

以下操作必须自动保存：

- 新增标签；
- 修改标签；
- 删除标签；
- 修改 Episode 结论；
- 修改工作状态；
- 切换 Episode；
- 关闭窗口。

界面显示：

```text
已保存
保存中…
保存失败，正在重试
```

### 18.6 撤销与恢复

V1 支持当前会话内：

- 撤销最近一次标签新增、修改或删除；
- 恢复；
- 最少保留 50 步历史。

撤销操作完成后同样写入数据库。

---

## 19. 快捷键设计

### 19.1 系统保留快捷键

| 快捷键 | 功能 |
|---|---|
| Space | 播放/暂停 |
| ← / → | 上一帧/下一帧 |
| Shift + ← / → | 后退/前进 1 秒 |
| I | 设置区间起点 |
| O | 设置区间终点 |
| Enter | 打开标签选择或确认 |
| Esc | 取消选区/关闭轻面板 |
| Ctrl + Z | 撤销 |
| Ctrl + Shift + Z | 恢复 |
| N | 下一条 Episode |
| P | 上一条 Episode |
| F | 当前相机全屏 |
| Ctrl + S | 立即保存 |
| Ctrl + F | 搜索标签 |

### 19.2 标签快捷键

标签库可配置单键或组合键，但不能占用系统保留快捷键。输入备注文本时，标签快捷键自动暂停响应。

### 19.3 Episode 结论快捷键

建议默认：

| 快捷键 | 结论 |
|---|---|
| Alt + 1 | 通过 |
| Alt + 2 | 有条件通过 |
| Alt + 3 | 需裁剪 |
| Alt + 4 | 需修复 |
| Alt + 5 | 需重采 |
| Alt + 6 | 废弃 |

---

## 20. 质检状态与结论

### 20.1 工作状态

```text
unreviewed       未开始
in_progress      质检中
completed        已完成
needs_recheck    待复核
reviewed         已复核
```

状态规则：

- 首次打开且发生播放或标注后，自动变为 `in_progress`；
- 提交质量结论时可变为 `completed`；
- 用户可手动设为 `needs_recheck`；
- V1 不强制必须存在标签才能完成。

### 20.2 质量结论

```text
pass                 通过
pass_with_labels     有条件通过
trim                 需裁剪
repair               需修复
recollect            需重采
reject               废弃
```

结论与标签相互独立：

- 一个 Episode 可以有标签但仍然通过；
- 一个 Episode 可以没有区间标签但因整体任务错误而废弃；
- 工具不根据标签自动替用户选择结论。

### 20.3 完成校验

提交完成前可提示但不强制：

- 是否选择质量结论；
- 是否存在未填写的必填标签字段；
- 是否存在仍处于草稿状态的区间；
- 是否存在未保存错误。

---

## 21. 标签数据模型

### 21.1 标注对象

```json
{
  "annotation_id": "ann_01J...",
  "episode_id": "ep_01J...",
  "label_set_id": "mocap_qc_default",
  "label_schema_version": "1.1.0",
  "label_code": "mocap_joint_jitter",
  "scope": "time_range",
  "start_offset_ns": 32180000000,
  "end_offset_ns": 34720000000,
  "target_type": "joint",
  "target_key": "right_wrist",
  "severity": "normal",
  "action": "trim",
  "comment": "右手腕连续抖动",
  "attributes": {
    "affected_joint": ["right_wrist"]
  },
  "source": "manual",
  "status": "confirmed",
  "reviewer": "reviewer_01",
  "created_at": "2026-08-01T00:00:00+09:00",
  "updated_at": "2026-08-01T00:01:00+09:00"
}
```

### 21.2 时间存储

数据库以 Episode 相对纳秒为主，导出时计算绝对时间：

```text
absolute_start_time_ns = episode.start_time_ns + start_offset_ns
absolute_end_time_ns   = episode.start_time_ns + end_offset_ns
```

这样既方便时间轴操作，也能和 MCAP 原始时间精确对齐。

---

## 22. 标签导出设计

### 22.1 导出范围

支持：

- 当前 Episode；
- 当前数据组；
- 当前数据源；
- 全部已完成 Episode；
- 当前筛选结果；
- 指定质量结论；
- 指定标签；
- 指定时间范围内采集的数据。

### 22.2 V1 导出格式

- JSON：单个文件同时包含任务元数据、Episode 结论、标注明细和标签定义快照；
- CSV：单个文件将 Episode 结论和标注明细展开到同一张表；
- 每个任务每次只选择一种格式，不再同时生成多份内容重复的文件。

### 22.3 导出文件命名

```text
<任务目录名>_标注结果.json
<任务目录名>_标注结果.csv
```

同一任务以相同格式重复导出时原子更新原文件，不添加时间戳，也不创建额外结果目录。

### 22.4 CSV 字段

```text
task_name
exported_at
episode_id
source_root
relative_episode_path
episode_name
data_group
start_time_ns
end_time_ns
duration_sec
camera_count
mocap_available
import_status
review_status
quality_decision
annotation_count
reviewer
reviewed_at
source_fingerprint
annotation_id
label_set_id
label_schema_version
label_code
label_name
scope
start_offset_ns
end_offset_ns
start_sec
end_sec
absolute_start_time_ns
absolute_end_time_ns
target_type
target_key
severity
action
comment
attributes_json
annotation_reviewer
annotation_created_at
annotation_updated_at
```

一个 Episode 有多条标注时对应多行；没有标注时仍保留一行，标注字段为空。

### 22.5 JSON 结构

```json
{
  "export_version": "2.0.0",
  "application_version": "1.0.0",
  "workspace_id": "ws_01J...",
  "task_name": "20260717_dishwasher_yangqiyao2",
  "label_set_id": "mocap_qc_default",
  "label_schema_version": "1.1.0",
  "exported_at": "2026-08-01T00:15:00+09:00",
  "filters": {
    "review_status": ["completed"],
    "quality_decision": []
  },
  "episode_count": 120,
  "annotation_count": 387,
  "format": "json",
  "label_schema": {},
  "episodes": [],
  "annotations": []
}
```

### 22.6 导出稳定性

- 使用稳定字段名；
- 任何新增字段不破坏已有字段；
- 导出结果必须携带 schema/version；
- 导出过程先写同目录临时文件，全部成功后原子替换正式文件；
- 导出失败不产生半成品正式文件。

---

## 23. 技术架构

### 23.1 V1 推荐技术选型

#### 前端

- Chrome / Chromium；
- 浏览器原生 JavaScript ES Module；
- HTML + CSS；
- Three.js：Mocap 3D；
- Canvas：时间轴与姿态辅助绘制。

#### 后端

- Python；
- 本机 HTTP、SSE 与二进制帧 API；
- SQLite；
- `mcap` Python 库；
- `msgpack`；
- NumPy；
- Pillow/OpenCV 用于图片验证和必要解码。

### 23.2 选择该架构的原因

- Web 入口避免桌面壳与浏览器后端形成两套业务逻辑；
- 浏览器和 Three.js 适合实现多相机网格、3D 和复杂时间轴；
- Python 更适合读取 MCAP、JSON、MsgPack、NumPy 和现有数据处理脚本；
- SQLite 足够支持本地单用户、数千 Episode 和大量标签；
- 前后端通过本机 API 分离，便于独立测试和持续运行。

### 23.3 安全边界

- Web 服务只监听 `127.0.0.1`；
- 工作区保存稳定的本机会话令牌；
- 所有 API 请求携带令牌；
- 不开放公网；
- 文件系统访问只允许用户显式添加的数据源和工作区目录。

---

## 24. 系统组件图

```text
┌───────────────────────────────────────────────────────────┐
│ Chrome / Chromium                                         │
│ Episode List / Cameras / Three.js / Timeline / Labels     │
└───────────────────────────┬───────────────────────────────┘
                            │ HTTP / SSE
┌───────────────────────────▼───────────────────────────────┐
│ Python Local Web Service                                  │
│ Import / MCAP / Playback / Annotation / Export            │
└─────────────┬───────────────────────┬─────────────────────┘
              │                       │
       ┌────────▼────────┐      ┌───────▼─────────┐
       │ Source Dataset  │      │ Workspace       │
       │ Read-only MCAP  │      │ SQLite + Cache  │
       └─────────────────┘      └─────────────────┘
```

---

## 25. 代码目录建议

```text
mocap-qc-annotator/
├── app/
│   ├── renderer/
│   │   ├── index.html
│   │   ├── renderer.js
│   │   ├── web-api.js
│   │   ├── styles.css
│   │   └── assets/
│   └── tests/
├── src/episode_qc/
│   ├── cli.py
│   ├── web_server.py
│   ├── workspace.py
│   └── playback.py
├── tests/
├── scripts/
│   ├── dev-web.sh
│   └── test-all.sh
├── package.json
├── pyproject.toml
└── README.md
```

---

## 26. 后端模块设计

### 26.1 WorkspaceService

负责：

- 创建、打开、关闭工作区；
- 获取工作区设置；
- 管理数据源根目录；
- 管理当前标签库；
- 记录最近打开状态。

### 26.2 ImportService

负责：

- 文件夹递归扫描；
- Episode 识别；
- 指纹计算；
- 增量更新；
- 汇总文件读取；
- 导入错误记录。

### 26.3 McapService

负责：

- MCAP Summary 读取；
- Channel/Schema/Topic 枚举；
- 当前 Episode 消息计数；
- 编码识别；
- 按 Topic 读取消息；
- 缓存构建。

### 26.4 PlaybackService

负责：

- 帧时间索引；
- 最近相机帧查询；
- 最近 Mocap 帧查询；
- 当前时间窗口预取；
- 缓存淘汰；
- 数据流覆盖信息。

### 26.5 AdapterRegistry

负责把不同 Topic/编码转换为统一模型：

```python
class StreamAdapter(Protocol):
    adapter_id: str

    def supports(self, topic: str, encoding: str, schema: dict | None) -> bool:
        ...

    def decode(self, payload: bytes, context: DecodeContext) -> object:
        ...
```

Motion Adapter 输出 `MotionFrame`；Camera Adapter 输出原始图片字节和元数据。

### 26.6 AnnotationService

负责：

- 标签创建、修改、删除；
- 范围和目标校验；
- 标签库版本绑定；
- 自动保存；
- 撤销历史记录；
- Episode 标签统计。

### 26.7 LabelSchemaService

负责：

- YAML/JSON/CSV 解析；
- 导入预览；
- 冲突检查；
- 版本生成；
- 启用和停用；
- Profile 绑定；
- 标签定义导出。

### 26.8 ExportService

负责：

- 解析导出筛选条件；
- 导出单个 JSON 或 CSV 文件；
- 临时文件写入并原子替换；
- 导出日志。

---

## 27. 数据库设计

V1 使用 SQLite，所有时间使用 UTC 纳秒或带时区 ISO 时间。

### 27.1 `workspace`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | UUID/ULID |
| name | TEXT | 工作区名称 |
| reviewer_name | TEXT | 当前质检员 |
| active_label_set_id | TEXT | 当前标签库 |
| settings_json | TEXT | 设置 |
| created_at | TEXT | 创建时间 |
| updated_at | TEXT | 更新时间 |

### 27.2 `data_source`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | 数据源 ID |
| workspace_id | TEXT FK | 工作区 |
| root_path | TEXT | 根目录 |
| profile_id | TEXT | 数据 Profile |
| enabled | INTEGER | 是否启用 |
| last_scanned_at | TEXT | 最近扫描 |
| created_at | TEXT | 创建时间 |

### 27.3 `episode`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | Episode ID |
| data_source_id | TEXT FK | 数据源 |
| relative_path | TEXT | 相对路径 |
| episode_name | TEXT | 名称 |
| data_group | TEXT | 上层数据组 |
| mcap_path | TEXT | MCAP 绝对路径 |
| summary_path | TEXT NULL | 汇总文件 |
| config_path | TEXT NULL | 配置文件 |
| fingerprint | TEXT | 指纹 |
| file_size | INTEGER | 文件大小 |
| file_mtime_ns | INTEGER | 修改时间 |
| start_time_ns | INTEGER NULL | 开始时间 |
| end_time_ns | INTEGER NULL | 结束时间 |
| duration_ns | INTEGER NULL | 时长 |
| import_status | TEXT | 导入状态 |
| import_error | TEXT NULL | 错误信息 |
| cache_status | TEXT | 缓存状态 |
| review_status | TEXT | 工作状态 |
| quality_decision | TEXT NULL | 质量结论 |
| reviewer_name | TEXT NULL | 质检员 |
| last_playhead_ns | INTEGER | 上次位置 |
| annotation_count | INTEGER | 标签数缓存 |
| created_at | TEXT | 创建时间 |
| updated_at | TEXT | 更新时间 |
| reviewed_at | TEXT NULL | 完成时间 |

唯一约束：

```text
UNIQUE(data_source_id, relative_path)
```

### 27.4 `stream`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | Stream ID |
| episode_id | TEXT FK | Episode |
| topic | TEXT | Topic |
| stream_key | TEXT NULL | Profile 归一化名称 |
| stream_type | TEXT | camera/mocap/robot/... |
| display_name | TEXT | 显示名称 |
| encoding | TEXT NULL | jpeg/json/msgpack/... |
| schema_name | TEXT NULL | MCAP Schema |
| adapter_id | TEXT NULL | 解析器 |
| message_count | INTEGER | 当前 Episode 消息数 |
| first_time_ns | INTEGER NULL | 第一条时间 |
| last_time_ns | INTEGER NULL | 最后一条时间 |
| nominal_hz | REAL NULL | 平均频率 |
| available | INTEGER | 是否可用 |
| metadata_json | TEXT | 附加元数据 |

### 27.5 `label_set`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | 内部 ID |
| label_set_key | TEXT | 外部稳定 ID |
| name | TEXT | 名称 |
| version | TEXT | 版本 |
| language | TEXT | 语言 |
| source_format | TEXT | yaml/json/csv/manual |
| source_hash | TEXT | 导入内容哈希 |
| enabled | INTEGER | 是否启用 |
| raw_schema_json | TEXT | 原始标准化结构 |
| created_at | TEXT | 创建时间 |

唯一约束：

```text
UNIQUE(label_set_key, version)
```

### 27.6 `label_definition`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | 内部 ID |
| label_set_id | TEXT FK | 标签库版本 |
| code | TEXT | 标签编码 |
| name | TEXT | 名称 |
| group_code | TEXT | 分组 |
| description | TEXT NULL | 描述 |
| enabled | INTEGER | 是否启用 |
| scopes_json | TEXT | 范围 |
| targets_json | TEXT | 目标 |
| default_severity | TEXT NULL | 默认严重程度 |
| default_action | TEXT NULL | 默认处理建议 |
| shortcut | TEXT NULL | 快捷键 |
| color | TEXT NULL | 颜色 |
| applicable_profiles_json | TEXT | Profile |
| fields_json | TEXT | 自定义字段 |

### 27.7 `annotation`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | 标注 ID |
| episode_id | TEXT FK | Episode |
| label_set_key | TEXT | 标签库稳定 ID |
| label_schema_version | TEXT | 创建时版本 |
| label_code | TEXT | 标签编码 |
| scope | TEXT | episode/time_range/time_point |
| start_offset_ns | INTEGER | 相对起点 |
| end_offset_ns | INTEGER | 相对终点 |
| target_type | TEXT | 目标类型 |
| target_key | TEXT NULL | 目标名称/Topic/关节 |
| severity | TEXT NULL | 严重程度 |
| action | TEXT NULL | 处理建议 |
| comment | TEXT NULL | 备注 |
| attributes_json | TEXT | 自定义字段值 |
| source | TEXT | manual/auto |
| status | TEXT | draft/confirmed |
| reviewer_name | TEXT | 标注者 |
| created_at | TEXT | 创建时间 |
| updated_at | TEXT | 更新时间 |
| deleted_at | TEXT NULL | 软删除 |

索引：

```text
INDEX episode_id
INDEX label_code
INDEX review/status related fields
INDEX (episode_id, start_offset_ns)
```

### 27.8 `change_log`

保存可撤销操作：

- entity_type；
- entity_id；
- operation；
- before_json；
- after_json；
- created_at；
- session_id。

### 27.9 `export_record`

记录：

- 导出时间；
- 导出范围；
- 输出目录；
- Episode 数量；
- 标签数量；
- 状态和错误。

---

## 28. API 设计

### 28.1 工作区

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/workspaces` | 新建工作区 |
| GET | `/api/workspaces/current` | 当前工作区 |
| PUT | `/api/workspaces/current` | 更新设置 |
| POST | `/api/workspaces/current/sources` | 添加数据源 |
| GET | `/api/workspaces/current/sources` | 数据源列表 |
| DELETE | `/api/workspaces/current/sources/{id}` | 移除引用，不删除原文件 |

### 28.2 导入

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/import/scan` | 扫描数据源 |
| GET | `/api/import/jobs/{job_id}` | 导入进度 |
| POST | `/api/import/rescan` | 增量重扫 |
| GET | `/api/import/errors` | 导入错误列表 |

### 28.3 Episode

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/episodes` | 列表、筛选、分页 |
| GET | `/api/episodes/{id}` | 详情 |
| GET | `/api/episodes/{id}/streams` | 数据流列表 |
| POST | `/api/episodes/{id}/prepare` | 构建播放缓存 |
| GET | `/api/episodes/{id}/prepare-status` | 缓存进度 |
| PUT | `/api/episodes/{id}/review` | 状态和结论 |
| PUT | `/api/episodes/{id}/playhead` | 保存播放位置 |

### 28.4 播放

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/episodes/{id}/timeline` | 流覆盖和标签轨道 |
| GET | `/api/episodes/{id}/cameras` | 有效相机列表 |
| GET | `/api/episodes/{id}/camera/{stream_id}/frame` | 指定时间最近 JPEG |
| GET | `/api/episodes/{id}/motion/layers` | 可用骨架图层 |
| GET | `/api/episodes/{id}/motion/frame` | 指定时间 Mocap 帧 |
| GET | `/api/episodes/{id}/motion/window` | 时间窗口批量帧 |

相机帧接口示例：

```text
GET /api/episodes/{id}/camera/{stream_id}/frame?time_ns=32180000000
```

响应头：

```text
Content-Type: image/jpeg
X-Frame-Offset-Ns: 32166666667
X-Skew-Ns: -13333333
X-End-Of-Stream: false
```

### 28.5 标签库

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/label-sets` | 标签库版本列表 |
| GET | `/api/label-sets/{key}/{version}` | 标签库详情 |
| POST | `/api/label-sets/import/preview` | 导入预览 |
| POST | `/api/label-sets/import/confirm` | 确认导入 |
| GET | `/api/label-sets/{key}/{version}/export` | 导出定义 |
| POST | `/api/label-sets/{key}/{version}/activate` | 激活 |
| PUT | `/api/labels/{id}` | 修改标签显示配置 |
| POST | `/api/labels/{id}/disable` | 停用 |

### 28.6 标注

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/episodes/{id}/annotations` | 当前 Episode 标签 |
| POST | `/api/episodes/{id}/annotations` | 新建 |
| PUT | `/api/annotations/{id}` | 修改 |
| DELETE | `/api/annotations/{id}` | 软删除 |
| POST | `/api/annotations/undo` | 撤销 |
| POST | `/api/annotations/redo` | 恢复 |

### 28.7 导出

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/exports/preview` | 预览范围和数量 |
| POST | `/api/exports` | 执行导出 |
| GET | `/api/exports/{id}` | 导出状态 |
| GET | `/api/exports/history` | 导出历史 |

---

## 29. 播放缓存与性能设计

### 29.1 缓存原则

- 原始文件只读；
- 缓存可删除并重建；
- Episode 首次打开时按需准备；
- 后续打开直接复用；
- 根据文件指纹判断缓存失效；
- 缓存总量可配置，默认上限例如 100 GB；
- 超限时按最近最少使用淘汰。

### 29.2 缓存状态

```text
not_prepared
preparing
ready
failed
stale
```

### 29.3 相机索引

每路相机保存：

```text
timestamps_ns: int64[N]
offsets: int64[N]
lengths: int32[N]
```

二分查找复杂度 `O(log N)`。

### 29.4 Mocap 索引

标准化后的 Mocap 数据可保存为：

```text
timestamps.npy
positions.npy
rotations.npy
validity.npy
skeleton.json
```

如果关节数量或拓扑在 Episode 内变化，则记录为解析错误，不强行拼接。

### 29.5 前端预取

播放时预取当前时间前后小窗口，例如：

```text
当前帧前 200 ms
当前帧后 1000 ms
```

拖动跳转时取消旧预取任务，优先加载新时间位置。

### 29.6 大文件策略

- 不一次性读取全部 MCAP 到内存；
- 按 Channel 扫描；
- 写缓存时流式处理；
- UI 显示进度和已处理字节；
- 某一路解析失败不阻塞其他相机或标签功能。

---

## 30. 简单自动辅助检查

人工检查为主，但 V1 可提供不改变最终结论的基础提示：

- MCAP 不可读；
- 无 Mocap 流；
- 当前 Episode 无相机；
- 某数据流消息数为 0；
- 时间戳倒序；
- 相邻消息间隔极大；
- 解析器不支持；
- Sidecar 和 MCAP 时间范围不一致；
- Summary 的 Episode 写入数量与 MCAP 统计不一致。

这些提示只显示在数据流状态和时间轴，不自动生成正式标签，除非用户确认。

---

## 31. 错误处理

### 31.1 导入错误

错误类型：

```text
EPISODE_MCAP_MISSING
MCAP_OPEN_FAILED
MCAP_SUMMARY_FAILED
SIDECAR_PARSE_FAILED
DUPLICATE_SOURCE
SOURCE_PATH_MISSING
PERMISSION_DENIED
```

处理原则：

- 单条失败不阻塞批量导入；
- Episode 保留在列表中并显示失败原因；
- 支持重新尝试；
- 支持打开源目录。

### 31.2 播放错误

```text
CAMERA_DECODE_FAILED
MOTION_ADAPTER_MISSING
MOTION_FRAME_INVALID
CACHE_BUILD_FAILED
FRAME_NOT_FOUND
```

处理原则：

- 单路相机失败不影响其他相机；
- Mocap 失败仍可进行相机和全局标注；
- 界面明确区分“没有数据”和“解析失败”。

### 31.3 保存错误

- 数据库事务失败时保留前端待保存状态；
- 自动重试；
- 切换 Episode 前若仍失败，弹出明确提示并允许导出紧急草稿；
- 不显示虚假的“已保存”。

### 31.4 源文件变化

打开时发现指纹变化：

- 标记 Episode `stale`；
- 不自动删除标签；
- 提示重新索引；
- 标签仍基于原 Episode 时间范围，重新索引后检查是否越界。

---

## 32. 日志与诊断

### 32.1 日志分类

- application；
- import；
- mcap；
- playback；
- annotation；
- export；
- crash。

### 32.2 日志内容

- 时间；
- 工作区 ID；
- Episode ID；
- 操作；
- 耗时；
- 错误堆栈；
- 文件路径需可配置脱敏。

### 32.3 诊断包

设置页提供“导出诊断包”，包含：

- 应用版本；
- 系统信息；
- 最近日志；
- 数据库 schema 版本；
- 当前 Profile 和标签库定义；
- 不包含原始 MCAP 和相机图像。

---

## 33. 非功能需求

### 33.1 兼容性

- Ubuntu 22.04/24.04；
- 本地磁盘和挂载 NAS 路径；
- 中文路径、空格路径；
- 大小写敏感文件系统；
- 离线运行。

### 33.2 性能目标

在本地 SSD、典型 2–4 路 JPEG 相机、2–5 分钟 Episode 条件下：

- 目录发现后尽快先显示 Episode 列表，不等待全部缓存；
- 已准备 Episode 打开时间目标小于 2 秒；
- 播放时 UI 交互保持流畅；
- 4 路相机 1× 播放时尽量达到源帧率，硬件不足时允许丢显示帧但不改变标注时间；
- 创建标签到 SQLite 落库目标小于 300 ms；
- 10,000 条标签查询和筛选不出现明显卡顿；
- 数据库支持至少 10,000 个 Episode 和 1,000,000 条标签的结构扩展。

### 33.3 稳定性

- 应用异常退出后可恢复最近工作区；
- 已落库标签不丢失；
- 缓存损坏可重建；
- 源数据始终不被修改；
- 数据库迁移可回滚或自动备份。

### 33.4 可维护性

- 数据 Profile、Motion Adapter、标签库彼此独立；
- 新相机 Topic 不要求修改 UI；
- 新标签不要求重新编译应用；
- 新 Mocap 格式只新增 Adapter；
- 数据库使用迁移工具维护版本。

---

## 34. 测试设计

### 34.1 单元测试

- Episode 目录识别；
- 指纹计算；
- Summary 解析；
- Topic 分类；
- 标签 Schema 校验；
- CSV 多值解析；
- 标签范围校验；
- 时间转换；
- 最近帧二分查找；
- 导出字段和版本；
- 撤销/恢复。

### 34.2 集成测试

- 导入包含多个数据组的根目录；
- 一个 Episode 损坏不影响其他 Episode；
- 0、1、2、4、6 路相机布局；
- 相机数量在 Episode 间变化；
- 无 Mocap Adapter 时仍可标注；
- 首次打开建立缓存；
- 二次打开复用缓存；
- 标签库导入预览和冲突处理；
- 自动保存后重启恢复；
- 单文件 JSON/CSV 导出结果可再次读取。

### 34.3 UI/E2E 测试

- 从选择文件夹到打开第一条 Episode；
- 播放、暂停、拖动和逐帧；
- 拖选区间并使用快捷键打标签；
- 选择相机目标；
- 选择关节目标；
- 修改区间边界；
- 撤销和恢复；
- 提交结论并进入下一条；
- 筛选已完成数据；
- 导出标签。

### 34.4 数据完整性测试

- 导出时间不越界；
- 导出标签数量等于数据库查询数量；
- 标签库版本正确；
- 软删除标签不出现在默认导出；
- Episode 相对时间和绝对时间转换正确；
- 应用不修改源文件的大小和修改时间。

---

## 35. 第一版实施计划

### 里程碑 0：项目骨架

交付：

- 浏览器页面与 Python 本地 Web API 可联调；
- Web 服务可启动、关闭并持久化工作区；
- SQLite 和 Alembic 初始化；
- 工作区创建和打开；
- 基础日志。

验收：本机 Web 服务可启动，浏览器前端能调用本地 API。

### 里程碑 1：文件夹导入与 Episode 列表

交付：

- 原生目录选择；
- 数据源管理；
- 递归扫描；
- Episode 识别；
- Summary/MCAP 元信息读取；
- Episode 数据库；
- 列表、分页、筛选；
- 导入错误展示；
- 增量重扫。

验收：可导入一个包含多条数据的根目录，并正确列出所有有效和失败 Episode。

### 里程碑 2：动态相机与基础播放

交付：

- 相机流识别；
- 相机帧缓存；
- 0/1/2/4/多路自适应布局；
- 全局播放时钟；
- 播放、暂停、跳转、倍速；
- 相机全屏和主相机；
- 时间轴基础轨道。

验收：相机数量不同的 Episode 可以无须改代码直接播放。

### 里程碑 3：Mocap 3D

交付：

- Adapter Registry；
- 首个 `mocap_human_motion` Adapter；
- Mocap 帧缓存；
- Three.js 骨架显示；
- 关节选择；
- Mocap 与相机时间同步。

验收：样例 MCAP 可显示人体骨架，并与相机共享同一播放头。

### 里程碑 4：标签库与人工标注

交付：

- 标签 Schema 模型；
- YAML/JSON/CSV 导入；
- 导入预览和冲突处理；
- 标签分组、搜索和快捷键；
- 整条、区间、时间点标注；
- 相机、关节、流、全局目标；
- 自定义字段；
- 自动保存；
- 撤销/恢复。

验收：无需修改代码即可导入一套新标签，并完成整条和区间标注。

### 里程碑 5：结论、导出与稳定性

交付：

- 质检状态；
- 质量结论；
- 上一条/下一条工作流；
- 单文件 JSON/CSV 导出；
- 导出预览；
- 崩溃恢复；
- 缓存清理；
- 安装和启动脚本；
- E2E 测试。

验收：质检员可完整完成“导入—查看—标注—结论—导出”闭环。

---

## 36. V1 验收标准

### 36.1 导入

- 能选择一个根目录并递归识别多条 Episode；
- 单条损坏不会中断其他数据；
- 支持重新扫描并发现新增 Episode；
- 不修改原始文件；
- 能显示导入错误和源路径。

### 36.2 动态相机

- 0、1、2、3、4、5 路相机均能正常布局；
- Episode 切换后自动重新生成相机窗口；
- 当前 Episode 消息数为 0 的相机不占空窗口；
- 相机帧跟随统一播放时间；
- 可对具体相机创建标签。

### 36.3 Mocap

- 能通过 Adapter 显示样例 `mocap_human_motion`；
- 支持选择关节；
- 能对关节创建区间标签；
- 未适配格式不会导致整个 Episode 无法质检。

### 36.4 标签

- YAML、JSON、CSV 标签库均可导入；
- 导入前有预览；
- 重复编码、无效分组和快捷键冲突会明确提示；
- 标签库有版本；
- 标签支持整条、区间和时间点；
- 标签支持全局、相机、关节和数据流目标；
- 标签增删改自动保存；
- 可撤销和恢复。

### 36.5 质检工作流

- 可筛选未质检 Episode；
- 可快速切换上一条和下一条；
- 切换前自动保存；
- 可设置质量结论；
- 重启后恢复质检进度和播放位置。

### 36.6 导出

- 可导出当前筛选结果；
- 每个任务只生成一份所选格式的结果文件；
- 导出包含 Episode 结论；
- JSON 导出包含标签 Schema 快照和版本；
- 导出包含相对和绝对时间；
- 导出失败不留下正式半成品文件。

---

## 37. 开发前必须准备的样例

为避免在消息格式上猜测，开始开发时建议准备以下最小样例集：

1. 一条完整 `episode.mcap`；
2. 对应 `config_snapshot.yaml`；
3. 对应 `summary.yaml` 或当前汇总文本；
4. `mocap_human_motion` 解码后的一条 JSON 消息；
5. `soma_retarget_action` 解码后的一条 MsgPack 消息；
6. 至少一条 JPEG 相机消息；
7. 一个 0 路相机 Episode；
8. 一个 1 路相机 Episode；
9. 一个 2 路或更多相机 Episode；
10. 一份计划使用的首版标签 YAML。

其中只有第 4 项是完成 Mocap 3D Adapter 的阻塞项；其他模块可以先并行开发。

---

## 38. 风险与应对

### 38.1 MCAP 消息格式不统一

风险：同名 Topic 在不同采集版本中字段不同。

应对：

- Adapter 带版本；
- 数据 Profile 指定 Adapter；
- 解析失败按帧记录，不崩溃；
- Profile 与 Episode 保存关联。

### 38.2 多相机大数据播放性能

风险：多路 JPEG 造成频繁磁盘读取和 UI 卡顿。

应对：

- 连续帧缓存文件 + 时间索引；
- 二分查找；
- 小窗口预取；
- 前端显示帧可丢弃但时间不漂移；
- 缓存使用独立线程/进程准备。

### 38.3 NAS 访问不稳定

风险：路径临时断开、读取慢。

应对：

- 明确区分源丢失与文件损坏；
- 已保存标签不受影响；
- 支持重新连接和重新索引；
- 缓存就绪时可继续查看已缓存内容；
- 不将数据库放在不稳定 NAS。

### 38.4 标签体系频繁变化

风险：历史标签无法解释。

应对：

- 稳定 `code`；
- 标签库版本；
- 标注保存创建时版本；
- 导出 Schema 快照；
- 已使用标签只停用不硬删除。

### 38.5 自动保存造成误操作固化

风险：误删标签立即保存。

应对：

- 软删除；
- 变更日志；
- 撤销/恢复；
- 数据库事务。

---

## 39. 第二版候选能力

第一版稳定后再考虑：

- XLSX 标签库导入；
- 自动跳变、冻结、丢帧候选；
- 相机模糊、黑屏、重复帧提示；
- 自动区间建议并由人工确认；
- 双人复核和抽检；
- 标签一致性统计；
- 自动裁剪并导出新 MCAP；
- Foxglove 标注互通；
- 局域网多人模式；
- 运营平台同步；
- 质量日报和人员统计；
- BFM 训练数据筛选接口。

---

## 40. 第一版最终决策摘要

| 项目 | V1 决策 |
|---|---|
| 产品形态 | Ubuntu 本地 Web 应用 |
| 主工作方式 | 人工同步回放与标注 |
| 数据输入 | 按文件夹递归导入多 Episode |
| 原始数据 | 默认只读 |
| 相机 | 根据当前 Episode 动态发现和布局 |
| Mocap | Adapter 插件化解析，Three.js 显示 |
| 标签 | YAML/JSON/CSV 自定义导入 |
| 标签版本 | 必须保存 |
| 标签范围 | Episode、区间、时间点 |
| 标签目标 | 全局、相机、关节、流等 |
| 进度 | SQLite 自动保存 |
| 导出 | 每任务单个 JSON 或 CSV 文件 |
| 自动质检 | 仅做基础提示，不替代人工结论 |
| 技术栈 | 浏览器原生 JavaScript + Three.js + Python 本地 Web 服务 + SQLite |
| 第一版完成标准 | 完成导入、播放、标注、结论、导出闭环 |

---

# 附录 A：首版标签库建议

```yaml
schema:
  schema_type: annotation_label_schema
  schema_version: "1.0.0"
  label_set_id: mocap_qc_v1
  label_set_name: Mocap 质检标签 V1
  language: zh-CN

severity_levels:
  - {code: minor, name: 轻微, order: 1}
  - {code: normal, name: 一般, order: 2}
  - {code: critical, name: 严重, order: 3}

actions:
  - {code: keep, name: 保留}
  - {code: keep_with_label, name: 保留但标记}
  - {code: trim, name: 裁剪区间}
  - {code: repair, name: 需要修复}
  - {code: recollect, name: 需要重采}
  - {code: reject, name: 整条废弃}
  - {code: review, name: 待复核}

groups:
  - {code: episode, name: 整体问题, order: 1}
  - {code: mocap, name: Mocap 问题, order: 2}
  - {code: camera, name: 相机问题, order: 3}
  - {code: teleoperation, name: 遥操作问题, order: 4}
  - {code: collection, name: 采集过程问题, order: 5}

labels:
  - code: episode_usable
    name: 数据可用
    group: episode
    annotation_scopes: [episode]
    target_types: [global]
    default_severity: minor
    default_action: keep
    color: "#22C55E"

  - code: task_incomplete
    name: 动作未完成
    group: episode
    annotation_scopes: [episode, time_range]
    target_types: [global]
    default_severity: critical
    default_action: recollect
    shortcut: T
    color: "#DC2626"

  - code: mocap_joint_jitter
    name: 关节抖动
    group: mocap
    annotation_scopes: [time_range, time_point]
    target_types: [mocap, joint]
    default_severity: normal
    default_action: trim
    shortcut: Q
    color: "#F59E0B"
    fields:
      - code: affected_joint
        name: 异常关节
        type: joint_selector
        required: true
        multiple: true

  - code: mocap_tracking_lost
    name: 追踪丢失
    group: mocap
    annotation_scopes: [time_range]
    target_types: [mocap, joint]
    default_severity: critical
    default_action: trim
    shortcut: W
    color: "#EF4444"

  - code: mocap_skeleton_jump
    name: 骨架跳变
    group: mocap
    annotation_scopes: [time_range, time_point]
    target_types: [mocap, joint]
    default_severity: critical
    default_action: trim
    shortcut: E
    color: "#F97316"

  - code: camera_blur
    name: 画面模糊
    group: camera
    annotation_scopes: [episode, time_range]
    target_types: [camera]
    default_severity: normal
    default_action: keep_with_label
    shortcut: B
    color: "#8B5CF6"

  - code: camera_freeze
    name: 画面卡顿或冻结
    group: camera
    annotation_scopes: [time_range]
    target_types: [camera]
    default_severity: normal
    default_action: trim
    shortcut: C
    color: "#6366F1"

  - code: camera_target_out_of_view
    name: 目标出画
    group: camera
    annotation_scopes: [time_range]
    target_types: [camera]
    default_severity: critical
    default_action: recollect
    color: "#A855F7"

  - code: retarget_error
    name: 重定向异常
    group: teleoperation
    annotation_scopes: [time_range]
    target_types: [retarget, joint]
    default_severity: critical
    default_action: trim
    color: "#E11D48"

  - code: hand_command_abnormal
    name: 灵巧手命令异常
    group: teleoperation
    annotation_scopes: [time_range]
    target_types: [hand, stream]
    default_severity: normal
    default_action: trim
    color: "#0EA5E9"

  - code: person_interruption
    name: 人员干扰或进入
    group: collection
    annotation_scopes: [time_range]
    target_types: [global, camera]
    default_severity: normal
    default_action: trim
    shortcut: R
    color: "#64748B"

  - code: device_adjustment
    name: 设备调整
    group: collection
    annotation_scopes: [time_range]
    target_types: [global]
    default_severity: normal
    default_action: trim
    color: "#78716C"

  - code: invalid_waiting
    name: 无效等待
    group: collection
    annotation_scopes: [time_range]
    target_types: [global]
    default_severity: minor
    default_action: trim
    color: "#94A3B8"
```

---

# 附录 B：建议优先开发的垂直切片

为了尽快看到可用结果，第一条完整垂直切片建议是：

```text
选择一个包含 2 条 Episode 的文件夹
  → 列表显示 2 条数据
  → 打开其中一条
  → 自动识别头部和胸部相机
  → 可同步播放
  → 时间轴拖出区间
  → 使用导入标签“画面模糊”标记头部相机
  → 选择“有条件通过”
  → 切换下一条
  → 选择 JSON 或 CSV，导出一份“任务名_标注结果”文件
```

这条链路打通后，再接入 Mocap 3D Adapter。这样即使骨架消息格式尚未完全明确，也不会阻塞导入、相机、标签和导出主体功能。
