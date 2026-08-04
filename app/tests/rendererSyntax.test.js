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

test("客户端 Data Worker 链路由浏览器本机读取并由中央保存任务", () => {
  const html = fs.readFileSync(path.resolve(__dirname, "../renderer/index.html"), "utf8");
  const renderer = fs.readFileSync(path.resolve(__dirname, "../renderer/renderer.js"), "utf8");
  const webApi = fs.readFileSync(path.resolve(__dirname, "../renderer/web-api.js"), "utf8");

  assert.match(html, /connect-src[^;]*127\.0\.0\.1:\*/);
  assert.match(renderer, /addLocalWorkerSource/);
  assert.match(renderer, /remote_episode_id/);
  assert.match(renderer, /source_type === "client_worker"/);
  assert.match(webApi, /\/api\/worker\/info/);
  assert.match(webApi, /\/api\/tasks\/register-worker/);
  assert.match(webApi, /onWorkerEpisodeCacheReady/);
  assert.match(webApi, /episodeQcWorker:/);
});
