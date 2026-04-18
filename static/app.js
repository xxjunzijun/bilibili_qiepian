const $ = (selector) => document.querySelector(selector);
let refreshTimer = null;

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
          <div>${badge(s.enabled ? "enabled" : "disabled")} ${badge(s.auto_upload ? "auto_upload" : "manual_upload")}</div>
          <p class="meta">房间号：${s.room_id}</p>
          <p class="meta">清晰度：${s.quality || "best"}</p>
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
          ${badge(r.status)} ${badge(r.upload_status)}
          <p class="meta">${r.live_title || "未记录标题"}</p>
          <p class="meta">${r.file_path || "未生成文件"}</p>
          <p class="meta">开始：${r.started_at} ${r.ended_at ? `结束：${r.ended_at}` : ""}</p>
          ${r.upload_error ? `<p class="meta">发布输出：${r.upload_error}</p>` : ""}
          <div class="actions">
            ${r.status === "finished" ? `<button class="secondary" onclick="queueUpload(${r.id})">加入投稿队列</button>` : ""}
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
  startRefreshLoop();
}

boot();
