const $ = (selector) => document.querySelector(selector);
let refreshTimer = null;
let networkTimer = null;
let latestNetworkRate = null;
let previousNetworkSample = null;
let networkInterfaceName = "";
const fileSamples = new Map();

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    cache: "no-store",
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  return response.json();
}

function showDashboard() {
  $("#dashboard").hidden = false;
}

function badge(value) {
  const cls = ["uploaded", "finished"].includes(value) ? "ok" : ["failed"].includes(value) ? "bad" : "";
  return `<span class="badge ${cls}">${value}</span>`;
}

function statusText(value) {
  const labels = {
    recording: "录制中",
    finished: "录制完成",
    interrupted: "录制中断",
    recording_failed: "录制失败",
    not_started: "未开始投稿",
    waiting: "等待下播",
    pending: "等待投稿",
    uploading: "投稿中",
    uploaded: "已投稿",
    failed: "投稿失败",
    skipped: "不自动投稿",
    remuxing: "封装中",
    remuxed: "可预览",
    enabled: "已启用",
    disabled: "已暂停",
    auto_upload: "自动投稿",
    manual_upload: "只录制",
  };
  return labels[value] || value;
}

function statusBadge(value) {
  return badge(statusText(value));
}

function formatMBps(bytesPerSecond) {
  if (bytesPerSecond === null || Number.isNaN(bytesPerSecond)) {
    return "计算中";
  }
  return `${(bytesPerSecond / 1024 / 1024).toFixed(2)} MB/s`;
}

function formatBytes(bytes) {
  if (bytes === null || bytes === undefined || Number.isNaN(bytes)) {
    return "未知";
  }
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = Number(bytes);
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  const decimals = unitIndex === 0 ? 0 : 2;
  return `${value.toFixed(decimals)} ${units[unitIndex]}`;
}

function parseJsonList(value) {
  if (!value) {
    return [];
  }
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed.filter(Boolean) : [];
  } catch (error) {
    return [];
  }
}

function segmentText(recording) {
  const paths = parseJsonList(recording.segment_paths);
  const count = paths.length || (recording.file_path ? 1 : 0);
  const segmentHours = Number(recording.segment_hours || 0);
  if (!segmentHours && count <= 1) {
    return "";
  }
  const rule = segmentHours ? `每 ${segmentHours} 小时分段` : "未设置分段";
  return `<p class="meta">分段：${rule}，当前 ${count} P${recording.status === "recording" ? `，正在写入 P${recording.current_segment_index || count}` : ""}</p>`;
}

function previewButtons(recording) {
  const paths = parseJsonList(recording.mp4_paths);
  if (!paths.length) {
    return "";
  }
  return paths
    .map((_, index) => `<button class="secondary" onclick="openPreview(${recording.id}, ${index + 1})">预览 P${index + 1}</button>`)
    .join("");
}

function remuxText(recording) {
  if (recording.status === "recording") {
    return "";
  }
  if (recording.remux_status === "remuxed") {
    return `<p class="meta">MP4：已生成，可网页预览</p>`;
  }
  if (recording.remux_status === "remuxing") {
    return `<p class="meta">MP4：正在封装...</p>`;
  }
  if (recording.remux_status === "failed") {
    return `<p class="meta">MP4：封装失败</p>`;
  }
  return `<p class="meta">MP4：尚未生成</p>`;
}

function canManualUpload(recording) {
  if (recording.status === "recording") {
    return false;
  }
  if (["pending", "uploading", "uploaded"].includes(recording.upload_status)) {
    return false;
  }
  return Boolean(recording.file_path || recording.segment_paths || recording.mp4_paths);
}

function networkLine(recording) {
  if (recording.status !== "recording") {
    return "";
  }
  if (latestNetworkRate === null) {
    return `<p class="meta live-traffic" data-live-traffic>服务器下行：计算中</p>`;
  }
  return `<p class="meta live-traffic" data-live-traffic>服务器下行：${formatMBps(latestNetworkRate)} <span>${networkInterfaceName || "默认网卡"}</span></p>`;
}

function fileLine(recording) {
  if (recording.status !== "recording") {
    return "";
  }
  return `<p class="meta live-file" data-file-metric="${recording.id}">录制文件：计算中</p>`;
}

function renderFileMetric(id, sample) {
  const node = document.querySelector(`[data-file-metric="${id}"]`);
  if (!node) {
    return;
  }
  if (!sample || !sample.exists) {
    node.textContent = "录制文件：尚未生成";
    return;
  }
  const rateText = sample.rate === null ? "写入速度：计算中" : `写入速度：${formatMBps(sample.rate)}`;
  node.textContent = `录制文件：${formatBytes(sample.size_bytes)}，${rateText}`;
}

