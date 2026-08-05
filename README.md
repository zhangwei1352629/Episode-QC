# Episode QC 用户手册

Episode QC 是一套面向 Episode MCAP 与 FZMotion BVH 数据集的本地 Web 质检与命令行工具。由常驻 Python 服务提供工作区和二进制帧 API，通过本机浏览器访问。

软件支持递归导入 Episode、相机与 Mocap 同步回放、人工区间/时间点/整条标注、可版本化标签库、质检结论自动保存、撤销/恢复以及按任务生成单个 JSON/CSV 结果文件。源数据始终按只读方式访问，播放缓存和标注结果保存在独立工作区中。

## 1. 功能概览

- 递归发现并索引 `episode_*` 目录中的 MCAP 或 BVH 文件；
- 动态识别相机 Topic，并同步显示多路 JPEG 画面；
- 解析 `mocap_human_motion.raw_v1` 或 FZMotion BVH，显示可旋转、缩放和选关节的三维姿态；
- 支持区间、时间点和整条 Episode 三种标注范围；
- 支持 YAML、JSON、CSV 标签库的校验、预览、导入和版本管理；
- 自动保存质检员、播放位置、标注和 Episode 结论；
- 支持标注撤销、恢复、软删除及按任务单文件导出；
- 提供可折叠的任务栏和标注栏，记忆当前浏览器的工作台布局，并在未选择任务、Episode 或播放缓存未就绪时禁用无效操作；
- 提供局部画面撕裂、残留区域和光流异常的命令行检测工具；
- 从 Flow 领取质检任务，将大文件从 NAS 完整、可续传地缓存到本地并校验 SHA-256；
- 向 Flow 上报质检开工和提交前心跳，对源 MCAP/BVH、元数据和配置快照保持只读，结果先原子写回 NAS 再提交 Flow。

## 2. 环境要求

- Python：`3.11` 或 `3.12`；
- Node.js：`20` 或更高版本（仅重新构建或测试 Web 前端时需要）；
- Python 包管理器：`uv`；
- 浏览器：当前主要使用 Chrome/Chromium 验证；
- 操作系统：Linux 已完成 Chrome/Chromium Web 入口验证。

如尚未安装 `uv`：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

安装或更新 Python 环境：

```bash
uv sync --dev
```

如需修改或测试 Three.js Web 页面，再安装前端依赖：

```bash
npm install
```

## 3. 启动本地 Web 软件（推荐）

运行以下命令会启动只监听 `127.0.0.1` 的 Python 服务、读取或生成工作区访问令牌并自动打开浏览器：

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

启动终端会打印访问地址；只打开不带令牌的首页也会自动补齐当前工作区令牌。服务默认不接受局域网连接。关闭浏览器标签页不会立即停止后台缓存，返回启动终端按 `Ctrl+C` 可退出服务。

### 局域网访问

在可信局域网中，可以监听全部网卡，并显式声明允许访问的网卡地址：

```bash
uv run episode-qc web \
  --host 0.0.0.0 \
  --public-host 192.168.123.222 \
  --public-host 10.1.11.155 \
  --port 8765 \
  --no-browser
```

同一网络中的电脑随后可直接打开以下任一地址：

```text
http://192.168.123.222:8765/
http://10.1.11.155:8765/
```

`--host` 是服务监听地址；`--public-host` 是允许浏览器使用的 IP 或主机名，可重复填写。监听 `0.0.0.0` 时必须至少提供一个 `--public-host`，未声明的 Host 和 Origin 会被拒绝。若网卡通过 DHCP 获得了新地址，需要使用新地址重新启动。客户端还必须与相应网卡网络互通，且操作系统或网络防火墙允许 TCP 8765 端口。若本机启用了 UFW，可由管理员只放行这两个局域网网段：

```bash
sudo ufw allow from 192.168.123.0/24 to any port 8765 proto tcp comment 'Episode QC Ethernet'
sudo ufw allow from 10.1.10.0/23 to any port 8765 proto tcp comment 'Episode QC Wi-Fi'
sudo ufw status
```

局域网直达模式没有独立账号和角色：访问首页会自动取得工作区令牌，同网段中能够连接该端口的人都可查看数据、修改标注和导出结果。HTTP 也不加密传输。仅应在可信、隔离的质检网络内使用；不要把端口映射到互联网。多人同时操作同一工作区还可能互相覆盖当前质检员、播放状态或同一 Episode 的编辑，建议一个工作区同一时间由一名质检员使用。需要跨不可信网络或多用户权限时，应在前方增加 HTTPS、身份认证与独立工作区隔离。

