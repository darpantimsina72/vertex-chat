// ---------- State ----------
const LS = {
  key: "vchat_api_key",
  base: "vchat_base_url",
  model: "vchat_model",
  system: "vchat_system",
  history: "vchat_history",   // legacy single-chat store (migrated into chats)
  chats: "vchat_chats",
  current: "vchat_current_chat",
  memory: "vchat_memory",
  memoryOn: "vchat_memory_on",
};
let messages = [];          // OpenAI-format history of the open chat
let chats = [];             // [{id, title, updated, messages}] — all saved chats, newest first
let currentId = null;       // id of the open chat (null = new, unsaved chat)
let memory = [];            // [{id, text}] — facts about the user, sent with every chat
let pendingFiles = [];      // [{kind, name, size, dataUrl?, format?, text?}]
let sending = false;
let session = { prompt: 0, completion: 0, cost: 0, costKnown: false };  // running totals
let turnUsage = null;       // usage object for the in-flight turn
let turnCost = null;        // cost string for the in-flight turn

// ---------- Elements ----------
const $ = (id) => document.getElementById(id);
const messagesEl = $("messages");
const inputEl = $("input");
const usageEl = $("usage");
const modelSelect = $("modelSelect");

// ---------- Helpers ----------
function getKey() { return localStorage.getItem(LS.key) || ""; }
function getBase() { return localStorage.getItem(LS.base) || ""; }

function reqHeaders() {
  const h = { "Content-Type": "application/json" };
  const k = getKey(); if (k) h["x-api-key"] = k;
  const b = getBase(); if (b) h["x-base-url"] = b;
  return h;
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Minimal markdown: fenced code blocks + inline code, rest escaped with line breaks preserved.
function renderMarkdown(text) {
  const parts = text.split(/```/);
  let html = "";
  parts.forEach((part, i) => {
    if (i % 2 === 1) {
      const body = part.replace(/^[a-zA-Z0-9]*\n/, "");
      html += `<pre><code>${escapeHtml(body)}</code></pre>`;
    } else {
      html += escapeHtml(part).replace(/`([^`]+)`/g, "<code>$1</code>");
    }
  });
  return html;
}

function clearEmptyState() {
  const es = messagesEl.querySelector(".empty-state");
  if (es) es.remove();
}

function addBubble(role, contentHtml, rawText) {
  clearEmptyState();
  const wrap = document.createElement("div");
  wrap.className = `msg ${role}`;
  wrap.innerHTML = `<div class="msg-col"><div class="msg-head"><span class="role">${role === "user" ? "You" : "Model"}</span><button class="copy-btn" title="Copy this message">Copy</button></div><div class="bubble">${contentHtml}</div></div>`;
  const bubble = wrap.querySelector(".bubble");
  if (rawText !== undefined) bubble._raw = rawText;
  wrap.querySelector(".copy-btn").addEventListener("click", (e) => copyBubble(e.currentTarget, bubble));
  messagesEl.appendChild(wrap);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return bubble;
}

async function copyBubble(btn, bubble) {
  const text = bubble._raw ?? bubble.innerText;
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    // Fallback for browsers without clipboard API permission.
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
  btn.textContent = "Copied!";
  btn.classList.add("copied");
  setTimeout(() => { btn.textContent = "Copy"; btn.classList.remove("copied"); }, 1500);
}

function fmtCost(v) {
  const n = parseFloat(v);
  if (isNaN(n)) return null;
  return n === 0 ? "$0" : "$" + n.toFixed(6);
}