function updateTrafficNodes() {
  document.querySelectorAll("[data-live-traffic]").forEach((node) => {
    node.innerHTML = `服务器下行：${formatMBps(latestNetworkRate)} <span>${networkInterfaceName || "默认网卡"}</span>`;
  });
}

function setStatusMessage(id, message) {
  const node = document.querySelector(`[data-status-for="${id}"]`);
  if (node) {
    node.textContent = message;
  }
}

async function loadStreamers() {
  const streamers = await api("/api/streamers");
  $("#streamers").innerHTML =
    streamers
      .map(
        (s) => `
        <article class="card">
          <h3>${s.name}</h3>
          <div>${statusBadge(s.enabled ? "enabled" : "disabled")} ${statusBadge(s.auto_upload ? "auto_upload" : "manual_upload")}</div>
          <p class="meta">房间号：${s.room_id}</p>
          <p class="meta">清晰度：${s.quality || "best"}</p>
          <p class="meta">分段：${Number(s.segment_hours || 0) ? `每 ${s.segment_hours} 小时` : "不分段"}</p>
          <p class="meta">标签：${s.tags}</p>
          <p class="meta">标题：${s.title_template}</p>
          <p class="meta" data-status-for="${s.id}"></p>
          <div class="actions">
            <button class="secondary" data-check-button="${s.id}" onclick="checkStatus(${s.id})">检查开播</button>
            <button class="secondary" onclick="toggleStreamer(${s.id}, ${s.enabled ? "false" : "true"})">${s.enabled ? "暂停" : "启用"}</button>
            <button class="danger" onclick="deleteStreamer(${s.id})">删除</button>
          </div>
        </article>
      `,
      )
      .join("") || `<p class="meta">还没有主播。</p>`;
}

async function loadRecordings() {
  const recordings = await api("/api/recordings");
  $("#recordings").innerHTML =
    recordings
      .map(
        (r) => `
        <article class="recording">
          <strong>${r.streamer_name}</strong>
          ${statusBadge(r.status)}
          ${r.status === "recording" ? statusBadge("waiting") : statusBadge(r.upload_status)}
          <p class="meta">${r.live_title || "未记录标题"}</p>
          <p class="meta">${r.file_path || "未生成文件"}</p>
          ${segmentText(r)}
          ${fileLine(r)}
          ${networkLine(r)}
          ${r.log_path ? `<p class="meta">日志：${r.log_path}</p>` : ""}
          <p class="meta">开始：${r.started_at} ${r.ended_at ? `结束：${r.ended_at}` : ""}</p>
          ${remuxText(r)}
          ${r.status_check_error ? `<p class="meta status-check-error">状态检查异常：${r.status_check_error}</p>` : ""}
          ${r.error ? `<p class="meta">录制错误：${r.error}</p>` : ""}
          ${r.remux_error ? `<p class="meta">MP4 封装输出：${r.remux_error}</p>` : ""}
          ${r.upload_error ? `<p class="meta">发布输出：${r.upload_error}</p>` : ""}
          <div class="actions">
            ${r.status === "recording" ? `<button class="danger" onclick="stopRecording(${r.id})">中断并暂停主播</button>` : ""}
            ${r.status !== "recording" && r.remux_status !== "remuxed" ? `<button class="secondary" onclick="remuxRecording(${r.id})">生成 MP4 预览</button>` : ""}
            ${previewButtons(r)}
            ${canManualUpload(r) ? `<button class="secondary" onclick="queueUpload(${r.id})">手动投稿</button>` : ""}
            ${r.status !== "recording" ? `<button class="danger" onclick="deleteRecording(${r.id})">删除记录和文件</button>` : ""}
          </div>
        </article>
      `,
      )
      .join("") || `<p class="meta">还没有录制记录。</p>`;
}

async function refresh() {
  showDashboard();
  try {
    await Promise.all([loadStreamers(), loadRecordings()]);
  } catch (error) {
    console.error(error);
  }
}

function startRefreshLoop() {
  if (!refreshTimer) {
    refreshTimer = setInterval(refresh, 15000);
  }
  if (!networkTimer) {
    networkTimer = setInterval(updateLiveMetrics, 5000);
  }
}

async function updateLiveMetrics() {
  await Promise.all([updateNetworkRate(), updateRecordingFileMetrics()]);
}

