const $ = (selector) => document.querySelector(selector);
let refreshTimer = null;
let networkTimer = null;
let latestNetworkRxRate = null;
let latestNetworkTxRate = null;
let previousNetworkSample = null;
let networkInterfaceName = "";
const fileSamples = new Map();
const uploadCategories = [
  { tid: 171, name: "电子竞技" },
  { tid: 172, name: "手机游戏" },
  { tid: 17, name: "单机游戏" },
  { tid: 65, name: "网络游戏" },
  { tid: 21, name: "日常" },
  { tid: 138, name: "搞笑" },
  { tid: 161, name: "手工" },
  { tid: 162, name: "绘画" },
  { tid: 122, name: "野生技能协会" },
  { tid: 201, name: "科学科普" },
  { tid: 124, name: "社科·法律·心理" },
  { tid: 228, name: "人文历史" },
  { tid: 207, name: "财经商业" },
  { tid: 208, name: "校园学习" },
  { tid: 209, name: "职业职场" },
  { tid: 28, name: "原创音乐" },
  { tid: 31, name: "翻唱" },
  { tid: 59, name: "演奏" },
  { tid: 20, name: "宅舞" },
  { tid: 154, name: "舞蹈综合" },
  { tid: 182, name: "影视杂谈" },
  { tid: 183, name: "影视剪辑" },
  { tid: 85, name: "短片" },
  { tid: 95, name: "数码" },
  { tid: 230, name: "软件应用" },
  { tid: 231, name: "计算机技术" },
];

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

function badge(value, cls = "") {
  return `<span class="badge ${cls}">${value}</span>`;
}

function statusClass(value) {
  const good = ["finished", "uploaded", "remuxed", "enabled", "auto_upload"];
  const bad = ["failed", "recording_failed"];
  const working = ["recording", "waiting", "pending", "uploading", "remuxing"];
  return good.includes(value) ? "ok" : bad.includes(value) ? "bad" : working.includes(value) ? "working" : "";
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
  return badge(statusText(value), statusClass(value));
}

function formatRate(bytesPerSecond) {
  if (bytesPerSecond === null || Number.isNaN(bytesPerSecond)) {
    return "计算中";
  }
  const mibps = bytesPerSecond / 1024 / 1024;
  const mbps = (bytesPerSecond * 8) / 1000 / 1000;
  return `${mibps.toFixed(2)} MiB/s（${mbps.toFixed(2)} Mbps）`;
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
    return `<p class="meta">MP4：${statusBadge("remuxed")} 可网页预览</p>`;
  }
  if (recording.remux_status === "remuxing") {
    return `<p class="meta">MP4：${statusBadge("remuxing")}</p>`;
  }
  if (recording.remux_status === "failed") {
    return `<p class="meta">MP4：${statusBadge("failed")}</p>`;
  }
  return `<p class="meta">MP4：${statusBadge("not_started")}</p>`;
}

function remuxQualitySelect(recording) {
  const id = `remux-quality-${recording.id}`;
  const current = recording.mp4_profile || "default";
  const options = [
    ["default", "默认配置"],
    ["copy", "原样封装（最快）"],
    ["small", "小体积"],
    ["balanced", "均衡"],
    ["high", "高质量"],
  ];
  return `
    <select class="compact-select" id="${id}" aria-label="MP4 品质">
      ${options.map(([value, label]) => `<option value="${value}" ${current === value ? "selected" : ""}>${label}</option>`).join("")}
    </select>
  `;
}