function fmtSize(bytes) {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(0) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

// Render the in-flight turn's tokens/cost plus running session totals.
function renderUsage() {
  let line = "";
  if (turnUsage) {
    const cached = turnUsage.prompt_tokens_details?.cached_tokens ?? 0;
    line += `turn — in: ${turnUsage.prompt_tokens ?? "?"} (cached ${cached}), ` +
            `out: ${turnUsage.completion_tokens ?? "?"}, total: ${turnUsage.total_tokens ?? "?"}`;
    const c = turnCost != null ? fmtCost(turnCost) : null;
    line += `  •  cost: ${c ?? "n/a"}`;
  }
  if (session.prompt || session.completion) {
    line += `      |      session — in: ${session.prompt}, out: ${session.completion}` +
            `, cost: ${session.costKnown ? "$" + session.cost.toFixed(6) : "n/a"}`;
  }
  if (line) usageEl.textContent = line;
}

// Add the finished turn's numbers to the session totals (once per turn).
function commitTurn() {
  if (turnUsage) {
    session.prompt += turnUsage.prompt_tokens || 0;
    session.completion += turnUsage.completion_tokens || 0;
  }
  const n = parseFloat(turnCost);
  if (!isNaN(n)) { session.cost += n; session.costKnown = true; }
  renderUsage();
}

// ---------- Chats (stored in this browser) ----------
const newId = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 6);

function chatTitle(msgs) {
  const first = msgs.find((m) => m.role === "user");
  if (!first) return "New chat";
  let text = typeof first.content === "string" ? first.content
    : (first.content.find((p) => p.type === "text")?.text || "Attachment");
  text = text.split("[Attached file:")[0].trim() || "Attachment";
  return text.length > 40 ? text.slice(0, 40) + "…" : text;
}

// Replace big base64 attachments with a short note — used only when browser storage is full.
function stripMedia(msgs) {
  return msgs.map((m) => {
    if (typeof m.content === "string") return m;
    return { ...m, content: m.content.map((p) => p.type === "text" ? p
      : { type: "text", text: `[${p.type === "image_url" ? "image" : p.type === "input_audio" ? "audio" : p.file?.filename || "file"} removed to save browser space]` }) };
  });
}

function persistChats() {
  try {
    localStorage.setItem(LS.chats, JSON.stringify(chats));
  } catch (e) {
    // Storage full (~5 MB): drop attachment data from older chats, keep all text.
    try {
      chats = chats.map((c) => c.id === currentId ? c : { ...c, messages: stripMedia(c.messages) });
      localStorage.setItem(LS.chats, JSON.stringify(chats));
    } catch (e2) {
      usageEl.textContent = "Browser storage full — delete some old chats so new messages are saved.";
    }
  }
  try { localStorage.setItem(LS.current, currentId || ""); } catch (e) {}
}

function saveHistory() {
  if (!messages.length) {                             // nothing to keep (e.g. first message failed)
    if (currentId) { chats = chats.filter((c) => c.id !== currentId); currentId = null; persistChats(); renderChatList(); }
    return;
  }
  if (!currentId) {
    currentId = newId();
    chats.unshift({ id: currentId, title: "New chat", updated: Date.now(), messages: [] });
  }
  const chat = chats.find((c) => c.id === currentId);
  chat.messages = messages;
  chat.title = chatTitle(messages);
  chat.updated = Date.now();
  chats.sort((a, b) => b.updated - a.updated);
  persistChats();
  renderChatList();
}

function renderChatList() {
  const list = $("chatList");
  list.innerHTML = "";
  if (!chats.length) { list.innerHTML = `<div class="memory-empty">No saved chats yet.</div>`; return; }
  chats.forEach((c) => {
    const item = document.createElement("div");
    item.className = "chat-item" + (c.id === currentId ? " active" : "");
    item.title = c.title;
    item.innerHTML = `<span class="chat-title">${escapeHtml(c.title)}</span><button class="del" title="Delete chat">×</button>`;
    item.addEventListener("click", () => openChat(c.id));
    item.querySelector(".del").addEventListener("click", (e) => { e.stopPropagation(); deleteChat(c.id); });
    list.appendChild(item);
  });
}

const EMPTY_STATE = `<div class="empty-state"><h1>Vertex Chat</h1><p>New conversation. Attach images, audio, video or files. Saved chats are on the left; memory is on the right.</p></div>`;

