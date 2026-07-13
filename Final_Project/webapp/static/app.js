/* ============================================================
   MedQuery AI — frontend logic
   ============================================================ */
const $  = (s, r=document) => r.querySelector(s);
const $$ = (s, r=document) => Array.from(r.querySelectorAll(s));
const esc = s => (s ?? "").replace(/[&<>"']/g, c =>
  ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));

/* ---------- theme ---------- */
function applyTheme(t){
  document.documentElement.classList.toggle("light", t === "light");
  document.documentElement.classList.toggle("dark", t !== "light");
  try { localStorage.setItem("medquery-theme", t); } catch(e){}
}
function toggleTheme(){
  const now = document.documentElement.classList.contains("light") ? "light" : "dark";
  applyTheme(now === "light" ? "dark" : "light");
}
(function initTheme(){
  let t = "dark";
  try { t = localStorage.getItem("medquery-theme") || "dark"; } catch(e){}
  applyTheme(t);
})();

/* ---------- view switching ---------- */
const views = { chat:"view-chat", dashboard:"view-dashboard", history:"view-history", kb:"view-kb", settings:"view-settings" };
const crumbLabel = { chat:"New Session", dashboard:"Dashboard", history:"History", kb:"Knowledge Base", settings:"Settings" };
function showView(name){
  Object.values(views).forEach(id => $("#"+id).classList.add("hidden"));
  $("#"+views[name]).classList.remove("hidden");
  $$(".nav-item").forEach(b => b.classList.toggle("active", b.dataset.view === name));
  $("#crumbNow").textContent = crumbLabel[name] || "Session";
  if (name === "dashboard") loadDashboard();
  if (name === "history")   loadHistory();
  if (name === "kb")        loadKB();
  if (name === "settings")  loadSettings();
  closeDrawers();
}
$$(".nav-item").forEach(b => b.addEventListener("click", () => showView(b.dataset.view)));

/* ---------- mobile drawers ---------- */
function closeDrawers(){ $("#sidebar").classList.remove("open"); $("#context").classList.remove("open"); $("#scrim").style.display="none"; }
$("#menuBtn").addEventListener("click", () => { $("#sidebar").classList.toggle("open"); $("#scrim").style.display = $("#sidebar").classList.contains("open") ? "block":"none"; });
$("#ctxBtn").addEventListener("click", () => { $("#context").classList.toggle("open"); $("#scrim").style.display = $("#context").classList.contains("open") ? "block":"none"; });
$("#scrim").addEventListener("click", closeDrawers);

/* ---------- theme buttons ---------- */
$("#themeBtn").addEventListener("click", toggleTheme);
$("#themeBtn2")?.addEventListener("click", toggleTheme);

/* ---------- context tabs ---------- */
$$(".tab").forEach(t => t.addEventListener("click", () => {
  $$(".tab").forEach(x => x.classList.remove("active"));
  $$(".tab-panel").forEach(x => x.classList.remove("active"));
  t.classList.add("active");
  $("#tab-"+t.dataset.tab).classList.add("active");
}));

/* ---------- composer ---------- */
const input = $("#input"), sendBtn = $("#send"), stream = $("#stream");
function autogrow(){ input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 160) + "px"; }
input.addEventListener("input", autogrow);
input.addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey){ e.preventDefault(); send(); }
});
sendBtn.addEventListener("click", send);
$$(".chip").forEach(c => c.addEventListener("click", () => { input.value = c.dataset.q; send(); }));
$("#attachBtn").addEventListener("click", () => flashReady("Attachments aren't enabled in this demo."));

let busy = false;
function setBusy(on){
  busy = on;
  sendBtn.disabled = on;
  $("#readyState").textContent = on ? "● PROCESSING…" : "● QUERY ASSISTANT READY";
  $("#readyState").style.color = on ? "var(--amber)" : "var(--emerald)";
  $$(".agent-list li")[0].classList.toggle("busy", on); // Orchestrator
}
function flashReady(msg){
  const el = $("#readyState"); const prev = el.textContent;
  el.textContent = "● " + msg.toUpperCase();
  setTimeout(() => { if(!busy){ el.textContent = prev; } }, 2200);
}

