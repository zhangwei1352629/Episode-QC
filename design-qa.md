# Episode QC 增量质检页面设计验收

## 对比基准

- source visual truth: `/home/zw/.codex/generated_images/019ff3c8-e4c2-7d61-9670-44b4dcc6c451/exec-6ea3a982-13e5-415d-a8e2-e14ba979ae7d.png`
- implementation URL: `http://127.0.0.1:8770/`
- implementation screenshot: `/tmp/episode-qc-ui-final.png`
- combined comparison: `/tmp/episode-qc-ui-final-comparison.png`
- source pixels: `1586 × 992`
- implementation runtime CSS viewport: `1600 × 960`
- implementation raw browser capture: `3200 × 1920`
- density normalization: IAB 对固定全屏/WebGL 页面返回 4 倍密度平铺截图；取首个 `800 × 480` 完整渲染单元并归一到 `1600 × 960`。源图同步归一到 `1600 × 960` 后并排比较。
- state: 深色主题；Flow 任务 `QCJ-20260825-00006`；标签 `V3.0.0`；当前 `R3`；8 条 Episode；选中第一条；6 路相机、G1 29DOF、Mocap、历史 R1/R2 与本轮 R3 数据齐备。

## Full-view comparison evidence

- 页面保持设计稿的三栏信息架构：左侧任务/Episode，中间 G1 + 六路相机 + 时间轴，右侧标签/有效标注/Episode 结论。
- 顶栏持续展示任务编号、任务名称、标签版本和当前轮次；右侧工具入口保持原产品能力。
- 六路相机采用 `3 × 2`，G1 29DOF 独立显示；相机和 Mocap 覆盖信息合并为单条“数据源同步”。
- 底部保持“本轮变更”、Episode 级“确认本条并继续”和任务级唯一“提交本轮质检到 Flow”。
- 颜色继续使用项目既有深色底、青灰分隔与黄绿色主操作色；状态标签使用蓝、橙、绿、红、紫语义色。

## Focused region comparison evidence

- right sidebar: 历史标签直接出现在当前标签区和“本条有效标注”列表，显示 `R1`、`R2`、`R1→R3 已修改`、`本轮新增 · R3`；没有另设拥挤的只读历史面板。
- timeline + footer: 仅一条数据源同步轨道；标注按标签合并成有效结果轨道；当前有效、本轮变更、历史来源三种视图可切换；底部汇总新增、修改、移除、原样保留。
- typography: 使用项目既有 `Inter / Noto Sans SC / Microsoft YaHei` 字体栈；任务、Episode、标签、状态、辅助文字层级与设计稿一致，未出现遮挡或不可读换行。
- image quality: G1 使用现有 URDF/WebGL 资产；本机验收相机画面是临时合成测试帧，用于验证六路布局和帧读取，不代表生产视频内容。
- copy: “本条有效标注”“历史标注已合并到当前结果”“确认本条并继续”“提交本轮质检到 Flow”等文案区分 Episode 级与整任务级操作。

## Interaction evidence

- 6 个相机卡片均加载，G1 29DOF 单独可见。
- “数据源同步”DOM 行数为 1；上一轮独立面板 DOM 行数为 0；Flow 提交按钮 DOM 行数为 1。
- 历史 R1 标注可打开编辑器，编辑器明确提示“保存后记为 R3 变更”；保存后变为 `R1→R3 已修改`。
- 当前有效 / 本轮变更 / 历史来源分别显示 5 / 3 / 4 个标注块；筛选按钮正确更新 `aria-pressed`。
- “确认本条并继续”从 `episode_000001` 正常切换到 `episode_000002`。
- 任务未全部完成时 Flow 提交按钮保持禁用且显示“完成全部 Episode 后提交”。
- 浏览器控制台错误数：0。

## Findings

- 无未解决的 P0 / P1 / P2 问题。
- P3: 设计稿使用真实冰箱任务画面，本机验收使用合成帧；这是测试数据差异，生产数据加载后自动显示真实六路画面，不需要修改 UI。
- P3: 品牌标记继续沿用项目既有 `DQ` 资产，而非设计图中的六边形图形；保持了当前产品资产一致性。

## Comparison history

1. first comparison
   - P2: 顶栏没有持续显示任务编号、标签版本和当前轮次，用户切换 Episode 后缺少轮次上下文。
   - fix: 增加动态任务摘要、`标签 Vx.y.z`、`当前 Rn`，并在任务/Episode 切换时刷新。
2. second comparison
   - P2: 时间轴结果筛选只有视觉 active 状态，辅助技术无法获知当前选择。
   - fix: 为三种筛选补充初始 `aria-pressed`，点击时同步更新。
3. post-fix comparison
   - 顶栏、三栏布局、六路相机、单数据源轨道、增量标注、结论与底部双层操作结构均与目标信息架构一致；未发现新的 P0 / P1 / P2。

## Implementation checklist

- [x] 六路相机 + 独立 G1 29DOF
- [x] 相机/Mocap 合并成一条数据源同步轨道
- [x] 多轮历史标注合并进当前有效结果并可修改/删除
- [x] 标签和时间轴显示来源轮次及本轮变更
- [x] Episode 级确认与任务级唯一 Flow 提交区分
- [x] 顶栏显示任务、标签版本与当前轮次
- [x] 页面交互、无控制台错误、自动化回归通过

final result: passed
