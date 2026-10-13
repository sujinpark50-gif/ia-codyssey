// ADAIR Mission Intelligence 웹 화면 (Vanilla JS).
// 비밀값 없음: Firebase 웹 설정(공개 식별자)은 /api/config 에서 받고, API 는 로그인 ID 토큰으로만 부른다.

const FIREBASE_VERSION = "10.12.2";
const PAGE = 50;

const $ = (sel) => document.querySelector(sel);
const state = {
  cfg: null, fb: null, user: null,
  runId: null, metric: null, unit: "m/s", summary: null,
  filter: "", offset: 0, total: 0, editing: null,
  convId: null, busy: false,
};

class ApiError extends Error {
  constructor(status, body) {
    super(typeof body?.detail === "string" ? body.detail : `요청 실패 (HTTP ${status})`);
    this.status = status;
    this.body = body || {};
  }
}

// ---------------------------------------------------------------- 공통
async function api(path, { method = "GET", body } = {}) {
  const token = await state.user.getIdToken();
  const res = await fetch(path, {
    method,
    headers: { Authorization: `Bearer ${token}`, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 204) return null;
  let data = null;
  try { data = await res.json(); } catch { /* 본문 없음 */ }
  if (!res.ok) {
    if (res.status === 401) toast("로그인이 만료되었습니다. 다시 로그인하세요");
    throw new ApiError(res.status, data);
  }
  return data;
}

function errorText(e) {
  if (!(e instanceof ApiError)) return "서버에 연결할 수 없습니다. 잠시 후 다시 시도하세요";
  if (e.status === 422) return "입력값을 확인하세요: " + (e.body.detail || []).map((d) => d.msg).join(", ");
  if (e.status === 429) return `${e.message} (${e.body.retry_after_s}초 뒤 다시 시도)`;
  if (e.status === 504) return "AI 응답 시간이 초과되었습니다. 다시 시도하세요";
  return e.message;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const num = (v, d = 3) => (v === null || v === undefined ? "–" : Number(v).toFixed(d));
const kst = (iso) => (iso ? iso.replace("T", " ").replace(/(\.\d{3})?\d*\+09:00$/, "") : "–");

function show(el, on = true) { el.hidden = !on; }
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  show(t);
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => show(t, false), 3500);
}

// 답변 표시: HTML 을 먼저 이스케이프한 뒤 굵게·코드·목록만 허용
function renderMarkdown(text) {
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/`([^`]+)`/g, "<code>$1</code>");
  const out = [];
  let list = null;
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    const m = line.match(/^\s*[-*•]\s+(.*)$/);
    if (m) { (list ||= []).push(`<li>${inline(m[1])}</li>`); continue; }
    if (list) { out.push(`<ul>${list.join("")}</ul>`); list = null; }
    if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  if (list) out.push(`<ul>${list.join("")}</ul>`);
  return out.join("");
}

// ---------------------------------------------------------------- 인증
async function boot() {
  try {
    state.cfg = await (await fetch("/api/config")).json();
  } catch {
    return loginError("서버에 연결할 수 없습니다");
  }
  state.metric = state.cfg.default_metric;
  $("#question").maxLength = state.cfg.max_message_chars;
  if (!state.cfg.firebase) {
    show($("#login-view"));
    $("#login-btn").disabled = true;
    return loginError("로그인 설정이 없습니다. 서버의 FIREBASE_WEB_API_KEY·FIREBASE_AUTH_DOMAIN·FIREBASE_WEB_APP_ID 를 확인하세요");
  }
  const base = `https://www.gstatic.com/firebasejs/${FIREBASE_VERSION}`;
  const [{ initializeApp }, fbAuth] = await Promise.all([import(`${base}/firebase-app.js`), import(`${base}/firebase-auth.js`)]);
  const app = initializeApp(state.cfg.firebase);
  const auth = fbAuth.getAuth(app);
  state.fb = { auth, ...fbAuth };
  $("#login-btn").onclick = async () => {
    show($("#login-error"), false);
    try {
      await fbAuth.signInWithPopup(auth, new fbAuth.GoogleAuthProvider());
    } catch (e) {
      loginError(e.code === "auth/popup-closed-by-user" ? "로그인 창이 닫혔습니다" : `로그인 실패: ${e.code || e.message}`);
    }
  };
  $("#logout").onclick = () => fbAuth.signOut(auth);
  fbAuth.onAuthStateChanged(auth, (user) => (user ? enter(user) : leave()));
}