Web 模式下，“导入新任务”会先询问数据位于“当前打开网页的电脑”还是“QC
服务器/NAS”。服务器模式支持服务所在电脑上的绝对路径，也支持服务器已登录挂载的
`smb://服务器/共享/目录` NAS 地址；客户端模式见下一节。“导入标签库”和“导出结果”
使用 QC 服务器上的路径。

### 读取浏览器所在电脑的数据（Data Worker）

局域网电脑仅打开中央 QC 网页时，浏览器不能把任意本地绝对路径交给服务器读取。若数据
位于这台电脑，可在该电脑启动只监听 `127.0.0.1` 的 Data Worker：

```bash
uv run episode-qc data-worker \
  --port 8766 \
  --allow-origin http://192.168.123.222:8765 \
  --allow-origin http://10.1.11.155:8765
```

`--allow-origin` 必须与浏览器地址栏中的中央 QC 地址完全一致；只使用其中一个地址时，只需
配置对应的一项。启动窗口会显示 Worker 名称、Worker ID 和访问令牌。然后在中央 QC 页面：

1. 单击“导入新任务”，输入 `1`（当前打开网页的电脑）；
2. Worker 地址保留默认的 `http://127.0.0.1:8766`；
3. 粘贴启动窗口显示的访问令牌；
4. 输入数据在当前电脑上的绝对路径。

Worker 在当前电脑完成目录扫描、MCAP/BVH 解析、播放缓存和帧读取。中央 QC 服务器只保存
任务清单、标签、标注、质检结论和导出结果，不复制接近 100 GB 的源数据。Worker 令牌只保存在
当前浏览器的本地存储中，不会提交到中央服务器；中央数据库只记录稳定的 Worker ID 和本机
回环地址。播放和重新扫描期间必须保持 Worker 运行，完成标注后可关闭。

在另一台电脑打开同一个客户端任务时，该电脑没有原 Worker 和令牌，页面会提示无法连接；
这是为了避免误读其他电脑的数据。需要继续质检时，应回到数据所在电脑，启动同一个 Worker
工作区并打开中央页面。默认 Worker 工作区位于：

```text
~/.config/episode-qc/data-worker/
```

常见问题：

- 提示“无法连接本机 Data Worker”：确认启动窗口仍在运行，且端口为 `8766`；
- 提示“跨域来源未授权”：把浏览器地址栏中的 `协议://IP:端口` 加到启动命令的
  `--allow-origin` 后重启 Worker；
- 提示“访问令牌无效”：重新输入本次 Worker 工作区打印的令牌；
- Worker 无需开放防火墙端口，因为它只监听当前电脑的 `127.0.0.1`；
- MCAP/BVH 源文件始终只读，缓存写入 Worker 工作区，不写回数据目录。

### QC 任务与数据导入

QC 页面按“一个数据资产目录对应一个 QC 任务”组织数据。左侧“当前 QC 任务”卡片始终显示任务名称、任务编号、本地目录、状态和完成进度；Episode 列表、状态统计、上一条/下一条和结果导出都只作用于当前任务。

点击“导入新任务”并填写新目录后，软件会创建任务、索引 Episode，并在成功识别到可用 Episode 后自动进入该任务。以前的任务、标注和质检结论会保留在“切换任务”打开的任务中心中，不再混入当前列表。若目录中没有可用 Episode，页面会保留原任务并显示失败任务及错误原因。

再次导入完全相同的目录不会创建重复任务，而会重新扫描原任务。也可以在当前任务卡片中点击“重新扫描”。每个浏览器会记住自己最后选择的任务；每个任务还会单独记住上次打开的 Episode。

Flow 领取的任务使用平台 `QCJ-...` 编号，本地手工导入的任务使用 `LOCAL-...` 编号。结果导出固定包含当前任务的全部 Episode，不受页面搜索和状态筛选影响。

## 4. 数据目录要求

推荐的数据结构如下：

```text
数据根目录/
├── episode_000001/
│   ├── episode.mcap
│   ├── metadata.yaml
│   └── config_snapshot.yaml
├── episode_000002/
│   ├── motion.bvh
│   └── metadata.json
└── ...
```

