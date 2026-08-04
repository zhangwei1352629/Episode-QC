import { decodeMotionFrame, decodeRobotActionFrame } from "./playback-binary.mjs";

installWebApi();

function installWebApi() {
  const query = new URLSearchParams(window.location.search);
  const incomingToken = query.get("token");
  if (incomingToken) {
    window.sessionStorage.setItem("episodeQcToken", incomingToken);
    window.history.replaceState({}, "", `${window.location.pathname}${window.location.hash}`);
  }
  const token = incomingToken || window.sessionStorage.getItem("episodeQcToken") || "";
  const cacheByEpisode = new Map();

  async function request(path, { method = "GET", body, binary = false } = {}) {
    const headers = { "X-Episode-QC-Token": token };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
    if (!response.ok) {
      let message = `${response.status} ${response.statusText}`;
      try { message = (await response.json()).error || message; } catch { /* non-JSON error */ }
      throw new Error(message);
    }
    if (response.status === 204) return null;
    return binary ? response : response.json();
  }

  function normalizeWorkerUrl(value) {
    let parsed;
    try { parsed = new URL(String(value || "").trim()); } catch { throw new Error("Data Worker 地址无效"); }
    if (
      parsed.protocol !== "http:"
      || !["127.0.0.1", "localhost", "[::1]"].includes(parsed.hostname)
      || !parsed.port
      || (parsed.pathname !== "/" && parsed.pathname !== "")
      || parsed.username
      || parsed.password
      || parsed.search
      || parsed.hash
    ) {
      throw new Error("Data Worker 必须使用本机地址，例如 http://127.0.0.1:8766");
    }
    return parsed.origin;
  }

  async function workerRequest(provider, path, { method = "GET", body, binary = false } = {}) {
    if (!provider?.token) throw new Error("缺少此电脑 Data Worker 的访问令牌");
    const baseUrl = normalizeWorkerUrl(provider.url);
    const headers = { "X-Episode-QC-Token": provider.token };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    let response;
    try {
      response = await fetch(`${baseUrl}${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        cache: "no-store",
      });
    } catch {
      throw new Error(`无法连接本机 Data Worker（${baseUrl}）。请先在数据所在电脑启动 Worker`);
    }
    if (!response.ok) {
      let message = `${response.status} ${response.statusText}`;
      try { message = (await response.json()).error || message; } catch { /* non-JSON error */ }
      if (response.status === 401) message = "Data Worker 访问令牌无效，请重新连接";
      throw new Error(message);
    }
    if (response.status === 204) return null;
    return binary ? response : response.json();
  }

  function workerStorageKey(workerId) {
    return `episodeQcWorker:${workerId}`;
  }

  function rememberWorker(provider) {
    window.localStorage.setItem(workerStorageKey(provider.workerId), JSON.stringify(provider));
    window.localStorage.setItem("episodeQcWorkerUrl", provider.url);
    window.localStorage.setItem(`episodeQcWorkerToken:${provider.url}`, provider.token);
  }

  function storedWorker(task) {
    if (!task?.worker_id) return null;
    try {
      const provider = JSON.parse(window.localStorage.getItem(workerStorageKey(task.worker_id)) || "null");
      if (!provider || provider.workerId !== task.worker_id) return null;
      return { ...provider, url: task.worker_url || provider.url };
    } catch {
      return null;
    }
  }

  async function verifyWorker(provider, expectedWorkerId = null) {
    const response = await workerRequest(provider, "/api/worker/info");
    const worker = response?.worker;
    if (!worker?.id || expectedWorkerId && worker.id !== expectedWorkerId) {
      throw new Error("当前 Data Worker 与此任务所属电脑不一致");
    }
    const verified = {
      workerId: worker.id,
      name: worker.name || worker.id,
      url: normalizeWorkerUrl(provider.url),
      token: provider.token,
    };
    rememberWorker(verified);
    return verified;
  }

  async function requireWorker(task) {
    const saved = storedWorker(task);
    if (saved) {
      try { return await verifyWorker(saved, task.worker_id); } catch { /* ask for a renewed token below */ }
    }
    const url = normalizeWorkerUrl(task?.worker_url || saved?.url || window.localStorage.getItem("episodeQcWorkerUrl") || "http://127.0.0.1:8766");
    const rememberedToken = saved?.token || window.localStorage.getItem(`episodeQcWorkerToken:${url}`) || "";
    const workerToken = window.prompt(
      "此任务的数据在当前电脑。请输入 Data Worker 启动窗口显示的访问令牌",
      rememberedToken,
    );
    if (workerToken === null || !workerToken.trim()) throw new Error("未填写 Data Worker 访问令牌");
    return verifyWorker({ url, token: workerToken.trim() }, task.worker_id);
  }

  async function registerWorkerManifest(provider, remoteTaskId) {
    const manifest = await workerRequest(
      provider,
      `/api/tasks/${encodeURIComponent(remoteTaskId)}/manifest`,
    );
    return request("/api/tasks/register-worker", {
      method: "POST",
      body: {
        worker: { id: provider.workerId, name: provider.name, url: provider.url },
        manifest,
      },
    });
  }

  function frameMetadata(response) {
    return {
      frameOffsetNs: Number(response.headers.get("X-Frame-Offset-Ns")),
      skewNs: Number(response.headers.get("X-Frame-Skew-Ns")),
      frameIndex: Number(response.headers.get("X-Frame-Index")),
      endOfStream: response.headers.get("X-End-Of-Stream") === "1",
    };
  }

  const api = {
    getWorkspaceState: (taskId) => request(
      `/api/workspace${taskId ? `?task_id=${encodeURIComponent(taskId)}` : ""}`,
    ),
    getTasks: () => request("/api/tasks"),
    updateWorkspaceSettings: (value) => request("/api/workspace/settings", { method: "POST", body: value }),
    addSource: async () => {
      const previous = window.localStorage.getItem("episodeQcSourcePath") || "";
      const rootPath = window.prompt(
        "请输入 QC 服务器可访问的绝对路径或已挂载 NAS 目录",
        previous,
      );
      if (rootPath === null || !rootPath.trim()) return null;
      window.localStorage.setItem("episodeQcSourcePath", rootPath.trim());
      return request("/api/tasks/import", { method: "POST", body: { rootPath: rootPath.trim() } });
    },
    addSourcePath: (rootPath) => request("/api/tasks/import", { method: "POST", body: { rootPath } }),
    addLocalWorkerSource: async () => {
      const previousUrl = window.localStorage.getItem("episodeQcWorkerUrl") || "http://127.0.0.1:8766";
      const enteredUrl = window.prompt("请输入本机 Data Worker 地址", previousUrl);
      if (enteredUrl === null || !enteredUrl.trim()) return null;
      const url = normalizeWorkerUrl(enteredUrl);
      const previousToken = window.localStorage.getItem(`episodeQcWorkerToken:${url}`) || "";
      const workerToken = window.prompt("请输入 Data Worker 启动窗口显示的访问令牌", previousToken);
      if (workerToken === null || !workerToken.trim()) return null;
      const provider = await verifyWorker({ url, token: workerToken.trim() });
      const previousPath = window.localStorage.getItem(`episodeQcLocalSourcePath:${provider.workerId}`) || "";
      const rootPath = window.prompt(
        "请输入当前电脑上的数据绝对路径（数据不会上传到 QC 服务器）",
        previousPath,
      );
      if (rootPath === null || !rootPath.trim()) return null;
      window.localStorage.setItem(`episodeQcLocalSourcePath:${provider.workerId}`, rootPath.trim());
      const indexed = await workerRequest(provider, "/api/tasks/import", {
        method: "POST",
        body: { rootPath: rootPath.trim() },
      });
      const registered = await registerWorkerManifest(provider, indexed.task_id);
      return { ...registered, workerName: provider.name, remoteResult: indexed };
    },
    rescanTask: (taskId) => request(`/api/tasks/${encodeURIComponent(taskId)}/rescan`, { method: "POST" }),
    rescanWorkerTask: async (task) => {
      const provider = await requireWorker(task);
      const indexed = await workerRequest(
        provider,
        `/api/tasks/${encodeURIComponent(task.remote_task_id)}/rescan`,
        { method: "POST" },
      );
      const registered = await registerWorkerManifest(provider, task.remote_task_id);
      return { ...registered, workerName: provider.name, remoteResult: indexed };
    },
    requireWorker,
    getEpisode: (episodeId) => request(`/api/episodes/${encodeURIComponent(episodeId)}`),
    prepareEpisode: async (episodeId, provider = null) => {
      const cache = provider
        ? await workerRequest(provider, `/api/episodes/${encodeURIComponent(episodeId)}/cache`, { method: "POST" })
        : await request(`/api/episodes/${encodeURIComponent(episodeId)}/cache`, { method: "POST" });
      cacheByEpisode.set(episodeId, cache);
      return cache;
    },
    getCameraFrame: async ({ episodeId, streamId, timeNs, provider = null }) => {
      const path = `/api/episodes/${encodeURIComponent(episodeId)}/cameras/${encodeURIComponent(streamId)}/frame?time_ns=${Math.round(timeNs)}`;
      const response = provider
        ? await workerRequest(provider, path, { binary: true })
        : await request(path, { binary: true });
      return { dataUrl: URL.createObjectURL(await response.blob()), ...frameMetadata(response) };
    },
    getMotionFrame: async ({ episodeId, timeNs, provider = null }) => {
      const path = `/api/episodes/${encodeURIComponent(episodeId)}/motion/frame?time_ns=${Math.round(timeNs)}`;
      const response = provider
        ? await workerRequest(provider, path, { binary: true })
        : await request(path, { binary: true });
      if (!response) return null;
      const cache = cacheByEpisode.get(episodeId);
      const motion = cache?.motion;
      if (!motion?.available) return null;
      const frame = decodeMotionFrame(await response.arrayBuffer(), motion.joint_names.length);
      return {
        ...frame,
        ...frameMetadata(response),
        jointNames: motion.joint_names,
        parentIndices: motion.parent_indices,
        coordinateFrame: motion.coordinate_frame,
        units: motion.units,
      };
    },
    getRobotActionFrame: async ({ episodeId, sourceKey, timeNs, provider = null }) => {
      const path = `/api/episodes/${encodeURIComponent(episodeId)}/actions/${encodeURIComponent(sourceKey)}/frame?time_ns=${Math.round(timeNs)}`;
      const response = provider
        ? await workerRequest(provider, path, { binary: true })
        : await request(path, { binary: true });
      if (!response) return null;
      const cache = cacheByEpisode.get(episodeId);
      const frame = decodeRobotActionFrame(await response.arrayBuffer(), sourceKey);
      return {
        ...frame,
        ...frameMetadata(response),
        jointPositions: frame.joint_positions,
        rootPosition: frame.root_position,
        rootQuaternionWxyz: frame.root_quaternion_wxyz,
        jointNames: cache?.robot_actions?.joint_names || [],
      };
    },
    onEpisodeCacheReady: (callback) => {
      const events = new EventSource(`/api/events?token=${encodeURIComponent(token)}`);
      events.onmessage = (event) => {
        const payload = JSON.parse(event.data);
        if (payload.cache && payload.episodeId) cacheByEpisode.set(payload.episodeId, payload.cache);
        callback(payload);
      };
      return () => events.close();
    },
    onWorkerEpisodeCacheReady: (provider, callback) => {
      const baseUrl = normalizeWorkerUrl(provider.url);
      const events = new EventSource(`${baseUrl}/api/events?token=${encodeURIComponent(provider.token)}`);
      events.onmessage = (event) => {
        const payload = JSON.parse(event.data);
        if (payload.cache && payload.episodeId) cacheByEpisode.set(payload.episodeId, payload.cache);
        callback(payload);
      };
      return () => events.close();
    },
    importLabelSchema: async () => {
      const previous = window.localStorage.getItem("episodeQcLabelSchemaPath") || "";
      const schemaPath = window.prompt("请输入标签库文件的绝对路径。推荐使用页面上方下载的中文简易模板，只需填写标签名称。", previous);
      if (schemaPath === null || !schemaPath.trim()) return null;
      window.localStorage.setItem("episodeQcLabelSchemaPath", schemaPath.trim());
      return request("/api/label-schema/preview", { method: "POST", body: { schemaPath: schemaPath.trim() } });
    },
    confirmLabelSchema: () => request("/api/label-schema/import", { method: "POST" }),
    saveAnnotation: (value) => request("/api/annotations", { method: "POST", body: value }),
    deleteAnnotation: (annotationId) => request(`/api/annotations/${encodeURIComponent(annotationId)}`, { method: "DELETE" }),
    undo: () => request("/api/undo", { method: "POST" }),
    redo: () => request("/api/redo", { method: "POST" }),
    updateReview: ({ episodeId, ...value }) => request(
      `/api/episodes/${encodeURIComponent(episodeId)}/review`,
      { method: "POST", body: value },
    ),
    exportWorkspace: async (value) => {
      let outputParent = value?.outputParent;
      if (!outputParent) {
        const previous = window.localStorage.getItem("episodeQcExportPath") || "";
        outputParent = window.prompt("请输入标注结果保存目录的绝对路径", previous);
        if (outputParent === null || !outputParent.trim()) return null;
        outputParent = outputParent.trim();
        window.localStorage.setItem("episodeQcExportPath", outputParent);
      }
      return request("/api/export", { method: "POST", body: { ...value, outputParent } });
    },
  };

  window.episodeQc = api;
}