/* ---------- rendering answers ---------- */
function citeify(s){
  return s.replace(/\(([a-z0-9_]*_policy[a-z0-9_,\s]*)\)/gi,
    (m, g) => `<span class="cite">${esc(g.trim())}</span>`);
}
function inlineFmt(s){
  s = esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  return citeify(s);
}
function formatAnswer(text){
  const lines = (text || "").split("\n");
  let html = "", para = [], list = [];
  const flushP = () => { if (para.length){ html += `<p>${inlineFmt(para.join(" "))}</p>`; para = []; } };
  const flushL = () => { if (list.length){ html += `<ul>${list.map(li=>`<li>${inlineFmt(li)}</li>`).join("")}</ul>`; list = []; } };
  for (const raw of lines){
    const line = raw.trim();
    if (!line){ flushP(); flushL(); continue; }
    const m = line.match(/^(?:[-*•]|\d+\.)\s+(.*)$/);
    if (m){ flushP(); list.push(m[1]); }
    else  { flushL(); para.push(line); }
  }
  flushP(); flushL();
  return html || `<p>${inlineFmt(text)}</p>`;
}
const ROUTE_META = {
  DATABASE:{ pill:"route-db", label:"PATIENT DATABASE", mid:"SQL Agent" },
  POLICY:{ pill:"route-policy", label:"POLICY LIBRARY", mid:"RAG Agent" },
  SMALLTALK:{ pill:"route-other", label:"ASSISTANT", mid:"Direct reply" },
  OUT_OF_DOMAIN:{ pill:"route-other", label:"OUT OF SCOPE", mid:"Direct reply" },
};
function pipelineHTML(pipe){
  return pipe.map((n,i)=>{
    const hot = (i === 1) ? " hot" : "";
    const arrow = i < pipe.length-1 ? `<span class="arrow">→</span>` : "";
    return `<span class="node${hot}">${esc(n)}</span>${arrow}`;
  }).join("");
}
function addUser(q){
  hideEmpty();
  const el = document.createElement("div");
  el.className = "msg user";
  el.innerHTML = `<div class="bubble-user">${esc(q)}</div>`;
  stream.appendChild(el); scrollDown();
}
function addThinking(){
  const el = document.createElement("div");
  el.className = "msg bot thinking";
  el.innerHTML = `<div class="card"><div class="dots"><i></i><i></i><i></i></div><span>Routing your question…</span></div>`;
  stream.appendChild(el); scrollDown();
  return el;
}
function addBot(res){
  const rm = ROUTE_META[res.route] || ROUTE_META.SMALLTALK;
  const refused = res.evidence && res.evidence.type === "policy" && res.evidence.grounded === false;
  const meta = [];
  meta.push(`<span><b>route</b> ${esc(res.route)}</span>`);
  meta.push(`<span><b>latency</b> ${res.latency_s}s</span>`);
  if (res.sql) meta.push(`<span><b>sql</b> read-only ✓</span>`);
  const el = document.createElement("div");
  el.className = "msg bot";
  el.innerHTML = `
    <div class="card">
      <div class="pipeline">
        ${pipelineHTML(res.pipeline || ["Orchestrator", rm.mid, "Formatter"])}
        <span class="route-pill ${rm.pill}">${rm.label}</span>
      </div>
      <div class="answer ${refused ? "refused":""}">${formatAnswer(res.answer)}</div>
      <div class="answer-meta">${meta.join("")}</div>
    </div>`;
  stream.appendChild(el); scrollDown();
}
function addError(msg){
  const el = document.createElement("div");
  el.className = "msg bot";
  el.innerHTML = `<div class="card"><div class="answer refused">${esc(msg)}</div></div>`;
  stream.appendChild(el); scrollDown();
}
function hideEmpty(){ const e = $("#empty"); if (e) e.remove(); }
function scrollDown(){ stream.scrollTop = stream.scrollHeight; }

/* ---------- send ---------- */
async function send(){
  const q = input.value.trim();
  if (!q || busy) return;
  input.value = ""; autogrow();
  addUser(q);
  setBusy(true);
  const thinking = addThinking();
  try{
    const r = await fetch("/api/chat", {
      method:"POST", headers:{ "Content-Type":"application/json" },
      body: JSON.stringify({ question:q })
    });
    const data = await r.json();
    thinking.remove();
    if (!r.ok || data.error){ addError(data.error || "The assistant is unavailable right now."); }
    else { addBot(data); renderContext(data); loadStats(); }
  }catch(e){
    thinking.remove();
    addError("Couldn't reach the server. Is the Flask app running?");
  }finally{
    setBusy(false); input.focus();
  }
}

