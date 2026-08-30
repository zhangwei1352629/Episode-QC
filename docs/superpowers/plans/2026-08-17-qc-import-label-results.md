# QC 导入任务自动恢复目录内标注结果

**Goal:** 当任务文件夹已包含 `*标注结果.json` / `qc_result.json` 时，`/api/tasks/import`（含 rescan）重新导入任务后自动还原可显示的标注、Episode 质检状态与质量结论。

**Architecture:** 在 `scan_data_source` 完成 `episode` 扫描后，新增一次“结果文件扫描-恢复结果”的步骤。恢复逻辑独立封装到 `workspace.py`，通过任务数据源 `source_id + relative_path` 找到对应 episode 后恢复尚未开始的 Episode 状态，插入/更新 annotation 记录，并刷新 `annotation_count`。

**Tech Stack:** Python、sqlite3、现有 Episode-QC 仓库测试框架

## Global Constraints

- 不引入新外部依赖。
- 恢复失败不应阻塞任务导入；仅跳过无效标注并返回警告。
- 不改变现有扫描/导入任务逻辑的核心行为。

### Task 1: 添加结果文件发现与解析的工作区层函数

**Files:**
- Modify: `src/episode_qc/workspace.py`

**Implementations/Steps**

- [ ] **Step 1: 写入失败场景前置测试（回归）**

  - 在 `tests/test_workspace_v1.py` 增加测试：任务目录已有导出 JSON 时，`scan_data_source` 会把标注恢复为 `episode.annotation_count > 0` 且 `episode_detail` 包含对应 `label_code`。

- [ ] **Step 2: 先编写最小函数并验证测试失败**

  - 在 `src/episode_qc/workspace.py` 新增结果文件发现/解析的骨架函数，先保证格式不匹配时安全返回空列表。

- [ ] **Step 3: 实现恢复逻辑（幂等）**

  - 扫描任务根目录优先文件名匹配 `qc_result.json` 与 `*标注结果.json`。
  - 解析 `annotations` 并按 `relative_episode_path` / `relative_path` 映射到 episode。
  - 验证时间范围并写入 annotations（或更新同 ID 标注），刷新 `annotation_count`。
  - 在导入结果不可用或非法时仅记录警告，不中断任务导入。

- [ ] **Step 4: 在 `scan_data_source`/`rescan_qc_task` 路径挂载恢复**

  - `scan_data_source` 在重建 episode 后调用新函数。
  - 通过返回值将恢复数量带入结果（如 `restored_annotations`）。

### Task 2: 验证并更新前后端展示链路

**Files:**
- Modify: `src/episode_qc/workspace.py`
- Modify: `tests/test_workspace_v1.py`
- Optionally: `tests/test_web_server.py`

- [ ] **Step 1: 补充 web 场景测试（如有需要）**

  - 验证 `/api/tasks/import` 后调用 `/api/episodes/<id>` 能获取恢复的标注。

- [ ] **Step 2: 运行目标测试并修复边界问题**

  - `pytest tests/test_workspace_v1.py -k import`（或相应新增测试名）
  - 如涉及 web 测试再补全并执行 `pytest tests/test_web_server.py -k import`

- [ ] **Step 3: 产出结果并报告行为变化**

  - 导入任务目录若有结果文件则恢复注释；无文件或非法文件时原有导入行为不变。