function resetView() {
  session = { prompt: 0, completion: 0, cost: 0, costKnown: false };
  messagesEl.innerHTML = EMPTY_STATE;
  usageEl.textContent = "";
}

function startNewChat() {
  if (sending) return;
  messages = []; currentId = null;
  persistChats();
  resetView();
  renderChatList();
  inputEl.focus();
}

function openChat(id) {
  if (sending || id === currentId) return;
  const chat = chats.find((c) => c.id === id);
  if (!chat) return;
  currentId = id;
  persistChats();
  resetView();
  renderMessages(chat.messages);
  renderChatList();
}

function deleteChat(id) {
  const chat = chats.find((c) => c.id === id);
  if (!chat || !confirm(`Delete "${chat.title}"? This cannot be undone.`)) return;
  chats = chats.filter((c) => c.id !== id);
  if (id === currentId) { messages = []; currentId = null; resetView(); }
  persistChats();
  renderChatList();
}

// ---------- Memory (facts about the user, stored in this browser) ----------
function saveMemory() {
  try { localStorage.setItem(LS.memory, JSON.stringify(memory)); } catch (e) {}
}

function addMemory(text) {
  text = text.trim();
  if (!text || memory.some((m) => m.text.toLowerCase() === text.toLowerCase())) return false;
  memory.push({ id: newId(), text });
  saveMemory();
  renderMemory();
  return true;
}

function renderMemory() {
  const list = $("memoryList");
  list.innerHTML = "";
  if (!memory.length) { list.innerHTML = `<div class="memory-empty">Nothing saved yet.</div>`; return; }
  memory.forEach((m) => {
    const item = document.createElement("div");
    item.className = "memory-item";
    item.innerHTML = `<span>${escapeHtml(m.text)}</span><button class="del" title="Forget this">×</button>`;
    item.querySelector(".del").addEventListener("click", () => {
      memory = memory.filter((x) => x.id !== m.id); saveMemory(); renderMemory();
    });
    list.appendChild(item);
  });
}

// System prompt sent to the model = your instructions + saved memory.
// Kept stable between turns so prompt caching still works.
function buildSystem() {
  let sys = $("systemPrompt").value.trim();
  if ($("memoryToggle").checked && memory.length) {
    sys += (sys ? "\n\n" : "") +
      "What you know about the user (saved memory — use it when relevant, don't repeat it back):\n" +
      memory.map((m) => "- " + m.text).join("\n");
  }
  return sys;
}

// "Remember that I prefer formal Hindi" → saves "I prefer formal Hindi".
function captureRemember(text) {
  const m = text.match(/^\s*(?:please\s+)?remember(?:\s+that)?[\s:,-]+([\s\S]+)/i);
  if (!m) return null;
  const fact = m[1].trim().replace(/[.!]+$/, "");
  return fact && addMemory(fact) ? fact : null;
}

function showMemoryNote(fact) {
  const note = document.createElement("div");
  note.className = "memory-note";
  note.textContent = `✓ Saved to memory: ${fact}`;
  messagesEl.appendChild(note);
}

// ---------- Models ----------
async function loadModels() {
  if (!getKey()) { modelSelect.innerHTML = `<option value="">set API key in ⚙</option>`; return; }
  modelSelect.innerHTML = `<option value="">loading…</option>`;
  try {
    const r = await fetch("/api/models", { headers: reqHeaders() });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    const { models } = await r.json();
    const saved = localStorage.getItem(LS.model);
    modelSelect.innerHTML = "";
    if (!models.length) modelSelect.innerHTML = `<option value="">no models returned</option>`;
    models.forEach((m) => {
      const o = document.createElement("option");
      o.value = m; o.textContent = m;
      if (m === saved) o.selected = true;
      modelSelect.appendChild(o);
    });
  } catch (e) {
    modelSelect.innerHTML = `<option value="">error: ${escapeHtml(String(e.message || e))}</option>`;
  }
}