function loginError(msg) {
  show($("#login-view"));
  const el = $("#login-error");
  el.textContent = msg;
  show(el);
}

function leave() {
  state.user = null;
  show($("#app-view"), false);
  show($("#session"), false);
  show($("#login-view"));
}

async function enter(user) {
  state.user = user;
  try {
    const me = await api("/api/me");
    $("#user-email").textContent = me.email;
    renderLimits(me.chat_limits);
  } catch (e) {
    await state.fb.signOut(state.fb.auth);
    return loginError(e instanceof ApiError && e.status === 403 ? e.message : errorText(e));
  }
  show($("#login-view"), false);
  show($("#app-view"));
  show($("#session"));
  await Promise.all([loadDashboard(), loadConversations()]);
}

// ---------------------------------------------------------------- 대시보드
async function loadDashboard() {
  try {
    let s = await api(`/api/data/summary?metric=${encodeURIComponent(state.metric)}`);
    if (!s.run_id) {  // 여러 run 이 섞이면 첫 run 을 기준으로
      state.runId = s.run_ids.find((r) => r) || null;
      if (state.runId) s = await api(`/api/data/summary?metric=${encodeURIComponent(state.metric)}&run_id=${encodeURIComponent(state.runId)}`);
    }
    state.runId = s.run_id;
    state.summary = s;
    state.unit = s.unit;
    renderSummary(s);
  } catch (e) {
    $("#target-meta").textContent = errorText(e);
    $("#chart").innerHTML = `<p class="state">${esc(errorText(e))}</p>`;
    return;
  }
  await Promise.all([loadChart(), loadTable()]);
}

function renderSummary(s) {
  const label = state.cfg.metrics[s.metric]?.label || s.metric;
  $("#target-meta").textContent = `run_id ${s.run_id} · ${label} · ${s.unit} · 기록 ${kst(s.start)} ~ ${kst(s.end)}`;
  const set = (k, html, title) => { const el = document.querySelector(`[data-k="${k}"]`); el.innerHTML = html; el.title = title || ""; };
  set("count", `${s.count}<small>개</small>`, `imported ${s.imported_count} · manual ${s.manual_count}`);
  for (const k of ["mean", "min", "max"]) set(k, `${num(s[k])}<small>${esc(s.unit)}</small>`, `${s[k]} ${s.unit}`);

  const rc = s.recent_comparison;
  const cmp = $("#compare");
  if (!rc) {
    cmp.innerHTML = `<span class="basis">최근 구간 비교를 할 수 없습니다 (sim_time_s 가 있는 데이터 없음).</span>`;
  } else {
    const d = rc.difference;
    cmp.innerHTML = `
      <div><span class="label">이전 60초 · sim ${rc.previous.from_sim_s}–${rc.previous.to_sim_s}s</span>
        <span class="val">${num(rc.previous.mean)} ${esc(s.unit)}</span></div>
      <div><span class="label">최근 60초 · sim ${rc.recent.from_sim_s}–${rc.recent.to_sim_s}s</span>
        <span class="val">${num(rc.recent.mean)} ${esc(s.unit)}</span></div>
      <div class="delta-box"><span class="label">차이</span>
        <span class="val delta ${d < 0 ? "down" : "up"}">${d > 0 ? "+" : ""}${num(d)} ${esc(s.unit)}</span></div>
      <span class="basis">시뮬레이션 시간(sim_time_s) 기준 1초 구간 평균. 기록 시각(date) 기준이 아닙니다.</span>`;
  }
  $("#cautions").innerHTML = s.cautions.slice(2).map((c) => `<li>${esc(c)}</li>`).join("");
}