其中：

- 每个 Episode 使用 `episode.mcap` 或 `motion.bvh` 作为主文件；同一目录两者都存在时优先 MCAP；
- `metadata.json`、`metadata.yaml`、`summary.yaml` 或 `episode_summary.yaml` 可作为 Episode 元数据；
- `config_snapshot.yaml` 或 `config.yaml` 可作为采集配置快照；
- 元数据和配置文件可以缺省，但 MCAP 需要可读取的 Summary，BVH 需要完整的 `HIERARCHY`、`Frames` 和 `Frame Time`；
- 实际目录和 Topic 匹配规则由 Data Profile 控制，示例见 [`data_profile_v1.example.yaml`](mocap_qc_v1_design_bundle/data_profile_v1.example.yaml)。

## 5. 浏览器使用流程

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

页面采用任务栏、回放工作区和标注栏三栏布局。顶部“任务栏”和“标注栏”按钮可以分别收起两侧面板，显示状态会保存在当前浏览器中；再次打开时会恢复上次布局。“工具”菜单集中提供质检员姓名、导出格式、标签库导入和标签模板下载。

任务、Episode 或播放缓存尚未就绪时，播放、时间轴、标注、结论和导出等相关按钮会保持禁用；键盘快捷键也遵循相同状态，避免在空工作区中产生误操作。切换到空任务时，上一条 Episode 的播放、区间和标注显示会一并清空。

### 5.2 添加数据目录

1. 单击右上角“导入新任务”；
2. 输入包含一个或多个 `episode_*` 的绝对路径，或输入
   `smb://服务器/共享/目录`；NAS 共享需要先在系统中登录或挂载；
3. 软件递归查找 MCAP/BVH，并读取每条 Episode 的时长、数据流和帧数；
4. 导入结束后，左侧会显示 Episode 总数、已完成数量和导入异常数量；
5. 可通过搜索框和状态下拉框筛选 Episode。

重新添加同一路径会执行重扫。源文件发生变化时，相应播放缓存会标记为需要重新生成。

Linux 下，应用会自动把 `smb://` 地址映射到当前用户的 GVFS 挂载目录，也能识别
系统级 CIFS 挂载。首次访问共享时，请先在文件管理器的“其他位置”中打开 NAS 并完成登录；
应用不会保存或请求 NAS 密码。若共享未挂载，错误信息会明确提示先登录挂载。

命令行同样支持 SMB 地址：

```bash
uv run episode-qc workspace-scan /path/to/workspace.db \
  'smb://delta-ai-nas.local/datasets/Delta_mocap/某个数据目录'
```

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

工作区没有激活标签库时，软件会自动导入中文简易模板。页面右上角也可以随时单击“下载标签模板”。

普通项目人员只需阅读[《简易标签填写说明》](docs/简易标签填写说明.md)，其中只包含标签名称、编码、分组、判断标准、标注对象和标注范围。需要维护完整配置的管理员再参考[《标签库填写说明》](docs/标签库填写说明.md)。

#### 推荐：中文简易模板

标注人员不需要理解完整的高级结构，但每个标签必须填写稳定、可读的英文编码和中文名称：

```yaml
标签库名称: 我的任务标签
标签:
  - 编码: unnatural_motion
    名称: 动作不自然
  - 编码: clothes_drop
    名称: 衣物掉落
    分组: 衣物处理
    说明: 衣物从手中或目标位置掉落
```

`编码`不能省略，必须以小写英文字母开头，只包含小写字母、数字和下划线，例如 `clothes_drop`。其他字段省略时，软件会自动分配颜色并使用以下默认值：

| 可选字段 | 默认值 | 可填写的中文值 |
|---|---|---|
| 范围 | 区间、时间点、整条均可用 | 区间、时间点、整条、全部 |
| 对象 | 全局 | 全局、画面、动捕、关节、全部 |
| 严重程度 | 一般 | 轻微、一般、严重 |
| 处理建议 | 保留但标记 | 保留、保留但标记、裁剪、修复、重采、废弃、待复核 |

多个值使用“、”“或”或英文竖线连接均可。模板中填写了无法识别的词时，软件会直接列出该字段允许填写的中文值。

可直接使用：