// ---------- Attachments ----------
const MAX_IMG_DIM = 1024;                 // cap longest side → fewer input tokens, smaller payload, less 504 risk
const MAX_MEDIA_BYTES = 25 * 1024 * 1024; // audio/video/docs cap — base64 inflates 33%, big bodies 504 at the gateway
const TEXT_EXTS = ["txt", "md", "csv", "tsv", "json", "srt", "vtt", "xml", "html", "yaml", "yml", "py", "js", "log"];

function ext(name) { return (name.split(".").pop() || "").toLowerCase(); }

function classifyFile(f) {
  if (f.type.startsWith("image/")) return "image";
  if (f.type.startsWith("audio/")) return "audio";
  if (f.type.startsWith("video/")) return "video";
  if (TEXT_EXTS.includes(ext(f.name)) || f.type.startsWith("text/")) return "text";
  return "file"; // pdf, docx, anything else → sent as file_data
}

const KIND_ICON = { image: "🖼", audio: "🎙", video: "🎬", text: "📄", file: "📎" };

function renderPreviews() {
  const box = $("attachPreviews");
  box.innerHTML = "";
  pendingFiles.forEach((att, idx) => {
    const t = document.createElement("div");
    if (att.kind === "image") {
      t.className = "thumb";
      t.innerHTML = `<img src="${att.dataUrl}" alt="${escapeHtml(att.name)}" /><button class="rm" data-i="${idx}">×</button>`;
    } else {
      t.className = "chip";
      t.innerHTML = `<span class="chip-icon">${KIND_ICON[att.kind]}</span>` +
                    `<span class="chip-name">${escapeHtml(att.name)}</span>` +
                    `<span class="chip-size">${fmtSize(att.size)}</span>` +
                    `<button class="rm" data-i="${idx}">×</button>`;
    }
    box.appendChild(t);
  });
  box.querySelectorAll(".rm").forEach((b) =>
    b.addEventListener("click", () => { pendingFiles.splice(+b.dataset.i, 1); renderPreviews(); })
  );
}

function readAsDataURL(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

function readAsText(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsText(file);
  });
}

// Downscale + re-encode images client-side before they ever hit the API (cost control).
function downscaleImage(file) {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => {
      const img = new Image();
      img.onload = () => {
        let { width, height } = img;
        const scale = Math.min(1, MAX_IMG_DIM / Math.max(width, height));
        if (scale < 1) { width = Math.round(width * scale); height = Math.round(height * scale); }
        const canvas = document.createElement("canvas");
        canvas.width = width; canvas.height = height;
        canvas.getContext("2d").drawImage(img, 0, 0, width, height);
        resolve(canvas.toDataURL("image/jpeg", 0.85));
      };
      img.onerror = () => resolve(reader.result);  // fall back to original on decode failure
      img.src = reader.result;
    };
    reader.readAsDataURL(file);
  });
}

async function handleFiles(files) {
  for (const f of files) {
    const kind = classifyFile(f);
    if (kind !== "image" && kind !== "text" && f.size > MAX_MEDIA_BYTES) {
      alert(`"${f.name}" is ${fmtSize(f.size)} — too big to send inline (limit ${fmtSize(MAX_MEDIA_BYTES)}). Trim/compress it first.`);
      continue;
    }
    try {
      if (kind === "image") {
        const dataUrl = await downscaleImage(f);
        pendingFiles.push({ kind, name: f.name, size: f.size, dataUrl });
      } else if (kind === "text") {
        const text = await readAsText(f);
        pendingFiles.push({ kind, name: f.name, size: f.size, text });
      } else {
        const dataUrl = await readAsDataURL(f);
        const att = { kind, name: f.name, size: f.size, dataUrl };
        if (kind === "audio") att.format = ext(f.name) || (f.type.split("/")[1] || "mp3");
        pendingFiles.push(att);
      }
      renderPreviews();
    } catch (e) {
      alert(`Could not read "${f.name}": ${e.message || e}`);
    }
  }
}