async function loadChart() {
  const chart = $("#chart");
  try {
    const q = new URLSearchParams({ metric: state.metric, run_id: state.runId, limit: 1000 });
    const res = await api(`/api/data?${q}`);
    state.series = res.items.filter((d) => d.sim_time_s !== null);
    if (!state.series.length) { chart.innerHTML = `<p class="state">그래프로 그릴 데이터가 없습니다</p>`; return; }
    drawChart();
  } catch (e) {
    chart.innerHTML = `<p class="state">${esc(errorText(e))}</p>`;
  }
}

function drawChart() {
  const el = $("#chart");
  const items = [...state.series].sort((a, b) => a.sim_time_s - b.sim_time_s);
  const W = el.clientWidth || 600, H = el.clientHeight || 260;
  const P = { l: 40, r: 12, t: 16, b: 26 };
  const x0 = items[0].sim_time_s, x1 = items[items.length - 1].sim_time_s + 1;
  const yMax = Math.max(1, Math.ceil(Math.max(...items.map((d) => d.value)) * 1.08));
  const sx = (v) => P.l + ((v - x0) / (x1 - x0)) * (W - P.l - P.r);
  const sy = (v) => H - P.b - (v / yMax) * (H - P.t - P.b);
  const parts = [];

  const rc = state.summary?.recent_comparison;
  if (rc) {
    for (const [w, cls, name] of [[rc.previous, "band-prev", "이전 60초"], [rc.recent, "band-recent", "최근 60초"]]) {
      const a = sx(Math.max(w.from_sim_s, x0)), b = sx(Math.min(w.to_sim_s, x1));
      parts.push(`<rect class="${cls}" x="${a}" y="${P.t}" width="${Math.max(0, b - a)}" height="${H - P.t - P.b}"/>`,
        `<text class="band-label" x="${a + 4}" y="${P.t + 11}">${name}</text>`);
    }
  }
  for (let y = 0; y <= yMax; y += yMax > 8 ? 2 : 1) {
    parts.push(`<line class="grid" x1="${P.l}" x2="${W - P.r}" y1="${sy(y)}" y2="${sy(y)}"/>`,
      `<text class="tick" x="${P.l - 6}" y="${sy(y) + 3}" text-anchor="end">${y}</text>`);
  }
  const step = (x1 - x0) > 300 ? 60 : 30;
  for (let x = Math.ceil(x0 / step) * step; x <= x1; x += step) {
    parts.push(`<text class="tick" x="${sx(x)}" y="${H - 8}" text-anchor="middle">${x}</text>`);
  }
  parts.push(`<line class="axis" x1="${P.l}" x2="${W - P.r}" y1="${H - P.b}" y2="${H - P.b}"/>`);

  const imported = items.filter((d) => d.record_type === "imported");
  if (imported.length) {
    const path = imported.map((d, i) => `${i ? "L" : "M"}${sx(d.sim_time_s + 0.5).toFixed(1)},${sy(d.value).toFixed(1)}`).join("");
    parts.push(`<path class="series" d="${path}"/>`);
  }
  for (const d of items.filter((d) => d.record_type === "manual")) {
    parts.push(`<circle class="manual" cx="${sx(d.sim_time_s + 0.5)}" cy="${sy(d.value)}" r="3.5"/>`);
  }
  parts.push(`<line class="cursor" id="cursor" y1="${P.t}" y2="${H - P.b}" visibility="hidden"/>`);
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="sim_time_s 별 1초 평균 속도 그래프, ${items.length}개">${parts.join("")}</svg><div class="tooltip" id="tip" hidden></div>`;

  const svg = el.querySelector("svg"), tip = $("#tip"), cursor = $("#cursor");
  svg.onmousemove = (ev) => {
    const r = svg.getBoundingClientRect();
    const px = ((ev.clientX - r.left) / r.width) * W;
    const t = x0 + ((px - P.l) / (W - P.l - P.r)) * (x1 - x0);
    let best = items[0];
    for (const d of items) if (Math.abs(d.sim_time_s + 0.5 - t) < Math.abs(best.sim_time_s + 0.5 - t)) best = d;
    const cx = sx(best.sim_time_s + 0.5);
    cursor.setAttribute("x1", cx); cursor.setAttribute("x2", cx); cursor.setAttribute("visibility", "visible");
    tip.innerHTML = `sim ${best.sim_time_s}s · ${num(best.value)} ${esc(best.unit)}<br>${kst(best.date)}${best.record_type === "manual" ? " · manual" : ""}`;
    tip.style.left = `${(cx / W) * r.width}px`;
    tip.style.top = `${(sy(best.value) / H) * r.height}px`;
    show(tip);
  };
  svg.onmouseleave = () => { show(tip, false); cursor.setAttribute("visibility", "hidden"); };
}

async function loadTable() {
  const rows = $("#rows");
  try {
    const q = new URLSearchParams({ metric: state.metric, run_id: state.runId, limit: PAGE, offset: state.offset });
    if (state.filter) q.set("record_type", state.filter);
    const res = await api(`/api/data?${q}`);
    state.total = res.total;
    rows.innerHTML = res.items.length ? res.items.map(rowHtml).join("") : `<tr><td colspan="6" class="state">데이터가 없습니다</td></tr>`;
    const page = Math.floor(state.offset / PAGE) + 1, pages = Math.max(1, Math.ceil(res.total / PAGE));
    $("#page-info").textContent = `${page} / ${pages} 쪽 · ${res.total}개`;
    $("#prev").disabled = state.offset === 0;
    $("#next").disabled = state.offset + PAGE >= res.total;
    state.rows = Object.fromEntries(res.items.map((d) => [d.id, d]));
  } catch (e) {
    rows.innerHTML = `<tr><td colspan="6" class="state">${esc(errorText(e))}</td></tr>`;
  }
}

function rowHtml(d) {
  const actions = d.record_type === "manual"
    ? `<div class="row-actions"><button class="btn small" data-edit="${esc(d.id)}">수정</button><button class="btn small danger" data-del="${esc(d.id)}">삭제</button></div>`
    : `<span class="lock" title="원본에서 계산한 값은 수정할 수 없습니다">읽기 전용</span>`;
  return `<tr><td>${d.sim_time_s ?? "–"}</td><td>${esc(kst(d.date))}</td><td class="num">${num(d.value)}</td>
    <td><span class="badge ${d.record_type}">${d.record_type}</span></td><td class="memo">${esc(d.memo)}</td><td>${actions}</td></tr>`;
}

function openForm(row = null) {
  state.editing = row;
  $("#form-title").textContent = row ? "수동 데이터 수정" : "수동 데이터 추가";
  $("#f-date").value = row ? row.date.slice(0, 19) : "";
  $("#f-value").value = row ? row.value : "";
  $("#f-sim").value = row?.sim_time_s ?? "";
  $("#f-memo").value = row?.memo ?? "";
  show($("#form-error"), false);
  show($("#data-form"));
  $("#f-date").focus();
}

async function submitForm(ev) {
  ev.preventDefault();
  const date = $("#f-date").value;
  const body = {
    date: (date.length === 16 ? `${date}:00` : date) + "+09:00",
    value: Number($("#f-value").value),
    memo: $("#f-memo").value.trim(),
    sim_time_s: $("#f-sim").value === "" ? null : Number($("#f-sim").value),
  };
  if (!state.editing) Object.assign(body, { run_id: state.runId, metric: state.metric, unit: state.unit });
  $("#form-submit").disabled = true;
  try {
    if (state.editing) await api(`/api/data/${encodeURIComponent(state.editing.id)}`, { method: "PUT", body });
    else await api("/api/data", { method: "POST", body });
    show($("#data-form"), false);
    toast(state.editing ? "수정했습니다" : "추가했습니다");
    await loadDashboard();
  } catch (e) {
    const el = $("#form-error");
    el.textContent = errorText(e);
    show(el);
  } finally {
    $("#form-submit").disabled = false;
  }
}

async function deleteRow(id) {
  if (!confirm("이 수동 데이터를 삭제할까요?")) return;
  try {
    await api(`/api/data/${encodeURIComponent(id)}`, { method: "DELETE" });
    toast("삭제했습니다");
    await loadDashboard();
  } catch (e) {
    toast(errorText(e));
  }
}

// ---------------------------------------------------------------- AI 비서
function renderLimits(l) {
  if (!l) return;
  const today = l.used_today === null ? "?" : l.used_today;
  $("#limits").textContent = `오늘 ${today}/${l.daily_limit}회 · 1시간 ${l.used_last_hour}/${l.per_hour}회`;
}

async function loadConversations() {
  const list = $("#conv-list");
  try {
    const res = await api("/api/conversations");
    list.innerHTML = res.items.length
      ? res.items.map((c) => `<li class="item ${c.id === state.convId ? "active" : ""}">
          <button class="open" data-open="${esc(c.id)}" title="${esc(c.title)}">${esc(c.title)}<small>${esc(kst(c.updated_at).slice(5, 16))}</small></button>
          <button class="del" data-delconv="${esc(c.id)}" aria-label="대화 삭제" title="대화 삭제">✕</button></li>`).join("")
      : `<li class="state">저장된 대화가 없습니다</li>`;
  } catch (e) {
    list.innerHTML = `<li class="state">${esc(errorText(e))}</li>`;
  }
}

function assistantHtml(m, { unsaved = false } = {}) {
  const g = m.grounding;
  const flags = g && !g.ok
    ? `<div class="grounding">⚠ 자동 근거 검사에서 확인이 필요한 부분이 있습니다.
        ${g.unverified_numbers.length ? `<br>통계에 없는 수치: ${g.unverified_numbers.map(esc).join(", ")}` : ""}
        ${g.assertive_cause_phrases.length ? `<br>원인 단정 표현: ${g.assertive_cause_phrases.map(esc).join(", ")}` : ""}
        <br><small>정규식 기반 보조 검사입니다. 통과해도 사실 정확성을 보장하지 않습니다.</small></div>`
    : "";
  const b = m.stats_basis;
  const basis = b ? `<details class="basis"><summary>사용한 통계 근거</summary><dl>
      <dt>출처</dt><dd>${esc(b.source)}</dd>
      <dt>데이터 수</dt><dd>${b.count}</dd>
      <dt>평균·최소·최대</dt><dd>${b.mean} · ${b.min} · ${b.max} m/s</dd>
      ${b.recent_comparison ? `<dt>최근/이전 60초</dt><dd>${b.recent_comparison.recent.mean} / ${b.recent_comparison.previous.mean} m/s (sim_time_s)</dd>` : ""}
      <dt>계산 시각</dt><dd>${esc(kst(b.computed_at))}${b.from_cache ? " (캐시)" : ""}</dd></dl></details>` : "";
  return `<article class="msg assistant">
    <div class="msg-head"><span class="badge ai">AI 분석 · 참고용</span>${m.model ? `<span>${esc(m.model)}</span>` : ""}
      ${unsaved ? `<span class="unsaved">저장되지 않음</span>` : ""}</div>
    <div class="msg-body">${renderMarkdown(m.content)}</div>${flags}${basis}</article>`;
}

function userHtml(text) {
  return `<article class="msg user">${esc(text)}</article>`;
}

function renderThread(messages) {
  const t = $("#thread");
  t.innerHTML = messages.length
    ? messages.map((m) => (m.role === "user" ? userHtml(m.content) : assistantHtml(m))).join("")
    : `<p class="state">질문을 입력하면 대화가 시작됩니다.</p>`;
  t.scrollTop = t.scrollHeight;
}

async function openConversation(id) {
  try {
    const c = await api(`/api/conversations/${encodeURIComponent(id)}`);
    state.convId = c.id;
    renderThread(c.messages);
    await loadConversations();
  } catch (e) {
    toast(errorText(e));
    if (e.status === 404) { state.convId = null; await loadConversations(); }
  }
}

async function newConversation() {
  try {
    const c = await api("/api/conversations", { method: "POST", body: { run_id: state.runId, metric: state.metric } });
    state.convId = c.id;
    renderThread([]);
    await loadConversations();
    $("#question").focus();
  } catch (e) {
    toast(errorText(e));
  }
}

async function deleteConversation(id) {
  if (!confirm("이 대화를 삭제할까요? 메시지도 모두 지워집니다.")) return;
  try {
    await api(`/api/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (state.convId === id) { state.convId = null; renderThread([]); }
    await loadConversations();
    toast("대화를 삭제했습니다");
  } catch (e) {
    toast(errorText(e));
  }
}