- [通用中文简易模板](app/renderer/label-template-simple.yaml)
- [洗衣机任务简易 YAML](mocap_qc_v1_design_bundle/label_templates/washing_machine_task_qc_simple.yaml)
- [洗衣机任务简易 CSV](mocap_qc_v1_design_bundle/label_templates/washing_machine_task_qc_simple.csv)

手动导入步骤：

1. 单击右上角“导入标签库”；
2. 输入 YAML、JSON 或 CSV 文件在服务所在电脑上的绝对路径；
3. 查看新增、更新、不变和保留标签的预览结果；
4. 确认后导入并激活新版本。

原有完整结构仍然兼容，适用于需要自定义内部编码、扩展填写项或 Profile 限制的标签管理员：

- [高级 YAML 标签模板](mocap_qc_v1_design_bundle/label_templates/washing_machine_task_qc_v1.yaml)；
- [高级 JSON 标签模板](mocap_qc_v1_design_bundle/label_templates/washing_machine_task_qc_v1.json)；
- [高级 CSV 标签模板](mocap_qc_v1_design_bundle/label_templates/washing_machine_task_qc_v1.csv)；
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
3. 在标签上方的“标注对象”栏明确选择“全局”“全身动作”、某一路相机或具体关节；也可以继续单击相机画面或三维关节，两个入口会保持同步；
4. 当前对象不适用的标签会变灰，并显示该标签支持的对象；最终保存对象始终与选择栏高亮项一致；
5. 可先填写备注，再单击标签；
6. 标注创建后立即写入工作区，无需手动保存；
7. 单击已有标注可修改时间、严重程度、处理建议和备注，或删除该标注。

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
2. 打开右上角“工具”菜单，在“导出格式”中选择 `JSON` 或 `CSV`；
3. 单击“导出结果”并选择保存目录；
4. 软件按“任务目录名 + `_标注结果` + 扩展名”原子写入一个文件。

例如，导入目录为 `20260717_dishwasher_yangqiyao2` 时，结果为二选一：

```text
20260717_dishwasher_yangqiyao2_标注结果.json
20260717_dishwasher_yangqiyao2_标注结果.csv
```

JSON 文件包含导出版本、任务名、筛选条件、标签库快照、Episode 结论和标注明细。CSV
将 Episode 结论与标注明细展开到一张表；一个 Episode 有多条标注时对应多行，没有标注时仍保留一行。
一次筛选包含多个数据任务时，每个任务分别生成一个结果文件；不会把不同任务合并。同一任务以相同格式
再次导出时，会原子更新原文件，不再创建带时间戳的多文件目录。

命令行导出示例：

```bash
uv run episode-qc workspace-export /path/to/workspace.db /path/to/results --format json
uv run episode-qc workspace-export /path/to/workspace.db /path/to/results --format csv
```

## 6. Flow 平台任务与大文件本地缓存

质检工作站只从 Flow 领取任务，原始文件仍从 NAS 读取。一个任务对应一个数据资产；`platform-cache` 会一次缓存资产中的全部 Episode。对于约 100GB 的资产，它会先检查磁盘空间，再逐文件复制到本地 `.partial` 目录；中断后可续传，所有 Episode 主文件的 SHA-256 通过后才切换为 `ready`。由于 Web 回放还要生成派生缓存，默认要求开始时可用空间至少为“源数据大小的 2 倍 + 10GB”，即 100GB 任务至少预留约 210GB。

先设置质检账号，避免把密码保存在 shell 历史中：

```bash
export EPISODE_QC_FLOW_URL=http://127.0.0.1:8000
export EPISODE_QC_FLOW_USERNAME=demo_qc_reviewer
export EPISODE_QC_FLOW_PASSWORD=demo-qc-pass-2026
```

查看待领取任务：

```bash
uv run episode-qc platform-jobs --status pending
```

领取任务并完成 NAS → 本地缓存 → SHA-256 校验 → BVH/MCAP 建档 → 播放缓存：

```bash
uv run episode-qc platform-cache QCJ-20260804-0001 \
  /data/episode-qc-staging \
  /data/episode-qc-workspace/workspace.db \
  --playback-cache-root /data/episode-qc-workspace/cache \
  --reserve-gb 10
```

命令返回 `local_episodes`，其中记录 Flow Episode ID 与本地 Episode ID 的对应关系。随后用同一个工作区启动 Web 软件，在浏览器中逐个完成资产内所有 Episode 的回放、标注和结论：