/* ---------- context panel ---------- */
function renderContext(res){
  const ev = res.evidence || { type:"none" };
  // Sources tab -----------------------------------------------------------
  let src = "";
  if (ev.type === "policy"){
    const items = ev.sources || [];
    src += `<div class="ctx-section-head"><div class="t">Retrieved sources</div><span class="count-pill">${items.length} items</span></div>`;
    if (!items.length){ src += `<div class="ctx-empty">No chunks retrieved.</div>`; }
    items.forEach((s, i) => {
      src += `
      <div class="src-card">
        <div class="src-top">
          <span class="src-num">${i+1}</span>
          <span class="src-name">${esc(s.source)}</span>
          <span class="src-match">${s.match}% match</span>
        </div>
        <div class="src-snip">${esc(s.snippet)}…</div>
        <div class="src-bar"><i style="width:${s.match}%"></i></div>
        <div class="src-foot"><span>score ${s.score.toFixed(3)}</span><span>vector: FAISS</span></div>
      </div>`;
    });
    if (ev.grounded === false){
      src += `<div class="ctx-empty">Best score fell below the ${ev.threshold} threshold, so the assistant refused instead of guessing.</div>`;
    }
  } else if (ev.type === "database"){
    src += `<div class="ctx-section-head"><div class="t">Query &amp; result</div><span class="count-pill">SQL</span></div>`;
    src += `<div class="sql-card"><div class="h">GENERATED SQL · READ-ONLY</div><div class="sql-code">${esc(ev.sql || "—")}</div>`;
    if (ev.ok && ev.rows && ev.rows.length){
      src += `<div class="tbl-wrap"><table class="res"><thead><tr>${
        ev.columns.map(c=>`<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>${
        ev.rows.slice(0,20).map(row=>`<tr>${row.map(v=>`<td>${esc(String(v))}</td>`).join("")}</tr>`).join("")
      }</tbody></table></div>`;
    } else if (ev.error){
      src += `<div class="sql-code" style="color:var(--amber)">${esc(ev.error)}</div>`;
    }
    src += `</div>`;
  } else {
    src = `<div class="ctx-empty">This route needs no retrieval — it was answered directly, with no database query or document lookup.</div>`;
  }
  $("#tab-sources").innerHTML = src;

  // Trace tab -------------------------------------------------------------
  const steps = res.trace || [];
  $("#tab-trace").innerHTML =
    `<div class="ctx-section-head"><div class="t">Pipeline trace</div><span class="count-pill">${res.latency_s}s total</span></div>` +
    steps.map(s => `
      <div class="trace-step">
        <span class="trace-dot"></span>
        <div class="trace-body">
          <div class="trace-name">${esc(s.stage)}<span class="trace-ms">${s.ms}ms</span></div>
          <div class="trace-detail">${esc(s.detail)}</div>
        </div>
      </div>`).join("");
}

/* ---------- stats (context tab + dashboard + settings) ---------- */
let lastStats = null;
async function loadStats(){
  try{
    const r = await fetch("/api/stats"); const s = await r.json();
    if (s.error) return;
    lastStats = s;
    renderStatsPanel(s);
  }catch(e){}
}
function renderStatsPanel(s){
  const routes = Object.entries(s.routes || {}).map(([k,v]) =>
    `<div class="kv"><span class="k">${esc(k)}</span><span class="v">${v}</span></div>`).join("") ||
    `<div class="ctx-empty">No queries yet this session.</div>`;
  $("#tab-stats").innerHTML = `
    <div class="stat-mini">
      <div class="box"><div class="n">${s.patients.toLocaleString()}</div><div class="l">Patients</div></div>
      <div class="box"><div class="n">${s.admissions.toLocaleString()}</div><div class="l">Admissions</div></div>
      <div class="box"><div class="n">${s.policies}</div><div class="l">Policy docs</div></div>
      <div class="box"><div class="n">${s.chunks}</div><div class="l">Vector chunks</div></div>
    </div>
    <div class="ctx-section-head"><div class="t">This session</div></div>
    <div class="kv"><span class="k">Questions asked</span><span class="v">${s.session_queries}</span></div>
    <div class="kv"><span class="k">Avg latency</span><span class="v">${s.avg_latency_s}s</span></div>
    ${routes}
    <div class="ctx-section-head" style="margin-top:16px"><div class="t">Engine</div></div>
    <div class="kv"><span class="k">LLM</span><span class="v">${esc(s.model)}</span></div>
    <div class="kv"><span class="k">Threshold</span><span class="v">${s.threshold}</span></div>`;
}

/* ---------- dashboard ---------- */
async function loadDashboard(){
  const r = await fetch("/api/stats"); const s = await r.json();
  if (s.error){ $("#dashStats").innerHTML = `<div class="empty-note">${esc(s.error)}</div>`; return; }
  lastStats = s;
  $("#dashStats").innerHTML = [
    ["Patients", s.patients.toLocaleString()],
    ["Admissions", s.admissions.toLocaleString()],
    ["Distinct hospitals", s.hospitals.toLocaleString()],
    ["Policy documents", s.policies],
    ["Vector chunks", s.chunks],
    ["Questions this session", s.session_queries],
  ].map(([l,n]) => `<div class="stat-card"><div class="n">${n}</div><div class="l">${l}</div></div>`).join("");
  const max = Math.max(...s.conditions.map(c=>c.count), 1);
  $("#dashConditions").innerHTML = s.conditions.map(c => `
    <div class="bar-row">
      <span>${esc(c.name)}</span>
      <span class="bar-track"><span class="bar-fill" style="width:${Math.round(c.count/max*100)}%"></span></span>
      <span class="bar-val">${c.count.toLocaleString()}</span>
    </div>`).join("");
}

/* ---------- history ---------- */
async function loadHistory(){
  const r = await fetch("/api/history"); const log = await r.json();
  const body = $("#historyBody");
  if (!log.length){ body.innerHTML = `<div class="empty-note">No questions yet this session. Ask something in the Query Assistant.</div>`; return; }
  body.innerHTML = log.slice().reverse().map(item => {
    const rm = ROUTE_META[item.route] || ROUTE_META.SMALLTALK;
    let extra = "";
    if (item.sql) extra = `<div class="hist-sql">${esc(item.sql)}</div>`;
    else if (item.sources && item.sources.length)
      extra = `<div class="hist-src">sources: ${item.sources.map(s=>esc(s.source)).join(", ")}</div>`;
    return `<div class="hist-item">
      <div class="hist-top">
        <span class="hist-q">${esc(item.query)}</span>
        <span class="route-pill ${rm.pill}">${rm.label}</span>
        <span class="mono-label dim">${item.latency_s}s</span>
      </div>${extra}</div>`;
  }).join("");
}

/* ---------- knowledge base ---------- */
async function loadKB(){
  const r = await fetch("/api/policies"); const docs = await r.json();
  $("#kbBody").innerHTML = docs.map((d, i) => `
    <div class="kb-doc${i===0?" open":""}">
      <div class="kb-head">
        <svg viewBox="0 0 24 24" width="14" height="14" class="kb-chev"><path fill="currentColor" d="M8 5l8 7l-8 7z"/></svg>
        <span class="kb-name">${esc(d.name)}</span>
        <span class="count-pill">${d.text.length} chars</span>
      </div>
      <div class="kb-body">${esc(d.text)}</div>
    </div>`).join("");
  $$("#kbBody .kb-head").forEach(h => h.addEventListener("click", () => h.parentElement.classList.toggle("open")));
}

/* ---------- settings ---------- */
async function loadSettings(){
  try{
    const r = await fetch("/api/health"); const h = await r.json();
    const s = lastStats;
    $("#settingModelDesc").innerHTML =
      `LLM: <b>${esc(h.model)}</b> (Groq) · Embeddings: <b>all-MiniLM-L6-v2</b> (local) · Vector store: <b>FAISS</b>.` +
      (h.api_key_set ? "" : `<br><span style="color:var(--amber)">GROQ_API_KEY is not set on the server — set it and restart to enable answers.</span>`);
  }catch(e){}
}

/* ---------- boot ---------- */
loadStats();
input.focus();