async function ask(ev) {
  ev.preventDefault();
  const q = $("#question").value.trim();
  if (!q || state.busy) return;
  state.busy = true;
  $("#ask-btn").disabled = true;
  show($("#ask-error"), false);
  const t = $("#thread");
  if (t.querySelector(".state")) t.innerHTML = "";
  t.insertAdjacentHTML("beforeend", userHtml(q) + `<div class="thinking" id="thinking"><span class="dots"><i></i><i></i><i></i></span>Firestore 통계로 분석 중…</div>`);
  t.scrollTop = t.scrollHeight;
  try {
    const res = await api("/api/chat", {
      method: "POST",
      body: { message: q, ...(state.convId ? { conversation_id: state.convId } : { run_id: state.runId, metric: state.metric }) },
    });
    $("#thinking")?.remove();
    t.insertAdjacentHTML("beforeend", assistantHtml({ content: res.answer, model: res.model, grounding: res.grounding, stats_basis: res.stats_basis }));
    state.convId = res.conversation_id;
    $("#question").value = "";
    updateCounter();
    await loadConversations();
  } catch (e) {
    $("#thinking")?.remove();
    if (e instanceof ApiError && e.body.answer) {
      // 답변은 생성됐지만 저장 실패: 화면에만 보여 주고 "저장되지 않음" 표시 (질문은 입력창에 남김)
      t.insertAdjacentHTML("beforeend", assistantHtml({ content: e.body.answer }, { unsaved: true }));
    } else if (t.lastElementChild?.classList.contains("user")) {
      t.lastElementChild.remove();  // 기록되지 않은 질문 말풍선은 지우고, 질문은 입력창에 남김
      if (!t.children.length) t.innerHTML = `<p class="state">질문을 입력하면 대화가 시작됩니다.</p>`;
    }
    const el = $("#ask-error");
    el.textContent = errorText(e);
    show(el);
  } finally {
    t.scrollTop = t.scrollHeight;
    state.busy = false;
    $("#ask-btn").disabled = false;
    api("/api/me").then((me) => renderLimits(me.chat_limits)).catch(() => {});
  }
}