// ---------- Send ----------
// Build OpenAI-format multimodal content:
//   image → image_url (data URL)
//   audio → input_audio (base64 + format)  [LiteLLM maps this to the provider's audio input]
//   video / pdf / other binary → file with file_data (data URL)
//   text file → inlined into the text part (best compatibility, cache-friendly)
function buildUserContent(text) {
  let fullText = text || "";
  pendingFiles.filter((a) => a.kind === "text").forEach((a) => {
    fullText += `\n\n[Attached file: ${a.name}]\n\`\`\`\n${a.text}\n\`\`\``;
  });

  const media = pendingFiles.filter((a) => a.kind !== "text");
  if (!media.length) return fullText;

  const content = [];
  if (fullText) content.push({ type: "text", text: fullText });
  media.forEach((a) => {
    if (a.kind === "image") {
      content.push({ type: "image_url", image_url: { url: a.dataUrl } });
    } else if (a.kind === "audio") {
      const b64 = a.dataUrl.split(",")[1] || "";
      content.push({ type: "input_audio", input_audio: { data: b64, format: a.format } });
    } else {
      content.push({ type: "file", file: { file_data: a.dataUrl, filename: a.name } });
    }
  });
  return content;
}

function userBubbleHtml(text) {
  let html = renderMarkdown(text || "");
  pendingFiles.forEach((att) => {
    if (att.kind === "image") {
      html += `<img src="${att.dataUrl}" alt="${escapeHtml(att.name)}" />`;
    } else {
      html += `<div class="bubble-att">${KIND_ICON[att.kind]} ${escapeHtml(att.name)} <small>(${fmtSize(att.size)})</small></div>`;
    }
  });
  return html;
}

async function send() {
  if (sending) return;
  const text = inputEl.value.trim();
  if (!text && !pendingFiles.length) return;
  const model = modelSelect.value;
  if (!model) { usageEl.textContent = "Pick a model first (top bar)."; return; }

  sending = true;
  $("sendBtn").disabled = true;

  addBubble("user", userBubbleHtml(text), text);
  const remembered = captureRemember(text);
  if (remembered) showMemoryNote(remembered);
  messages.push({ role: "user", content: buildUserContent(text) });
  inputEl.value = "";
  inputEl.style.height = "auto";
  pendingFiles = [];
  renderPreviews();
  saveHistory();

  const stream = $("streamToggle").checked;
  const payload = {
    model,
    messages,
    system: buildSystem(),
    temperature: parseFloat($("temperature").value),
    top_p: parseFloat($("topp").value),
    cache: $("cacheToggle").checked,
    stream,
  };
  const mt = $("maxTokens").value;
  if (mt) payload.max_tokens = parseInt(mt, 10);

  const bubble = addBubble("assistant", `<span class="blink"></span>`);
  let assistantText = "";
  turnUsage = null;
  turnCost = null;

  try {
    const res = await fetch("/api/chat", { method: "POST", headers: reqHeaders(), body: JSON.stringify(payload) });
    if (!res.ok) {
      let detail; try { detail = (await res.json()).detail; } catch { detail = await res.text(); }
      throw new Error(detail || res.statusText);
    }

    if (!stream) {
      const data = await res.json();
      assistantText = data.choices?.[0]?.message?.content || "";
      bubble.innerHTML = renderMarkdown(assistantText);
      bubble._raw = assistantText;
      turnUsage = data.usage || null;
      turnCost = data._litellm_cost ?? null;
      renderUsage();
    } else {
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf("\n\n")) >= 0) {
          const frame = buffer.slice(0, idx).trim();
          buffer = buffer.slice(idx + 2);
          if (!frame) continue;
          const dataLine = frame.split("\n").find((l) => l.startsWith("data:"));
          if (!dataLine) continue;
          const data = dataLine.slice(5).trim();
          if (data === "[DONE]") continue;
          let json; try { json = JSON.parse(data); } catch { continue; }
          if (json.error) throw new Error(json.error);
          const delta = json.choices?.[0]?.delta?.content;
          if (delta) { assistantText += delta; bubble.innerHTML = renderMarkdown(assistantText) + `<span class="blink"></span>`; messagesEl.scrollTop = messagesEl.scrollHeight; }
          if (json.usage) { turnUsage = json.usage; renderUsage(); }
          if (json.litellm_cost !== undefined) { turnCost = json.litellm_cost; renderUsage(); }
        }
      }
      bubble.innerHTML = renderMarkdown(assistantText);
      bubble._raw = assistantText;
    }

    messages.push({ role: "assistant", content: assistantText });
    saveHistory();
    commitTurn();
  } catch (e) {
    bubble.innerHTML = `<span style="color:#ef4444">Error: ${escapeHtml(String(e.message || e))}</span>`;
    // Roll back the user turn so retry doesn't duplicate it.
    messages.pop();
    saveHistory();
  } finally {
    sending = false;
    $("sendBtn").disabled = false;
  }
}

