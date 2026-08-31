const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const esbuild = require("esbuild");

test("完整前端入口可以解析和打包", () => {
  const entryPoint = path.resolve(__dirname, "../renderer/renderer.js");

  assert.doesNotThrow(() => {
    esbuild.buildSync({
      entryPoints: [entryPoint],
      bundle: true,
      format: "esm",
      platform: "browser",
      write: false,
      logLevel: "silent",
    });
  });
});

test("任务化页面包含当前任务、任务中心和按任务导出", () => {
  const html = fs.readFileSync(path.resolve(__dirname, "../renderer/index.html"), "utf8");
  const renderer = fs.readFileSync(path.resolve(__dirname, "../renderer/renderer.js"), "utf8");
  const webApi = fs.readFileSync(path.resolve(__dirname, "../renderer/web-api.js"), "utf8");

  assert.match(html, /id="current-task-name"/);
  assert.match(html, /id="task-center"/);
  assert.match(html, /id="rescan-task"/);
  assert.match(renderer, /taskId: state\.currentTaskId/);
  assert.match(renderer, /const episodeIds = state\.episodes/);
  assert.match(webApi, /\/api\/tasks\/import/);
  assert.match(webApi, /task_id=/);
});

test("本地任务直接读取 QC 服务器目录且不再包含 Worker 链路", () => {
  const html = fs.readFileSync(path.resolve(__dirname, "../renderer/index.html"), "utf8");
  const renderer = fs.readFileSync(path.resolve(__dirname, "../renderer/renderer.js"), "utf8");
  const webApi = fs.readFileSync(path.resolve(__dirname, "../renderer/web-api.js"), "utf8");

  assert.match(renderer, /const result = await window\.episodeQc\.addSource\(taskKind\)/);
  assert.match(webApi, /QC 服务器可访问的绝对路径或已挂载 NAS 目录/);
});

test("Flow 任务中心包含登录、领取、缓存和提交入口", () => {
  const html = fs.readFileSync(path.resolve(__dirname, "../renderer/index.html"), "utf8");
  const renderer = fs.readFileSync(path.resolve(__dirname, "../renderer/renderer.js"), "utf8");
  const webApi = fs.readFileSync(path.resolve(__dirname, "../renderer/web-api.js"), "utf8");

  assert.match(html, /id="flow-login-form"/);
  assert.match(html, /id="refresh-flow-reviewers"/);
  assert.match(html, /id="flow-reviewer-select"/);
  assert.match(html, /id="flow-task-list"/);
  assert.match(html, /id="submit-flow-task"/);
  assert.match(renderer, /refreshPlatformJobs/);
  assert.match(renderer, /loadPlatformReviewers/);
  assert.match(renderer, /function flowJobStatusName/);
  assert.match(renderer, /claimPlatformJob/);
  assert.match(renderer, /job\.claimable === false/);
  assert.match(renderer, /claim_blocked_reason/);
  assert.match(renderer, /groupFlowClaimPoolJobs/);
  assert.match(renderer, /data-flow-task-key/);
  assert.match(renderer, /expandedFlowTaskKeys/);
  assert.match(html, /Flow 任务池/);
  assert.match(html, /id="task-center-toast-stack"/);
  assert.match(renderer, /startPlatformJob/);
  assert.match(webApi, /\/api\/platform\/reviewers/);
  assert.match(webApi, /\/api\/platform\/login/);
  assert.match(webApi, /\/api\/platform\/jobs/);
});

test("质检界面使用可读中文、完整人员身份和紧凑路径", () => {
  const html = fs.readFileSync(path.resolve(__dirname, "../renderer/index.html"), "utf8");
  const renderer = fs.readFileSync(path.resolve(__dirname, "../renderer/renderer.js"), "utf8");

  assert.match(html, /<title>DataOps · Episode 质检<\/title>/);
  assert.match(html, /实际执行姿态（Policy，默认）/);
  assert.doesNotMatch(html, />EPISODE QC</);
  assert.match(renderer, /reviewer\.display_name.*reviewer\.employee_no.*reviewer\.team_name/);
  assert.match(renderer, /function compactSourcePath/);
  assert.match(renderer, /compactSourcePath\(taskPath\)/);
});

test("单机模式隐藏 Flow 并直接导入 QC 服务器目录", () => {
  const html = fs.readFileSync(path.resolve(__dirname, "../renderer/index.html"), "utf8");
  const renderer = fs.readFileSync(path.resolve(__dirname, "../renderer/renderer.js"), "utf8");
  const css = fs.readFileSync(path.resolve(__dirname, "../renderer/styles.css"), "utf8");

  assert.match(html, /id="flow-task-panel"/);
  assert.match(html, /id="local-task-title"/);
  assert.match(renderer, /platform\.enabled === false/);
  assert.match(renderer, /单机 QC 任务/);
  assert.match(renderer, /const result = await window\.episodeQc\.addSource\(taskKind\)/);
  assert.match(css, /\.task-center-dialog\.standalone-mode \.task-center-columns/);
});