async function updateNetworkRate() {
  try {
    const sample = await api("/api/metrics/network");
    if (!sample.supported) {
      latestNetworkRate = null;
      networkInterfaceName = sample.interface ? `网卡 ${sample.interface} 不可用` : "不支持当前系统";
      updateTrafficNodes();
      return;
    }
    networkInterfaceName = sample.interface ? `网卡 ${sample.interface}` : "所有非 lo 网卡";
    if (previousNetworkSample) {
      const byteDelta = sample.rx_bytes - previousNetworkSample.rx_bytes;
      const timeDelta = sample.timestamp - previousNetworkSample.timestamp;
      latestNetworkRate = timeDelta > 0 ? Math.max(0, byteDelta / timeDelta) : null;
      updateTrafficNodes();
    }
    previousNetworkSample = sample;
  } catch (error) {
    console.error(error);
  }
}

async function updateRecordingFileMetrics() {
  const nodes = [...document.querySelectorAll("[data-file-metric]")];
  await Promise.all(
    nodes.map(async (node) => {
      const id = node.getAttribute("data-file-metric");
      if (!id) {
        return;
      }
      try {
        const metric = await api(`/api/recordings/${id}/file`);
        const now = Date.now() / 1000;
        const previous = fileSamples.get(id);
        let rate = null;
        if (previous && metric.exists) {
          const byteDelta = metric.size_bytes - previous.size_bytes;
          const timeDelta = now - previous.timestamp;
          rate = timeDelta > 0 ? Math.max(0, byteDelta / timeDelta) : null;
        }
        const sample = { ...metric, timestamp: now, rate };
        fileSamples.set(id, sample);
        renderFileMetric(id, sample);
      } catch (error) {
        node.textContent = `录制文件：读取失败`;
        console.error(error);
      }
    }),
  );
}

async function toggleStreamer(id, enabled) {
  await api(`/api/streamers/${id}`, { method: "PATCH", body: JSON.stringify({ enabled }) });
  refresh();
}

async function deleteStreamer(id) {
  if (!confirm("确定删除这个主播？")) return;
  await api(`/api/streamers/${id}`, { method: "DELETE" });
  refresh();
}

async function checkStatus(id) {
  const button = document.querySelector(`[data-check-button="${id}"]`);
  if (button) {
    button.disabled = true;
  }
  setStatusMessage(id, "正在检查开播状态...");
  try {
    const status = await api(`/api/streamers/${id}/status`);
    setStatusMessage(id, status.is_live ? `正在直播：${status.title || "未获取到标题"}` : "当前未开播。");
  } catch (error) {
    setStatusMessage(id, `检查失败：${error.message}`);
    console.error(error);
  } finally {
    if (button) {
      button.disabled = false;
    }
  }
}

async function queueUpload(id) {
  await api(`/api/recordings/${id}/upload`, { method: "POST" });
  refresh();
}

async function remuxRecording(id) {
  await api(`/api/recordings/${id}/remux`, { method: "POST" });
  refresh();
}

async function stopRecording(id) {
  if (!confirm("确定中断当前录制并暂停这个主播？暂停后不会自动重新开录。")) return;
  await api(`/api/recordings/${id}/stop?disable_streamer=true`, { method: "POST" });
  refresh();
}

function openPreview(id, segmentIndex) {
  const video = $("#preview-video");
  const dialog = $("#preview-dialog");
  $("#preview-title").textContent = `视频预览 P${segmentIndex}`;
  video.src = `/api/recordings/${id}/media/${segmentIndex}`;
  dialog.showModal();
  video.play().catch(() => {});
}

function closePreview() {
  const video = $("#preview-video");
  video.pause();
  video.removeAttribute("src");
  video.load();
  $("#preview-dialog").close();
}

async function deleteRecording(id) {
  if (!confirm("确定删除这条录制记录和本地视频文件？这个操作不可恢复。")) return;
  await api(`/api/recordings/${id}?delete_file=true`, { method: "DELETE" });
  refresh();
}

$("#streamer-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const formElement = event.currentTarget;
  const form = new FormData(formElement);
  const payload = Object.fromEntries(form.entries());
  payload.auto_upload = form.get("auto_upload") === "on";
  payload.enabled = true;
  payload.tid = Number(payload.tid || 171);
  payload.quality = payload.quality || "best";
  payload.segment_hours = Number(payload.segment_hours || 0);
  $("#streamer-message").textContent = "正在添加主播...";
  $("#add-streamer-button").disabled = true;
  try {
    await api("/api/streamers", { method: "POST", body: JSON.stringify(payload) });
    formElement.reset();
    $("#streamer-message").textContent = "添加成功，已加入自动检测列表。";
    await refresh();
  } catch (error) {
    $("#streamer-message").textContent = `添加失败：${error.message}`;
    console.error(error);
  } finally {
    $("#add-streamer-button").disabled = false;
  }
});

$("#refresh").addEventListener("click", refresh);

async function boot() {
  await refresh();
  await updateLiveMetrics();
  startRefreshLoop();
}

boot();