// ---------- History restore ----------
function renderMessages(saved) {
  try {
    messages = saved;
    if (!saved.length) return;
    saved.forEach((m) => {
      if (m.role === "user") {
        let text = "", extra = "";
        if (typeof m.content === "string") text = m.content;
        else m.content.forEach((p) => {
          if (p.type === "text") text += p.text;
          if (p.type === "image_url") extra += `<img src="${p.image_url.url}" />`;
          if (p.type === "input_audio") extra += `<div class="bubble-att">🎙 audio attachment</div>`;
          if (p.type === "file") extra += `<div class="bubble-att">📎 ${escapeHtml(p.file?.filename || "file attachment")}</div>`;
        });
        addBubble("user", renderMarkdown(text) + extra, text);
      } else if (m.role === "assistant") {
        addBubble("assistant", renderMarkdown(m.content || ""), m.content || "");
      }
    });
  } catch (e) {}
}

function loadStore() {
  try { chats = JSON.parse(localStorage.getItem(LS.chats) || "[]"); } catch (e) { chats = []; }
  if (!Array.isArray(chats)) chats = [];
  try { memory = JSON.parse(localStorage.getItem(LS.memory) || "[]"); } catch (e) { memory = []; }
  if (!Array.isArray(memory)) memory = [];
  // One-time move of the old single conversation into the chat list.
  try {
    const legacy = JSON.parse(localStorage.getItem(LS.history) || "[]");
    if (Array.isArray(legacy) && legacy.length) {
      const id = newId();
      chats.unshift({ id, title: chatTitle(legacy), updated: Date.now(), messages: legacy });
      localStorage.setItem(LS.current, id);
      persistChats();
    }
    localStorage.removeItem(LS.history);
  } catch (e) {}
  chats.sort((a, b) => b.updated - a.updated);
  let cur = null;
  try { cur = localStorage.getItem(LS.current); } catch (e) {}
  const chat = chats.find((c) => c.id === cur);
  currentId = chat ? chat.id : null;
  if (chat) renderMessages(chat.messages);
  try { $("memoryToggle").checked = localStorage.getItem(LS.memoryOn) !== "0"; } catch (e) {}
  renderChatList();
  renderMemory();
}

// ---------- Wiring ----------
function autoGrow() { inputEl.style.height = "auto"; inputEl.style.height = Math.min(inputEl.scrollHeight, 200) + "px"; }

inputEl.addEventListener("input", autoGrow);
inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
});
$("sendBtn").addEventListener("click", send);
$("attachBtn").addEventListener("click", () => $("fileInput").click());
$("fileInput").addEventListener("change", (e) => { handleFiles(e.target.files); e.target.value = ""; });