test("Ego 任务入口复用五路相机和 SMPL Pose 骨架工作区", () => {
  const html = fs.readFileSync(path.resolve(__dirname, "../renderer/index.html"), "utf8");
  const renderer = fs.readFileSync(path.resolve(__dirname, "../renderer/renderer.js"), "utf8");
  const viewer = fs.readFileSync(path.resolve(__dirname, "../renderer/g1-viewer.js"), "utf8");
  const webApi = fs.readFileSync(path.resolve(__dirname, "../renderer/web-api.js"), "utf8");
  const css = fs.readFileSync(path.resolve(__dirname, "../renderer/styles.css"), "utf8");

  assert.match(html, /id="add-ego-source"/);
  assert.match(html, /id="task-center-import-ego"/);
  assert.match(html, /id="motion-controls-toggle"[^>]+aria-expanded="false"/);
  assert.match(html, /id="motion-control-panel" hidden/);
  assert.match(renderer, /addSource\("ego_omniego"\)/);
  assert.match(renderer, /人体 Pose 骨架/);
  assert.match(renderer, /function setMotionControlsExpanded/);
  assert.match(renderer, /viewerProfile: isEgoTask\(\) \? "ego_omniego"/);
  assert.match(viewer, /humanPoseMode \? 4\.15 : 3\.15/);
  assert.match(webApi, /每个 MCAP 文件作为一个 Episode/);
  assert.match(css, /\.motion-control-panel\[hidden\]\s*\{\s*display:\s*none/);
});

test("标签库菜单和本地任务历史管理入口完整", () => {
  const html = fs.readFileSync(path.resolve(__dirname, "../renderer/index.html"), "utf8");
  const renderer = fs.readFileSync(path.resolve(__dirname, "../renderer/renderer.js"), "utf8");
  const webApi = fs.readFileSync(path.resolve(__dirname, "../renderer/web-api.js"), "utf8");

  assert.match(html, /<summary class="button secondary">标签库<\/summary>/);
  assert.match(html, /id="label-set-list"/);
  assert.match(html, /id="clear-local-task-history"/);
  assert.match(renderer, /handleLabelSetAction/);
  assert.match(renderer, /clearLocalTaskHistory/);
  assert.match(webApi, /\/api\/label-sets/);
  assert.match(webApi, /\/api\/tasks\/history/);
});

test("收起任务栏后标注区使用双栏布局并优化备注、当前标注和结论", () => {
  const html = fs.readFileSync(path.resolve(__dirname, "../renderer/index.html"), "utf8");
  const css = fs.readFileSync(path.resolve(__dirname, "../renderer/styles.css"), "utf8");

  assert.match(html, /class="annotation-note"/);
  assert.match(html, /id="decision-current"/);
  assert.match(css, /body\.episodes-collapsed \.label-sidebar\s*\{[^}]*grid-template-columns:/s);
  assert.match(css, /body\.episodes-collapsed \.label-section\s*\{[^}]*grid-column:\s*1/s);
  assert.match(css, /body\.episodes-collapsed \.annotations-section\s*\{[^}]*grid-column:\s*2/s);
  assert.match(css, /\.annotation-note textarea:focus\s*\{[^}]*min-height:/s);
  assert.match(css, /\.decision-copy small\s*\{[^}]*display:\s*none/s);
});

test("本条有效标注默认展开并保留手动收起能力", () => {
  const html = fs.readFileSync(path.resolve(__dirname, "../renderer/index.html"), "utf8");
  const renderer = fs.readFileSync(path.resolve(__dirname, "../renderer/renderer.js"), "utf8");
  const css = fs.readFileSync(path.resolve(__dirname, "../renderer/styles.css"), "utf8");

  assert.match(html, /id="toggle-current-annotations"[^>]*aria-expanded="true"/);
  assert.match(renderer, /function setCurrentAnnotationsExpanded\(expanded, persist = true\)/);
  assert.match(renderer, /episodeQcCurrentAnnotationsExpanded/);
  assert.match(css, /\.label-sidebar\s*\{[^}]*grid-template-rows:\s*minmax\(0, 1fr\) auto auto/s);
  assert.match(css, /\.annotations-section:not\(\.expanded\)[^{]*\.annotation-list\s*\{\s*display:\s*none/s);
});

test("顶栏持续显示当前任务、标签版本和质检轮次", () => {
  const html = fs.readFileSync(path.resolve(__dirname, "../renderer/index.html"), "utf8");
  const renderer = fs.readFileSync(path.resolve(__dirname, "../renderer/renderer.js"), "utf8");
  const css = fs.readFileSync(path.resolve(__dirname, "../renderer/styles.css"), "utf8");

  assert.match(html, /id="header-task-summary"/);
  assert.match(html, /id="header-label-version"/);
  assert.match(html, /id="header-review-round"/);
  assert.match(renderer, /function renderHeaderContext\(\)/);
  assert.match(renderer, /currentReviewRound\(episode\)/);
  assert.match(css, /\.header-task-context\s*\{/);
});

test("时间轴结果筛选向辅助技术回报当前选中状态", () => {
  const html = fs.readFileSync(path.resolve(__dirname, "../renderer/index.html"), "utf8");
  const renderer = fs.readFileSync(path.resolve(__dirname, "../renderer/renderer.js"), "utf8");

  assert.match(html, /data-timeline-view="effective"[^>]*aria-pressed="true"/);
  assert.match(html, /data-timeline-view="changes"[^>]*aria-pressed="false"/);
  assert.match(renderer, /item\.setAttribute\("aria-pressed", String\(item === button\)\)/);
});

test("空 Episode 仍提供可拖拽选区且保存后清空已消费区间", () => {
  const renderer = fs.readFileSync(path.resolve(__dirname, "../renderer/renderer.js"), "utf8");

  assert.match(renderer, /timeline-empty annotation-lane-surface/);
  assert.match(renderer, /resetRangeSelection\(\);/);
  assert.match(renderer, /请先按 I 设置区间起点/);
});
