import { decodeMotionFrame, decodeRobotActionFrame } from "./playback-binary.mjs";

if (!window.episodeQc) {
  installWebApi();
}

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

  function frameMetadata(response) {
    return {
      frameOffsetNs: Number(response.headers.get("X-Frame-Offset-Ns")),
      skewNs: Number(response.headers.get("X-Frame-Skew-Ns")),
      frameIndex: Number(response.headers.get("X-Frame-Index")),
      endOfStream: response.headers.get("X-End-Of-Stream") === "1",
    };
  }

  const api = {
    getWorkspaceState: () => request("/api/workspace"),
    updateWorkspaceSettings: (value) => request("/api/workspace/settings", { method: "POST", body: value }),
    addSource: async () => {
      const previous = window.localStorage.getItem("episodeQcSourcePath") || "";
      const rootPath = window.prompt("请输入本机数据源目录（服务端可访问的绝对路径）", previous);
      if (rootPath === null || !rootPath.trim()) return null;
      window.localStorage.setItem("episodeQcSourcePath", rootPath.trim());
      return request("/api/sources", { method: "POST", body: { rootPath: rootPath.trim() } });
    },
    addSourcePath: (rootPath) => request("/api/sources", { method: "POST", body: { rootPath } }),
    getEpisode: (episodeId) => request(`/api/episodes/${encodeURIComponent(episodeId)}`),
    prepareEpisode: async (episodeId) => {
      const cache = await request(`/api/episodes/${encodeURIComponent(episodeId)}/cache`, { method: "POST" });
      cacheByEpisode.set(episodeId, cache);
      return cache;
    },
    getCameraFrame: async ({ episodeId, streamId, timeNs }) => {
      const response = await request(
        `/api/episodes/${encodeURIComponent(episodeId)}/cameras/${encodeURIComponent(streamId)}/frame?time_ns=${Math.round(timeNs)}`,
        { binary: true },
      );
      return { dataUrl: URL.createObjectURL(await response.blob()), ...frameMetadata(response) };
    },
    getMotionFrame: async ({ episodeId, timeNs }) => {
      const response = await request(
        `/api/episodes/${encodeURIComponent(episodeId)}/motion/frame?time_ns=${Math.round(timeNs)}`,
        { binary: true },
      );
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
    getRobotActionFrame: async ({ episodeId, sourceKey, timeNs }) => {
      const response = await request(
        `/api/episodes/${encodeURIComponent(episodeId)}/actions/${encodeURIComponent(sourceKey)}/frame?time_ns=${Math.round(timeNs)}`,
        { binary: true },
      );
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
    importLabelSchema: async () => {
      const previous = window.localStorage.getItem("episodeQcLabelSchemaPath") || "";
      const schemaPath = window.prompt("请输入标签库 YAML、JSON 或 CSV 文件的绝对路径", previous);
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
        outputParent = window.prompt("请输入导出目录的绝对路径", previous);
        if (outputParent === null || !outputParent.trim()) return null;
        outputParent = outputParent.trim();
        window.localStorage.setItem("episodeQcExportPath", outputParent);
      }
      return request("/api/export", { method: "POST", body: { ...value, outputParent } });
    },
  };

  window.episodeQc = api;
}