// Paste images directly
inputEl.addEventListener("paste", (e) => {
  const items = [...(e.clipboardData?.items || [])].filter((i) => i.type.startsWith("image/"));
  if (items.length) { handleFiles(items.map((i) => i.getAsFile())); e.preventDefault(); }
});

// Drag & drop any file onto the page
document.addEventListener("dragover", (e) => e.preventDefault());
document.addEventListener("drop", (e) => {
  e.preventDefault();
  if (e.dataTransfer?.files?.length) handleFiles(e.dataTransfer.files);
});

$("reloadModels").addEventListener("click", loadModels);
modelSelect.addEventListener("change", () => localStorage.setItem(LS.model, modelSelect.value));

$("temperature").addEventListener("input", (e) => ($("tempVal").textContent = e.target.value));
$("topp").addEventListener("input", (e) => ($("toppVal").textContent = e.target.value));
$("systemPrompt").addEventListener("input", (e) => localStorage.setItem(LS.system, e.target.value));

$("newChat").addEventListener("click", startNewChat);
$("newChatSide").addEventListener("click", startNewChat);

$("memoryToggle").addEventListener("change", (e) => {
  try { localStorage.setItem(LS.memoryOn, e.target.checked ? "1" : "0"); } catch (err) {}
});
$("memoryAddBtn").addEventListener("click", () => {
  if (addMemory($("memoryInput").value)) $("memoryInput").value = "";
});
$("memoryInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); $("memoryAddBtn").click(); }
});

// Settings modal
$("openSettings").addEventListener("click", () => {
  $("apiKey").value = getKey();
  $("baseUrl").value = getBase();
  $("settingsModal").classList.remove("hidden");
});
$("closeSettings").addEventListener("click", () => $("settingsModal").classList.add("hidden"));
$("saveSettings").addEventListener("click", () => {
  localStorage.setItem(LS.key, $("apiKey").value.trim());
  localStorage.setItem(LS.base, $("baseUrl").value.trim());
  $("settingsModal").classList.add("hidden");
  loadModels();
});

// Feedback modal
$("openFeedback").addEventListener("click", () => {
  $("fbStatus").querySelector("small").textContent = "";
  $("feedbackModal").classList.remove("hidden");
});
$("closeFeedback").addEventListener("click", () => $("feedbackModal").classList.add("hidden"));
$("sendFeedback").addEventListener("click", async () => {
  const msg = $("fbMsg").value.trim();
  const status = $("fbStatus").querySelector("small");
  if (!msg) { status.textContent = "Write a message first."; return; }
  const files = [...($("fbShots").files || [])].slice(0, 5);
  const attachments = await Promise.all(files.map(f => new Promise((res) => {
    const r = new FileReader();
    r.onload = () => res({ name: f.name, data: r.result.split(",")[1] });
    r.onerror = () => res(null);
    r.readAsDataURL(f);
  })));
  $("sendFeedback").disabled = true;
  status.textContent = "Sending…";
  try {
    const r = await fetch("/api/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        kind: $("fbKind").value, name: $("fbName").value.trim(),
        message: msg, attachments: attachments.filter(Boolean),
      }),
    });
    const data = await r.json();
    if (r.ok && data.ok) {
      $("fbMsg").value = ""; $("fbShots").value = "";
      status.textContent = "✓ Sent — thank you!";
      setTimeout(() => $("feedbackModal").classList.add("hidden"), 1200);
    } else {
      status.textContent = "✗ " + (data.error || data.detail || "Could not send.")
        + (data.saved ? " Saved to " + data.saved + " — send that folder to the developer." : "");
    }
  } catch (e) {
    status.textContent = "✗ " + e;
  } finally {
    $("sendFeedback").disabled = false;
  }
});

// ---------- Init ----------
(function init() {
  const sys = localStorage.getItem(LS.system);
  if (sys) $("systemPrompt").value = sys;
  loadStore();
  loadModels();
})();