function selectedRemuxQuality(id) {
  return document.querySelector(`#remux-quality-${id}`)?.value || "default";
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

function canRemux(recording) {
  return recording.status !== "recording" && !["uploading"].includes(recording.upload_status) && recording.remux_status !== "remuxing";
}

function networkLine(recording) {
  if (!showsLiveMetrics(recording)) {
    return "";
  }
  if (latestNetworkRxRate === null || latestNetworkTxRate === null) {
    return `<p class="meta live-traffic" data-live-traffic>网卡流量：计算中</p>`;
  }
  return `<p class="meta live-traffic" data-live-traffic>${networkTrafficText()}</p>`;
}

function fileLine(recording) {
  if (!recording.file_path && !recording.segment_paths && !recording.mp4_paths) {
    return "";
  }
  const mode = recording.upload_status === "uploading" ? "uploading" : recording.status === "recording" ? "recording" : "static";
  const label = mode === "uploading" ? "上传文件" : mode === "recording" ? "录制文件" : "本地文件";
  return `<p class="meta live-file" data-file-metric="${recording.id}" data-file-mode="${mode}">${label}：计算中</p>`;
}

function showsLiveMetrics(recording) {
  return recording.status === "recording" || recording.upload_status === "uploading";
}

function renderFileMetric(id, sample) {
  const node = document.querySelector(`[data-file-metric="${id}"]`);
  if (!node) {
    return;
  }
  if (!sample || !sample.exists) {
    const label = node.dataset.fileMode === "uploading" ? "上传文件" : "录制文件";
    node.textContent = `${label}：尚未生成`;
    return;
  }
  if (node.dataset.fileMode === "uploading") {
    const countText = sample.file_count > 1 ? `，${sample.file_count} 个文件` : "";
    node.textContent = `上传文件：${formatBytes(sample.size_bytes)}${countText}`;
    return;
  }
  if (node.dataset.fileMode === "static") {
    const countText = sample.file_count > 1 ? `，${sample.file_count} 个文件` : "";
    node.textContent = `本地文件：${formatBytes(sample.size_bytes)}${countText}`;
    return;
  }
  const rateText = sample.rate === null ? "写入速度：计算中" : `写入速度：${formatRate(sample.rate)}`;
  node.textContent = `录制文件：${formatBytes(sample.size_bytes)}，${rateText}`;
}

function updateTrafficNodes() {
  document.querySelectorAll("[data-live-traffic]").forEach((node) => {
    node.innerHTML = networkTrafficText();
  });
}

function networkTrafficText() {
  return `网卡下行：${formatRate(latestNetworkRxRate)}，上行：${formatRate(latestNetworkTxRate)} <span>${networkInterfaceName || "默认网卡"}</span>`;
}

function setStatusMessage(id, message) {
  const node = document.querySelector(`[data-status-for="${id}"]`);
  if (node) {
    node.textContent = message;
  }
}

function selected(value, current) {
  return String(value) === String(current) ? "selected" : "";
}

function checked(value) {
  return Number(value) ? "checked" : "";
}

function categoryLabel(tid) {
  const category = uploadCategories.find((item) => Number(item.tid) === Number(tid));
  return category ? `${category.name} (${category.tid})` : `自定义分区 (${tid || 171})`;
}

function categorySelect(tid, inputId) {
  const currentTid = Number(tid || 171);
  const hasPreset = uploadCategories.some((item) => item.tid === currentTid);
  const options = uploadCategories
    .map((item) => `<option value="${item.tid}" ${selected(item.tid, currentTid)}>${item.name} (${item.tid})</option>`)
    .join("");
  return `
    <select data-category-select data-tid-input="${inputId}" onchange="syncTidPreset(this)">
      ${options}
      <option value="custom" ${hasPreset ? "" : "selected"}>自定义分区</option>
    </select>
  `;
}

function setupCategorySelects(root = document) {
  root.querySelectorAll("[data-category-select]").forEach((select) => {
    if (select.options.length) {
      return;
    }
    const input = document.getElementById(select.dataset.tidInput);
    const currentTid = Number(input?.value || select.dataset.currentTid || 171);
    const hasPreset = uploadCategories.some((item) => item.tid === currentTid);
    select.innerHTML = `${uploadCategories
      .map((item) => `<option value="${item.tid}" ${selected(item.tid, currentTid)}>${item.name} (${item.tid})</option>`)
      .join("")}<option value="custom" ${hasPreset ? "" : "selected"}>自定义分区</option>`;
  });
}

function syncTidPreset(select) {
  const input = document.getElementById(select.dataset.tidInput);
  if (!input) {
    return;
  }
  input.readOnly = select.value !== "custom";
  if (select.value !== "custom") {
    input.value = select.value;
  }
}

function syncTidInput(input) {
  const select = document.querySelector(`[data-category-select][data-tid-input="${input.id}"]`);
  if (!select) {
    return;
  }
  select.value = uploadCategories.some((item) => item.tid === Number(input.value)) ? String(input.value) : "custom";
  syncTidPreset(select);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function streamerToggleButton(streamer) {
  if (streamer.enabled) {
    return `<button class="secondary" onclick="toggleStreamer(${streamer.id}, false)">暂停</button>`;
  }
  return `<button class="secondary" onclick="enableAndCheckStreamer(${streamer.id})">启用并检查开播</button>`;
}

function streamerEditForm(streamer) {
  const tidInputId = `streamer-tid-${streamer.id}`;
  const tidIsPreset = uploadCategories.some((item) => item.tid === Number(streamer.tid || 171));
  return `
    <details class="advanced streamer-edit">
      <summary>修改配置</summary>
      <form class="streamer-edit-form" onsubmit="updateStreamerConfig(event, ${streamer.id})">
        <div class="advanced-grid">
          <label class="field">
            <span>主播名称</span>
            <input name="name" value="${escapeHtml(streamer.name)}" required />
          </label>
          <label class="field">
            <span>直播间号</span>
            <input name="room_id" value="${escapeHtml(streamer.room_id)}" required />
          </label>
          <label class="field">
            <span>录制清晰度</span>
            <select name="quality">
              <option value="best" ${selected("best", streamer.quality || "best")}>最高可用 best</option>
              <option value="1080p" ${selected("1080p", streamer.quality)}>1080p</option>
              <option value="720p" ${selected("720p", streamer.quality)}>720p</option>
              <option value="480p" ${selected("480p", streamer.quality)}>480p</option>
              <option value="worst" ${selected("worst", streamer.quality)}>最低可用 worst</option>
            </select>
          </label>
          <label class="field">
            <span>录制分段</span>
            <select name="segment_hours">
              <option value="0" ${selected(0, streamer.segment_hours || 0)}>不分段</option>
              <option value="1" ${selected(1, streamer.segment_hours)}>每 1 小时一段</option>
              <option value="2" ${selected(2, streamer.segment_hours)}>每 2 小时一段</option>
              <option value="3" ${selected(3, streamer.segment_hours)}>每 3 小时一段</option>
            </select>
          </label>
          <label class="field">
            <span>投稿分区</span>
            ${categorySelect(streamer.tid, tidInputId)}
          </label>
          <label class="field">
            <span>自定义 tid</span>
            <input id="${tidInputId}" name="tid" type="number" min="1" value="${escapeHtml(streamer.tid || 171)}" onchange="syncTidInput(this)" ${tidIsPreset ? "readonly" : ""} />
          </label>
          <label class="field">
            <span>投稿标签</span>
            <input name="tags" value="${escapeHtml(streamer.tags)}" />
          </label>
          <label class="field full">
            <span>标题模板</span>
            <input name="title_template" value="${escapeHtml(streamer.title_template)}" />
          </label>
          <label class="field full">
            <span>简介模板</span>
            <textarea name="description_template">${escapeHtml(streamer.description_template)}</textarea>
          </label>
        </div>
        <label class="check-row">
          <input name="auto_upload" type="checkbox" ${checked(streamer.auto_upload)} />
          <span>
            <strong>下播后自动投稿</strong>
            <small>关闭后只保留本地文件，可以之后手动投稿。</small>
          </span>
        </label>
        <div class="form-actions">
          <button type="submit">保存配置</button>
          <p class="meta" data-edit-message="${streamer.id}"></p>
        </div>
      </form>
    </details>
  `;
}

function uploadOutputLine(recording) {
  if (recording.upload_status === "uploaded") {
    return `<p class="meta upload-summary">投稿结果：已发布，详细输出见投稿日志</p>`;
  }
  if (!recording.upload_error) {
    return "";
  }
  const summary = recording.upload_status === "failed" ? "发布失败，展开查看输出" : "发布提示，展开查看输出";
  return `
    <details class="log-details">
      <summary>${summary}</summary>
      <pre>${recording.upload_error}</pre>
    </details>
  `;
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
          <p class="meta">投稿分区：${categoryLabel(s.tid)}</p>
          <p class="meta">分段：${Number(s.segment_hours || 0) ? `每 ${s.segment_hours} 小时` : "不分段"}</p>
          <p class="meta">标签：${s.tags}</p>
          <p class="meta">标题：${s.title_template}</p>
          <p class="meta" data-status-for="${s.id}"></p>
          <div class="actions">
            <button class="secondary" data-check-button="${s.id}" onclick="checkStatus(${s.id})">检查开播</button>
            ${streamerToggleButton(s)}
            <button class="danger" onclick="deleteStreamer(${s.id})">删除</button>
          </div>
          ${streamerEditForm(s)}
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
          ${r.log_path ? `<p class="meta">录制日志：${r.log_path}</p>` : ""}
          ${r.upload_log_path ? `<p class="meta">投稿日志：${r.upload_log_path}</p>` : ""}
          <p class="meta">开始：${r.started_at} ${r.ended_at ? `结束：${r.ended_at}` : ""}</p>
          ${remuxText(r)}
          ${r.status_check_error ? `<p class="meta status-check-error">状态检查异常：${r.status_check_error}</p>` : ""}
          ${r.error ? `<p class="meta">录制错误：${r.error}</p>` : ""}
          ${r.remux_error ? `<p class="meta">MP4 封装输出：${r.remux_error}</p>` : ""}
          ${uploadOutputLine(r)}
          <div class="actions">
            ${r.status === "recording" ? `<button class="danger" onclick="stopRecording(${r.id})">中断并暂停主播</button>` : ""}
            ${r.status !== "recording" ? remuxQualitySelect(r) : ""}
            ${canRemux(r) ? `<button class="secondary" onclick="remuxRecording(${r.id})">${r.remux_status === "remuxed" ? "重新生成 MP4" : "生成 MP4 预览"}</button>` : ""}
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
      latestNetworkRxRate = null;
      latestNetworkTxRate = null;
      networkInterfaceName = sample.interface ? `网卡 ${sample.interface} 不可用` : "不支持当前系统";
      updateTrafficNodes();
      return;
    }
    networkInterfaceName = sample.interface ? `网卡 ${sample.interface}` : "所有非 lo 网卡";
    if (previousNetworkSample) {
      const rxByteDelta = sample.rx_bytes - previousNetworkSample.rx_bytes;
      const txByteDelta = sample.tx_bytes - previousNetworkSample.tx_bytes;
      const timeDelta = sample.timestamp - previousNetworkSample.timestamp;
      latestNetworkRxRate = timeDelta > 0 ? Math.max(0, rxByteDelta / timeDelta) : null;
      latestNetworkTxRate = timeDelta > 0 ? Math.max(0, txByteDelta / timeDelta) : null;
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

async function updateStreamerConfig(event, id) {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  const payload = Object.fromEntries(data.entries());
  payload.auto_upload = data.get("auto_upload") === "on";
  payload.tid = Number(payload.tid || 171);
  payload.segment_hours = Number(payload.segment_hours || 0);
  payload.quality = payload.quality || "best";
  const message = document.querySelector(`[data-edit-message="${id}"]`);
  if (message) {
    message.textContent = "正在保存...";
  }
  try {
    await api(`/api/streamers/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
    if (message) {
      message.textContent = "已保存";
    }
    await refresh();
  } catch (error) {
    if (message) {
      message.textContent = `保存失败：${error.message}`;
    }
    console.error(error);
  }
}

async function enableAndCheckStreamer(id) {
  setStatusMessage(id, "正在启用并检查开播...");
  await api(`/api/streamers/${id}/enable-and-check`, { method: "POST" });
  await refresh();
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
  await api(`/api/recordings/${id}/upload`, {
    method: "POST",
    body: JSON.stringify({ quality: selectedRemuxQuality(id) }),
  });
  refresh();
}

async function remuxRecording(id) {
  await api(`/api/recordings/${id}/remux`, {
    method: "POST",
    body: JSON.stringify({ quality: selectedRemuxQuality(id) }),
  });
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
setupCategorySelects();
document.querySelectorAll("[data-category-select]").forEach(syncTidPreset);

async function boot() {
  await refresh();
  await updateLiveMetrics();
  startRefreshLoop();
}

boot();
