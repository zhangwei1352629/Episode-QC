# Design QA — AI 分界点精调

- Source visual truth: `/home/zw/.codex/generated_images/01a01508-5a80-78b3-a60d-92d4b300e1e1/exec-dfafcac5-97dc-4079-af3a-22623c10e79e.png`
- Implementation capture: `in-app-browser://tab/6`（本轮 Codex 内置浏览器全页及时间轴局部截图）
- Preview URL: `http://127.0.0.1:8876/`
- Real-data verification URL: `http://127.0.0.1:8877/`
- Real-data source: `/home/zw/Desktop/AST-20260906-00012/episodes/episode_000001/episode.mcap`（独立测试库，只读引用源文件）
- Source pixels: 2171 × 724
- Browser verification viewport: 1440 × 900 CSS px，随后已恢复默认视口
- Browser capture: 1190 × 744 JPEG；时间轴局部捕获 646 × 253 JPEG
- Density normalization: 源图和实现均按 CSS 布局比例判断；实现局部图用于细节核对，不以浏览器截图缩放差异作为问题
- State: 本地 AI 整段预标注演示，第二个分界点选中并显示精调面板

## Full-view comparison evidence

- 信息结构与选定方向一致：结果视图、紧凑视图控制、单行 AI 分段、选中分界点、分界时间、帧号、吸附状态和左右段时长均在同一工作区内。
- 实现保留了现有 QC 的全局时间轴标题、I/O 选区和数据源同步信息，避免破坏已有高频流程。
- 未增加第二条标注进度轴；实现仍以现有播放滑杆作为唯一全局定位控件。

## Focused-region comparison evidence

- 分界点可点击宽度为 31 CSS px，视觉握柄为 9 CSS px；命中面积明显大于旧版，同时保持时间点视觉精度。
- 选中或拖动时，相邻两段同时提亮，并以强调色标识共享边界。
- 精调区显示左右标签、六位小数秒值、真实帧号、逐帧前移/后移、吸附到帧状态及左右段时长。
- 时间轴自身宽度为约 592 CSS px 时，容器查询将精调区切换为两列（`226.591px 339.886px`），没有按浏览器总宽度误排为四列。

## Findings and comparison history

### Iteration 1

- [P1] 中等宽度工作区的四列精调面板过于拥挤。
  - Evidence: QC 左右侧栏开启时，时间轴内容宽度约 592–711 CSS px；按浏览器宽度判断仍会使用四列。
  - Fix: 改为基于 `.timeline-panel` 自身宽度的容器查询；820 px 以下两列，590 px 以下单列。
  - Post-fix evidence: 592.287 px 宽度下实际列宽为 226.591 px 和 339.886 px，字段未截断。

### Iteration 2

- [P1] 拖动过程中逐帧吸附并同步加载画面，真实数据帧数较多时产生卡顿，而且拖完后不容易再次调整同一分界点。
  - Evidence: 真实 Episode 的优先相机缓存包含 1,330 帧；原交互在每次 `pointermove` 时吸附帧并触发 `seek`。
  - Fix: 拖动中使用连续时间预览并只重绘时间轴；松手时一次性吸附最近有效帧、定位画面并原子保存左右分段。
  - Post-fix evidence: 同一个真实分界点连续从 4.617 秒拖到 8.235 秒，再拖到 6.265 秒；最终保存为 6,264,983,236 ns，左右记录边界完全相等，画面定位到 F103。

### Iteration 3

- [P1] 31 px 宽命中区内偏离中心按下时，切分线会在首次移动时跳到指针中心，造成可见偏移。
  - Fix: 记录按下位置相对切分线中心的抓取偏移，并在整个拖动手势中保持该偏移。
  - Post-fix evidence: 从命中区左缘按住并向右拖 30 px，目标时间按轨道比例从 11.845 秒移动到 14.369 秒，不再先向左跳到指针位置。
- [P2] 隔离测试标签仅由英文编码临时生成展示名。
  - Fix: 测试标签、时间轴分段和有效标注列表统一改用中文展示名；编码保持不变。

### Iteration 4

- [P1] 鼠标左键刚按下、尚未移动时，切分条立即向右偏移约半个命中区宽度。
  - Root cause: 全局 `button:active` 样式覆盖了切分条的 `translateX(-50%)`，使 31 px 命中区失去中心定位。
  - Fix: 为切分条按下状态显式保留 `translateX(-50%)`；按下前后中心位置不变。

### Iteration 5

- [P2] 时间轴虽然已有真实帧信息，但秒数仍占据主视觉层级，不符合逐帧复检的操作习惯。
  - Fix: 播放读数、时间轴刻度、AI 分段、选区和分界精调统一改为帧号优先；秒数降为辅助信息，底层仍以纳秒保存。

### Iteration 6

- [P1] 单行 AI 分段缺少明确的多层语义，附加质量标签容易被误认为不再支持，且同属 AI 来源时可能破坏主分段识别。
  - Fix: 仅将连续互斥的动作/步骤标签归入“动作分段”；其余 AI、人工、质量和时间点标签按原标签轨道显示，并增加“附加标注 · 可重叠”层级提示。

### Final pass

- No actionable P0/P1/P2 visual or interaction findings remain.
- P3: 真实长标签在极短分段中仍可能省略显示；完整名称保留在辅助文本和悬停提示中，属于可接受约束。

## Required fidelity surfaces

- Fonts and typography: 复用 Inter、Noto Sans SC、Microsoft YaHei；分段名称、时间和精调数据采用清晰层级与等宽数字表现。
- Spacing and layout rhythm: 复用现有 8–11 px 紧凑节奏；精调区使用轻分隔而非嵌套卡片，并完成两列/单列响应式检查。
- Colors and visual tokens: 复用现有近黑背景、石墨边线和 `#cff45a` 强调色；相邻分段保持标签原色并仅在编辑时提亮。
- Image quality and asset fidelity: 此组件没有需要生成或替换的位图、品牌图或装饰资产；未使用占位图。
- Copy and content: 使用“AI 分段”“分界时间”“吸附到帧”“相邻段时长”等与当前质检语义一致的中文文案。

## Primary interactions tested

- 点击共享分界点可打开精调面板。
- 31 px 命中区域可直接拖动，左右分段原子联动且松手保存。
- 真实数据同一分界点可连续拖动两次；拖动中不读取视频，松手后只读取最终真实帧。
- 逐帧按钮将分界点从 1.353333 秒移动到真实 F3（2.000000 秒），左右段同步更新且无空帧/重叠。
- 不可继续移动的方向会禁用，避免越过相邻段边界。
- 页面控制台错误：0。
- Frontend automated tests: 78 passed.

final result: passed
