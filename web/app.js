"use strict";

const POLL_MS = 5000;
const $ = (id) => document.getElementById(id);

// ---- 포맷 ----------------------------------------------------------------
function human(n) {
  // 누적 처리 토큰은 실측 50억대까지 간다. B 단위가 없으면 "5013.6M"이 된다.
  if (n === null || n === undefined) return "–";
  if (n >= 1e9) return (n / 1e9).toFixed(1) + "B";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return Math.round(n / 1e3) + "K";
  return String(n);
}
const pct = (r) => (r === null || r === undefined) ? "–" : Math.round(r * 100) + "%";
const comma = (n) => (n ?? 0).toLocaleString("ko-KR");

function clock(iso) {
  const d = new Date(iso);
  return isNaN(d) ? "" : d.toLocaleTimeString("ko-KR", { hour12: false });
}

const STATUS_LABEL = { ok: "여유", caution: "주의", danger: "위험", unknown: "미상" };

// ---- 실시간 세션 ----------------------------------------------------------
function renderSessions(data) {
  const box = $("sessions");
  if (!data.sessions.length) {
    box.innerHTML = '<p class="empty">최근 15분 안에 활동한 세션이 없습니다.</p>';
    return;
  }
  const warn = data.warn_threshold;
  box.innerHTML = data.sessions.map((s) => {
    const width = Math.min(100, (s.ratio ?? 0) * 100);
    return `
      <article class="card ${escape_(s.status)}">
        <div class="card-top">
          <div>
            <div class="title">${escape_(s.title)}</div>
            <div class="meta">${escape_(s.project)} · ${escape_(s.model)} ·
              활동 ${clock(s.last_ts)}</div>
          </div>
          <div class="big">${pct(s.ratio)}</div>
        </div>
        <div class="bar-track">
          <div class="bar-fill" style="width:${width}%"></div>
          <div class="bar-mark" style="left:${warn * 100}%"
               title="경고 임계 ${pct(warn)}"></div>
        </div>
        <div class="card-foot">
          <span>컨텍스트 ${human(s.used)} / ${human(s.limit)}</span>
          <span class="badge">${STATUS_LABEL[s.status]}</span>
        </div>
      </article>`;
  }).join("");
}

function escape_(t) {
  const d = document.createElement("div");
  d.textContent = t ?? "";
  return d.innerHTML;
}

// ---- 집계 ----------------------------------------------------------------
function renderSummary(d) {
  const days = d.days.length;
  $("t-calls").textContent = comma(d.totals.calls);
  $("t-calls-sub").textContent = days ? `하루 평균 ${comma(Math.round(d.totals.calls / days))}` : "";
  $("t-tokens").textContent = human(d.totals.total);
  $("t-tokens-sub").textContent =
    `캐시 읽기 ${pct(d.totals.cache_read_ratio)} · 출력 ${human(d.totals.output)}`;
  $("t-compact").textContent = d.compacts.count + "회";
  $("t-compact-sub").textContent = d.compacts.count
    ? `버린 토큰 ${human(d.compacts.dropped_total)} · 회당 ${human(d.compacts.dropped_avg)}`
    : "아직 없음";
  $("t-unused").textContent = d.unused.count + "개";

  $("range-note").textContent = d.since ? `${d.since} 이후` : "전체 기간";
  drawChart(d.days);
  rank("r-mcp", "n-mcp", d.tools.mcp);
  rank("r-skill", "n-skill", d.tools.skill);
  rank("r-tools", "n-tools", d.tools.tools);

  $("foot").textContent = d.bad_lines
    ? `읽지 못한 줄 ${comma(d.bad_lines)}개 (건너뜀) · 갱신 ${clock(new Date().toISOString())}`
    : `갱신 ${clock(new Date().toISOString())}`;
}

function rank(listId, countId, rows) {
  const ol = $(listId);
  $(countId).textContent = rows.length ? `${rows.length}종 사용` : "";
  if (!rows.length) { ol.innerHTML = '<li class="empty">기록 없음</li>'; return; }
  const max = rows[0][1];
  ol.innerHTML = rows.slice(0, 8).map(([label, n]) => `
    <li><span class="name">${escape_(label)}</span><span class="n">${comma(n)}</span>
      <div class="mini" style="width:${(n / max) * 100}%"></div></li>`).join("");
}

// ---- 차트 (인라인 SVG, 외부 라이브러리 없음) --------------------------------
function drawChart(days) {
  const host = $("chart");
  if (!days.length) { host.innerHTML = '<p class="empty">이 기간에 기록이 없습니다.</p>'; return; }

  const W = 900, H = 240, PAD_L = 44, PAD_B = 28, PAD_T = 12;
  const groups = ["mcp", "skill", "tools"];
  const max = Math.max(1, ...days.map((d) => groups.reduce((a, g) => a + d.calls[g], 0)));
  const step = (W - PAD_L) / days.length;
  const bw = Math.min(28, step * 0.6);
  const y = (v) => PAD_T + (H - PAD_T - PAD_B) * (1 - v / max);

  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => {
    const v = Math.round(max * f);
    return `<line x1="${PAD_L}" x2="${W}" y1="${y(v)}" y2="${y(v)}" class="grid"/>
            <text x="${PAD_L - 8}" y="${y(v) + 4}" class="ax" text-anchor="end">${human(v)}</text>`;
  }).join("");

  const bars = days.map((d, i) => {
    const x = PAD_L + step * i + (step - bw) / 2;
    let acc = 0;
    const segs = groups.map((g) => {
      const v = d.calls[g];
      if (!v) return "";
      acc += v;
      const top = y(acc), bottom = y(acc - v);
      return `<rect x="${x}" y="${top}" width="${bw}" height="${Math.max(1, bottom - top)}"
                class="b-${g}"><title>${d.day} ${g} ${comma(v)}회</title></rect>`;
    }).join("");
    const label = d.day.slice(5).replace("-", "/");
    return segs + `<text x="${x + bw / 2}" y="${H - 8}" class="ax"
                     text-anchor="middle">${label}</text>`;
  }).join("");

  host.innerHTML =
    `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="일별 도구 호출 수"
          preserveAspectRatio="xMidYMid meet">${ticks}${bars}</svg>`;
}

// ---- 폴링 ----------------------------------------------------------------
let range = "7d";
let online = null;

function setConn(ok) {
  if (ok === online) return;
  online = ok;
  const el = $("conn");
  el.textContent = ok ? "연결됨" : "연결 끊김";
  el.className = "conn " + (ok ? "up" : "down");
}

async function get(path) {
  const r = await fetch(path, { cache: "no-store" });
  if (!r.ok) throw new Error(r.status);
  return r.json();
}

async function tickLive({ force = false } = {}) {
  // 배경 탭에서는 쉰다. 다만 첫 렌더는 예외 — 안 그러면 배경 탭에서 연 사용자는
  // 빈 상태 문구조차 없는 빈 영역을 보게 된다.
  if (document.hidden && !force) return;
  try { renderSessions(await get("/api/live")); setConn(true); }
  catch { setConn(false); }             // 화면은 그대로 둔다
}

async function loadSummary() {
  try { renderSummary(await get(`/api/summary?range=${range}`)); setConn(true); }
  catch { setConn(false); }
}

$("range-tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  range = b.dataset.range;
  [...e.currentTarget.children].forEach((c) => c.classList.toggle("on", c === b));
  loadSummary();
});

document.addEventListener("visibilitychange", () => { if (!document.hidden) tickLive(); });

loadSummary();
tickLive({ force: true });
setInterval(() => tickLive(), POLL_MS);