```bash
uv run episode-qc web --workspace-root /data/episode-qc-workspace
```

进入平台任务的人工质检阶段时，客户端会通过 `/api/v1/qc/jobs/{code}/work` 上报 `start`，并附带当前质检工作站名称；提交结果前会再上报一次 `heartbeat`。Flow 因而可以按真实开工时间和有效心跳统计质检工时，而不再用缓存状态代替人工质检状态。

所有 Episode 都有结论后，提交命令会把整项任务打包为一个 `qc_result.json`，原子写到资产 NAS 目录的 `qc/vN/`，NAS 发布成功后才批量回写 Flow：

```bash
uv run episode-qc platform-submit QCJ-20260804-0001 \
  /data/episode-qc-staging \
  /data/episode-qc-workspace/workspace.db \
  --quality-grade good
```

只有结果已经同步的任务才能删除本地原始数据暂存副本：

```bash
uv run episode-qc platform-evict QCJ-20260804-0001 /data/episode-qc-staging
```

不要直接删除 `ready/<job_code>`。未同步结果时，正式清理命令会拒绝删除；这样可避免网络故障后丢失已经完成但尚未回写的质检结果。工作区中的播放派生缓存和标注会继续保留，以支持复核；可按项目留存策略在任务闭环后整体归档或清理对应工作区。

## 7. 命令行画面质检

常规 Web 启动不会加载 NumPy、Pillow 或画面异常检测模块。命令行检测子命令可显式调用，并只在调用时延迟加载检测依赖。

所有命令可通过以下方式查看：

```bash
uv run episode-qc --help
```

### 7.1 查看相机 Topic

```bash
uv run episode-qc topics \
  20260717_dishwasher_yangqiyao2/episode_000050/episode.mcap
```

### 7.2 检测局部残留和画面撕裂

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

### 7.3 扫描整个数据目录

```bash
uv run episode-qc scan-folder \
  /home/zw/workspace/Episode-QC/20260717_dishwasher_yangqiyao2 \
  --jobs 4 \
  --json folder-qc-report.json \
  --export-dir folder-qc-snapshots
```

文件夹报告会在候选记录中补充 `episode` 和 `mcap_path`，便于按 Episode 汇总。

### 7.4 光流二次验证

对已知帧或播放时间附近执行块光流残差验证：

```bash
uv run episode-qc verify-flow path/to/episode.mcap \
  --elapsed 153.556181911 \
  --window-frames 8 \
  --json flow-report.json \
  --export-dir flow-snapshots
```

当前后端使用无额外模型依赖的 NumPy 块匹配算法，适合对可疑区间执行本地二次检查。

### 7.5 已知正样本

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

### 7.6 参数调整建议

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

## 8. 测试与验证

运行 Python 测试：

```bash
uv run pytest
```

运行 Web 前端测试：

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

## 9. 常见问题

### 首次打开 Episode 较慢

首次打开需要顺序读取 MCAP 并生成播放缓存，文件越大、相机越多，耗时越长。缓存完成后再次打开会明显加快。请确保工作区磁盘有足够空间。

### 相机画面没有显示

确认 MCAP 中存在压缩 JPEG Topic，并检查 Data Profile 的 `topic_patterns` 是否匹配实际 Topic。

### Mocap 骨架没有显示

当前播放适配器支持 `mocap_human_motion.raw_v1`/`link_pose_float32` 和标准 FZMotion BVH。其他格式会保留在源数据中，但不会自动显示为骨架。

### XLSX 标签模板无法导入

当前版本仅直接导入 YAML、JSON 和 CSV。XLSX 用于 Excel 编辑；编辑完成后请另存为 CSV，或同步修改 YAML/JSON 文件后再导入。

### 软件或电脑出现明显卡顿

避免对同一工作区同时启动多个 Web 服务，也不要并行打开多个大型 Episode 生成缓存。若卡顿重复出现，请记录发生时间、正在执行的操作和所选 Episode，以便结合系统日志定位。

### 数据是否会被修改

不会。Episode QC 对 MCAP/BVH、元数据和采集配置按只读方式访问。所有索引、缓存、标注和进度都写入工作区；平台任务的 `qc_result.json` 会按明确流程写入 NAS 的独立 `qc/` 目录，不覆盖原始 Episode。