function updateCounter() {
  $("#counter").textContent = `${$("#question").value.length} / ${state.cfg?.max_message_chars ?? 2000}`;
}

// ---------------------------------------------------------------- 이벤트
function bind() {
  document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => { t.classList.toggle("active", t === tab); t.setAttribute("aria-selected", t === tab); });
    document.querySelectorAll("[data-pane]").forEach((p) => p.classList.toggle("shown", p.dataset.pane === tab.dataset.tab));
    if (tab.dataset.tab === "dashboard" && state.series) drawChart();
  }));
  $("#dashboard").classList.add("shown");

  $("#filter").onchange = (e) => { state.filter = e.target.value; state.offset = 0; loadTable(); };
  $("#prev").onclick = () => { state.offset = Math.max(0, state.offset - PAGE); loadTable(); };
  $("#next").onclick = () => { state.offset += PAGE; loadTable(); };
  $("#add-btn").onclick = () => openForm();
  $("#form-cancel").onclick = () => show($("#data-form"), false);
  $("#data-form").onsubmit = submitForm;
  $("#rows").onclick = (e) => {
    const edit = e.target.closest("[data-edit]"), del = e.target.closest("[data-del]");
    if (edit) openForm(state.rows[edit.dataset.edit]);
    if (del) deleteRow(del.dataset.del);
  };

  $("#new-conv").onclick = newConversation;
  $("#conv-list").onclick = (e) => {
    const open = e.target.closest("[data-open]"), del = e.target.closest("[data-delconv]");
    if (open) openConversation(open.dataset.open);
    if (del) deleteConversation(del.dataset.delconv);
  };
  $("#ask-form").onsubmit = ask;
  $("#question").addEventListener("input", updateCounter);
  $("#question").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) $("#ask-form").requestSubmit();
  });

  let timer;
  new ResizeObserver(() => { clearTimeout(timer); timer = setTimeout(() => state.series && drawChart(), 120); }).observe($("#chart"));
}

bind();
boot();
