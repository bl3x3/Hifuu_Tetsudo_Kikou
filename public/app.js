const app = document.querySelector("#app");
const params = new URLSearchParams(location.search);
const isHost = location.pathname === "/host";
const isScreen = location.pathname === "/screen";
const isPhone = location.pathname === "/join";
let gameText, names;
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const duration = (minutes) => {
  const n = Math.max(0, Math.ceil(minutes));
  return n >= 60
    ? `${Math.floor(n / 60)}小时${n % 60 ? `${n % 60}分钟` : ""}`
    : `${n}分钟`;
};
const journeyClock = (minutes) => {
  const n = Math.max(0, Math.ceil(minutes));
  return `${String(Math.floor(n / 60)).padStart(2, "0")}:${String(n % 60).padStart(2, "0")}`;
};
const rail = (tick) => {
  const min = 960 + Math.floor(tick * 15);
  return `第${Math.floor(min / 1440) + 1}日 ${String(Math.floor(min / 60) % 24).padStart(2, "0")}:${String(min % 60).padStart(2, "0")}`;
};
let config,
  assets,
  mapText,
  state,
  room = params.get("room")?.toUpperCase() || "",
  credential = "",
  address = "",
  template = "random";
let polling = false,
  busy = false,
  renderKey = "",
  sequence = 0,
  replayTimer,
  replayTick = null,
  sound = true,
  audioCtx;
let seenArrivalKeys = new Set(),
  arrivalSoundInitialized = false,
  selectedCard = null,
  selectedDestination = null,
  lastMessageId = 0;
let boardObserver,
  replayView = false,
  phoneView = "",
  axisOffer = "",
  connectionOK = true,
  learnedAddress = "";
const emblem =
  '<svg class="journey-emblem" viewBox="0 0 100 100" aria-hidden="true"><circle cx="50" cy="50" r="42"/><path d="M22 81 59 19M32 85 67 26M44 47Q64 43 80 54M49 39Q69 35 86 47M31 68 42 74M39 55 50 61M47 42 58 48"/><circle cx="23" cy="29" r="2"/><circle cx="76" cy="21" r="2"/><path d="m18 23 11 12M19 34l8-10"/></svg>';
function configureArt() {
  for (const [key, path] of Object.entries(assets.scenes || {})) {
    if (path)
      document.documentElement.style.setProperty(
        `--art-${key}`,
        `url(${JSON.stringify(path)})`,
      );
  }
}
// Polling must not close notebooks, move a station list, or steal keyboard focus.
function preserveView(root, draw, sameView = true) {
  const opened = sameView
    ? [...root.querySelectorAll("details[id]")]
        .filter((e) => e.open)
        .map((e) => e.id)
    : [];
  const axis = root.querySelector(".station-axis");
  const scroll = sameView
    ? { x: scrollX, y: scrollY, axis: axis?.scrollTop }
    : null;
  const active = root.contains(document.activeElement)
    ? document.activeElement
    : null;
  const focus =
    sameView && active
      ? {
          id: active.id,
          summary:
            active.tagName === "SUMMARY" ? active.parentElement.id : null,
          action: active.dataset.action,
          destination: active.dataset.destination,
          player: active.dataset.player,
        }
      : null;
  const selections = sameView
    ? [...root.querySelectorAll("select[id]")].map((e) => [e.id, e.value])
    : [];
  // Retain the actual scenery node so regular state updates do not restart its animation.
  const scenery = sameView ? root.querySelector(".window-landscape") : null;
  const animationTime = scenery?.getAnimations()[0]?.currentTime;
  draw();
  if (scenery && root.querySelector(".window-landscape")) {
    root.querySelector(".window-landscape").replaceWith(scenery);
    const animation = scenery.getAnimations()[0];
    if (animation && animationTime != null)
      animation.currentTime = animationTime;
  }
  for (const id of opened) {
    const e = document.getElementById(id);
    if (e) e.open = true;
  }
  for (const [id, value] of selections) {
    const e = document.getElementById(id);
    if (e && [...e.options].some((o) => o.value === value)) e.value = value;
  }
  if (focus) {
    const el = focus.id
      ? document.getElementById(focus.id)
      : focus.summary
        ? document.getElementById(focus.summary)?.querySelector("summary")
        : [...root.querySelectorAll("[data-action]")].find(
            (e) =>
              e.dataset.action === focus.action &&
              e.dataset.destination === focus.destination &&
              e.dataset.player === focus.player,
          );
    el?.focus({ preventScroll: true });
  }
  if (scroll) {
    const el = root.querySelector(".station-axis");
    if (el && scroll.axis !== undefined) el.scrollTop = scroll.axis;
    window.scrollTo(scroll.x, scroll.y);
  } else window.scrollTo(0, 0);
}
const storageKey = (code) =>
  `${isHost ? "hifuu-host" : "hifuu-player"}:${code}`;
function saveCredential(code, value) {
  sessionStorage.setItem(storageKey(code), value);
  localStorage.setItem(storageKey(code), value);
}
function loadCredential(code) {
  return (
    sessionStorage.getItem(storageKey(code)) ||
    localStorage.getItem(storageKey(code)) ||
    ""
  );
}
function toast(message) {
  const el = document.querySelector("#toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => {
    el.classList.remove("show");
    el.textContent = "";
  }, 4500);
}
async function request(path, body, auth = credential) {
  const response = await fetch(path, {
    method: body ? "POST" : "GET",
    cache: "no-store",
    headers: {
      ...(auth ? { Authorization: `Bearer ${auth}` } : {}),
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(4500),
  });
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.error || "请求失败");
    error.status = response.status;
    error.code = data.code;
    throw error;
  }
  return data;
}
const station = (id) => config.stations.find((s) => s.id === id)?.name || "—";
const roleBadge = (role, label) => {
  const key = names[role] ? role : "unknown";
  return `<span class="role-icon">${assets.roles[key] ? `<img src="${esc(assets.roles[key])}" alt="${esc(names[key] || "身份未公开")}">` : esc(label)}</span>`;
};
const publicRole = (player) => (names[player?.role] ? player.role : "unknown");
const publicAvatar = (player) =>
  `<span class="public-avatar">${assets.roles[publicRole(player)] ? `<img src="${esc(assets.roles[publicRole(player)])}" alt="${esc(names[player?.role] || "身份未公开")}">` : "秘"}</span>`;
function activityEntries(limit = 8) {
  const players = new Map(
    (state?.players || []).map((player) => [player.id, player]),
  );
  const arrivals = (state?.arrivals || []).map((event) => ({
    kind: "arrival",
    id: `arrival:${event.player}:${event.station}:${event.tick}`,
    player: event.player,
    seat: players.get(event.player)?.seat,
    name: players.get(event.player)?.name,
    tick: event.tick,
    station: event.station,
  }));
  const requests = (state?.requests || []).map((event) => ({
    kind: "request",
    id: `request:${event.id}`,
    ...event,
  }));
  const dialogues = (state?.publicDialogues || []).map((event) => ({
    kind: "dialogue",
    ...event,
  }));
  return [...arrivals, ...requests, ...dialogues]
    .sort((a, b) => a.tick - b.tick || String(a.id).localeCompare(String(b.id)))
    .slice(-limit)
    .reverse();
}
function activityFeed(limit = 8) {
  const entries = activityEntries(limit);
  if (!entries.length)
    return '<p class="activity-empty">等待第一条公开记录。</p>';
  return `<div class="activity-list" aria-live="polite">${entries
    .map((event) => {
      if (event.kind === "dialogue") return dialogueCard(event);
      if (event.kind === "request") {
        const status =
          {
            pending: "等待回应",
            accepted: "接受留站",
            declined: "拒绝留站",
            expired: "未回应，主持人安排留站",
          }[event.status] || event.status;
        return `<div class="activity-item request-item"><span class="activity-badge">问</span><div><b>${event.requesterSeat}号 → ${event.targetSeat}号</b><small>${rail(event.tick)} · 请求留站</small><p>${esc(status)}</p></div></div>`;
      }
      return `<div class="activity-item arrival-item"><span class="activity-badge">${event.seat}号</span><div><b>${esc(event.name || "旅人")}</b><small>${rail(event.tick)} · 到站记录</small><p>到达 ${esc(station(event.station))}</p></div></div>`;
    })
    .join("")}</div>`;
}
function dialogueCard(event) {
  const player = state.players.find((p) => p.id === event.player);
  return `<article class="dialogue-card" data-dialogue="${event.id}"><div class="dialogue-heading">${publicAvatar(player)}<b>旅人${player?.seat || ""} · ${esc(player?.name || "旅人")}</b></div><small>${rail(event.tick)} · 系统剧情</small><p>「${esc(event.text)}」</p>${event.source ? `<p class="dialogue-source"><a href="${esc(event.sourceUrl)}" target="_blank" rel="noopener">${esc(event.source)}</a><br>${esc(event.mapping)}<br>官作日文转录／社区译文，地点绑定为本游戏设计。</p>` : ""}</article>`;
}
function dialogueHistory() {
  const events = state.publicDialogues || [];
  return events.length
    ? `<details id="dialogue-history" class="dialogue-history"><summary>旅途回声 · ${events.length}条系统剧情</summary>${events.slice().reverse().map(dialogueCard).join("")}</details>`
    : "";
}
function publicNotebook() {
  return `${dialogueHistory()}<details id="phone-public" class="phone-public"><summary>查看公开记录</summary>${activityFeed(8)}<p class="activity-note">说法可以骗人；到站记录不代表实时位置。旅途回声由系统触发，不是玩家主动发言，也不证明梅莉未被侵蚀。</p></details>`;
}
function applyTheme(tick = state?.tick || 0) {
  const minute = (960 + tick * 15) % 1440;
  document.documentElement.dataset.theme =
    minute >= 360 && minute < 1080 ? "day" : "night";
}
function initHome() {
  app.innerHTML = `<main class="title-scene title-home"><img class="title-art" src="${esc(assets.scenes.title)}" alt="莲子与梅莉坐在夕阳下的列车车厢中"><div class="title-content"><div class="eyebrow">HIFUU RAILWAY JOURNEY</div><div class="title-logo">${emblem}<h1><span>秘封</span>铁道纪行</h1></div><p class="lead">同一张时刻表。<br>不一定是同一个约定。</p><nav class="title-menu" aria-label="游戏入口"><a href="/host"><small>01</small><span>开启旅程<em>主持端</em></span><b>→</b></a><a href="/join"><small>02</small><span>持票入场<em>玩家端</em></span><b>→</b></a><a href="/rules.html" target="_blank" rel="noopener"><small>03</small><span>玩法说明<em>旅行须知</em></span><b>↗</b></a></nav></div></main>`;
}
function joinForm() {
  app.innerHTML = `<main class="entry-scene"><a class="entry-brand" href="/">秘封铁道纪行</a><section class="join-page paper-ticket"><div class="eyebrow">BOARDING PASS / 私人车票</div><h1>登上这趟列车</h1><p class="muted">扫描大屏幕上的二维码或输入房间码加入游戏。</p><form id="join-form" novalidate><label>房间码<input name="room" value="${esc(room)}" maxlength="5" placeholder="ABCDE" required autocomplete="off" autocapitalize="characters"></label><label>你的旅人昵称<input name="name" maxlength="12" placeholder="最多 12 个字" required autocomplete="nickname"></label><button class="primary" type="submit">领取车票 →</button></form><p class="entry-note">你可以说谎，但不能展示手机。<br>请让这张车票只留在你手里。</p></section></main>`;
}
function hostEntry() {
  app.innerHTML = `<main class="title-scene host-entry"><section class="host-entry-ticket paper-ticket"><div class="eyebrow">DEPARTURE / 主持检票单</div>${emblem}<h1>让旅程从这里开始</h1><p class="lead">全国百站，三位旅人。<br>下一站，遇见谁？</p><button class="primary" data-action="create">创建房间</button><p class="muted">玩家通过主持人提供的网址加入。<br>主持端请通过 localhost 打开，保持此页面在前台。</p><a href="/">← 返回入口</a></section></main>`;
}
function atlasFurniture() {
  return `<header class="atlas-heading"><div class="atlas-title"><span class="atlas-kicker">秘封倶楽部 / TRAVEL ATLAS</span><h1>秘封铁道纪行<span>轨道线网图</span></h1><p>同一张时刻表，不一定是同一个约定。</p></div><div class="atlas-edition">${emblem}</div></header><figure class="atlas-postcard" aria-label="随公共时钟切换的日夜窗景"><div class="atlas-window"></div><figcaption><span>車窓の向こう</span><small>窗外，是约定尚未抵达的地方。</small></figcaption></figure><div id="atlas-status" class="atlas-alert" role="status"></div>`;
}
function prepareAtlas(svg) {
  // Replace only the authored sheet's title and legend; retain station/edge groups.
  for (const node of [...svg.children]) {
    const tag = node.tagName.toLowerCase(),
      x = Number(node.getAttribute("x")),
      y = Number(node.getAttribute("y"));
    const heading =
      (tag === "text" && y <= 223) ||
      (tag === "rect" && ((x === 60 && y === 45) || (x === 86 && y === 76)));
    const legend =
      (tag === "rect" && x === 3050 && y === 1848) ||
      (tag === "text" && [3050, 3080].includes(x) && y >= 1848);
    if (heading || legend) node.remove();
  }
}
function initBoard() {
  boardObserver?.disconnect();
  replayView = false;
  app.innerHTML = `<div class="board-shell"><header class="board-toolbar"><a href="/" class="brand">秘封铁道纪行</a><div class="toolbar-right"><span id="connection" class="connection">● 已连接</span><a class="help-link" href="/rules.html" target="_blank" rel="noopener">玩法</a>${isHost ? `<button data-action="sound">到站音：${sound ? "开" : "关"}</button><button data-action="screen">观众屏</button>` : ""}<button data-action="fullscreen">全屏</button></div></header><section class="host-control-strip" aria-label="游戏控制"><div id="host-phase-controls"></div><p id="host-start-status" role="status"></p></section><main class="map-frame"><div id="network">${mapText}</div><aside class="board-panel" id="board-panel"></aside></main><main id="scene-stage" hidden></main></div>`;
  const shell = document.querySelector(".board-shell");
  const resizeBoard = () =>
    shell.style.setProperty(
      "--board-chrome-height",
      `${document.querySelector(".board-toolbar").offsetHeight + document.querySelector(".host-control-strip").offsetHeight}px`,
    );
  boardObserver = new ResizeObserver(resizeBoard);
  boardObserver.observe(document.querySelector(".board-toolbar"));
  boardObserver.observe(document.querySelector(".host-control-strip"));
  const svg = document.querySelector("#network svg");
  prepareAtlas(svg);
  const frame = document.querySelector(".map-frame");
  frame.classList.add("atlas");
  frame.insertAdjacentHTML(
    "beforeend",
    atlasFurniture() +
      '<aside id="board-dialogue" class="board-dialogue" aria-live="polite" hidden></aside>',
  );
  frame.insertAdjacentHTML(
    "beforeend",
    '<p id="dialogue-map-legend" hidden><i aria-hidden="true"></i><span>回声候选地点 · 经过有概率触发（东京需乘卯酉特急抵达）</span></p>',
  );
  svg
    .querySelector("g[data-name]")
    ?.insertAdjacentHTML("beforebegin", '<g id="dialogue-map-markers"></g>');
  svg.insertAdjacentHTML(
    "beforeend",
    '<g id="replay-trails"></g><g id="live-markers"></g>',
  );
}
function techBanner() {
  if (!state.technical) return "";
  return `<div class="notice danger">${state.technical.countdown !== null ? "连接已恢复，即将继续" : "连接中断，游戏时间已暂停"}<small>请保持大屏和三部手机在前台。</small></div>`;
}
function joinCode() {
  const joinAddress = state.joinAddress || address;
  return `<section class="rejoin-card"><div class="join-qr">${config.qrAvailable ? `<img alt="扫码返回当前房间" src="/api/qr?room=${room}&address=${encodeURIComponent(joinAddress)}">` : "<span>使用右侧网址加入</span>"}</div><div><b>扫码返回本局</b><p>房间码 ${esc(room)} · 请用原手机浏览器恢复座位</p><div class="join-address">${esc(joinAddress)}/join?room=${esc(room)}</div></div></section>`;
}
function continueStatus() {
  if (state.phase === "briefing") return "首程不限时，等待主持人发车";
  if (state.decisionPaused) return "主持人已暂停，等待手动继续";
  if (state.decisionRemaining !== null) return decisionClock();
  return "全部确认后3秒自动继续，主持人可暂停";
}
function timerStrip() {
  const paused = state.phase === "decision" || state.phase === "briefing";
  const status = state.technical
    ? "连接恢复中，游戏时间暂停"
    : state.phase === "briefing"
      ? "首轮选路，等待发车"
      : paused
        ? "等待选路，游戏时间暂停"
        : state.phase === "ended"
          ? "旅程已结束"
          : "列车运行中";
  const night = document.documentElement.dataset.theme === "night";
  const decisionSummary = paused
    ? `已确认 ${state.readyCount} 人${state.pendingPlayers?.length ? ` · 待确认 ${state.pendingPlayers.length} 人` : ""} · ${continueStatus()}`
    : "";
  return `<div class="timer-strip"><div class="clock-heading"><span class="eyebrow">铁路时刻 / RAILWAY TIME</span><span class="period-seal">${night ? "夜行" : "日行"}</span></div><div class="game-clock"><strong data-clock="rail">${rail((state.railMinutes ?? state.tick * 15) / 15)}</strong><span class="clock-unit">游戏时间</span></div><div class="rail-time"><span>${status}</span>${paused ? `<span>${decisionSummary}</span>` : ""}</div></div>`;
}

function outcomeKey() {
  if (state.result.outcome !== "yukari") return state.result.outcome;
  return state.result.reason.startsWith("她记得")
    ? "yukari_corrupted"
    : "yukari_direct";
}
function ending() {
  const titles = gameText.endingTitles;
  const cg = assets.endings[outcomeKey()];
  return `<section class="ending ${cg ? "" : "ending-typeset"}" data-outcome="${esc(outcomeKey())}"><div class="ending-art"><div class="ending-placeholder" aria-label="结局插画预留">${emblem}<span>${esc(titles[state.result.outcome])}</span><small>${state.result.station ? esc(station(state.result.station)) : "HIFUU RAILWAY"} · ${rail(state.result.tick)}</small></div>${cg ? `<img class="ending-cg" src="${esc(cg)}" alt="${esc(titles[state.result.outcome])}结局画面">` : ""}</div><div class="ending-story"><div class="eyebrow">END OF THE JOURNEY / 终着</div><h2>${esc(titles[state.result.outcome])}</h2><p>${esc(state.result.reason)}</p>${state.result.station ? `<p class="ending-place">${esc(station(state.result.station))} · ${rail(state.result.tick)}</p>` : ""}<div class="reveal-list">${state.players.map((p) => `<div>${roleBadge(p.role, p.seat)}<span>${esc(p.name)}<b>${esc(names[p.role] || "尚未分配")}${p.corrupted ? "（已侵蚀）" : ""}</b></span></div>`).join("")}</div></section>`;
}
function renderBoard() {
  preserveView(app, renderBoardContent);
}
function renderBoardContent() {
  const lobby = state.phase === "lobby";
  const atlasStatus = document.querySelector("#atlas-status");
  const warning = techBanner() + (state.technical ? joinCode() : "");
  if (atlasStatus.innerHTML !== warning) atlasStatus.innerHTML = warning;
  if (state.phase !== "ended") {
    replayView = false;
    replayTick = null;
  }
  const fullScene = lobby || (state.phase === "ended" && !replayView);
  document.querySelector(".map-frame").hidden = fullScene;
  const stage = document.querySelector("#scene-stage");
  stage.hidden = !fullScene;
  stage.className = lobby
    ? "lobby-stage title-scene title-home"
    : "ending-stage";
  if (!fullScene) stage.replaceChildren();
  const panel = fullScene ? stage : document.querySelector("#board-panel");
  const controls = document.querySelector("#host-phase-controls");
  if (controls) {
    let html = "";
    if (isHost && state.phase === "briefing")
      html += `<button data-action="launch" ${state.readyCount < 3 ? "disabled" : ""}>正式发车开启旅程</button><button data-action="launch-force" ${state.readyCount < 3 ? "" : "disabled"}>未确认者留站并发车</button>`;
    if (isHost && state.phase === "decision") {
      const pending = (state.pendingPlayers || []).length;
      html += `<button data-action="pause" aria-pressed="${state.decisionPaused}">${state.decisionPaused ? "恢复自动继续" : "暂停自动继续"}</button><button class="primary" data-action="continue" ${pending || state.technical ? "disabled" : ""}>立即继续行车</button><button data-action="continue-force" ${pending && !state.technical ? "" : "disabled"}>未确认者留站并继续</button>`;
    }
    if (isHost && !["lobby", "ended"].includes(state.phase))
      html += '<button data-action="abort">中止本局</button>';
    controls.innerHTML = html;
  }
  if (controls && isHost && lobby)
    controls.innerHTML = `<button class="primary start-game" data-action="setup" aria-describedby="host-start-status" ${state.players.length !== 3 || state.players.some((p) => !p.ready || !p.connected) ? "disabled" : ""}>开始游戏并分配身份 →</button>`;
  if (
    controls &&
    isHost &&
    state.phase !== "ended" &&
    typeof state.dialogueEnabled === "boolean"
  ) {
    controls.insertAdjacentHTML(
      "beforeend",
      `<button data-action="dialogue" aria-pressed="${state.dialogueEnabled}" title="第二日00:00起，经过相关站点即可概率触发角色对白，无需停车">旅途回声：${state.dialogueEnabled ? "开" : "关"} · 第二日00:00起</button>`,
    );
  }
  if (controls && state.phase === "ended")
    controls.innerHTML = `<button data-action="${replayView ? "ending" : "replay"}">${replayView ? "← 返回结局" : "▶ 行程回放"}</button>${replayView ? '<button data-action="replay">重新播放</button>' : ""}${isHost ? '<button data-action="export">导出本局</button><button data-action="rematch">同组三人再来一局</button><button data-action="new-room">创建新房间</button>' : ""}`;
  const startStatus = document.querySelector("#host-start-status");
  if (startStatus) {
    const ready = state.players.filter((p) => p.ready && p.connected).length;
    const disconnected = state.players.filter((p) => !p.connected).length;
    const pendingSeats = (state.pendingPlayers || [])
      .map((id) => state.players.find((p) => p.id === id)?.seat + "号")
      .filter(Boolean)
      .join("、");
    const requiredCount =
      state.readyCount + (state.pendingPlayers || []).length;
    startStatus.textContent =
      isHost && lobby
        ? `已加入 ${state.players.length}/3 人 · 已准备 ${ready}/3 人。${state.players.length < 3 ? `还需 ${3 - state.players.length} 人扫码加入。` : disconnected ? "请等待断线玩家重新连接。" : ready < 3 ? "请让玩家在手机上点击“准备出发”。" : "三人已准备，可以开始分配身份。"}`
        : isHost && state.phase === "briefing"
          ? `首程不限时 · 已确认 ${state.readyCount}/3 人；全部确认后正式发车，或安排未确认者留站并发车。`
          : isHost && state.phase === "decision"
            ? `讨论中 · 已确认 ${state.readyCount}/${requiredCount} 人${pendingSeats ? ` · 待确认：${pendingSeats}` : state.decisionPaused ? " · 主持人已暂停。" : " · 全部确认，3秒后自动继续。"}`
            : "";
    document.querySelector(".host-control-strip").hidden =
      !controls?.children.length && !startStatus.textContent;
    controls
      ?.querySelector('[data-action="launch"]')
      ?.classList.add("primary", "start-game");
  }
  if (lobby) {
    panel.innerHTML = `<img class="title-art" src="${esc(assets.scenes.title)}" alt="莲子与梅莉坐在夕阳下的列车车厢中"><div class="title-content"><div class="eyebrow">HIFUU RAILWAY JOURNEY</div><div class="title-logo">${emblem}<h1><span>秘封</span>铁道纪行</h1></div><section class="boarding-ticket paper-ticket"><div class="ticket-heading"><span class="eyebrow">BOARDING / 正在检票</span><span>三人席 ${state.players.length}/3</span></div><h2>候车大厅</h2><div class="boarding"><div class="join-qr">${config.qrAvailable ? `<img alt="扫码加入房间" src="/api/qr?room=${room}&address=${encodeURIComponent(address)}">` : "<span>使用下方网址加入</span>"}</div><div><span class="muted">房间码</span><strong class="room-code">${room}</strong><span class="muted">手机扫码或输入房间码加入</span></div></div><div class="join-address">${esc(address)}/join</div><div class="seat-list">${[
      0, 1, 2,
    ]
      .map((i) => {
        const p = state.players.find((p) => p.seat === i + 1);
        return `<div>${publicAvatar()}<span class="seat-number">${i + 1}</span><span class="seat-name">${p ? esc(p.name) : "等待旅人"}</span><span class="seat-status ${p?.ready && p?.connected ? "ready" : ""}">${p ? (!p.connected ? "已断线" : p.ready ? "已准备" : "阅读须知中") : "空席"}</span>${p && isHost ? `<button class="text-button" data-action="kick" data-player="${p.id}" aria-label="移除 ${esc(p.name)}">×</button>` : ""}</div>`;
      })
      .join(
        "",
      )}</div><p class="lobby-note">可以自由交流与说谎，请勿展示手机。<br>大屏记录最近到站，约定由你们自己保管。</p><p class="lobby-note">观众屏会自动跟随主持人切换到新房间。</p>${isHost ? `<details id="host-settings" class="host-settings"><summary>开局设置与网络地址</summary><label>手机加入地址<select id="address-select">${config.addresses.map((a) => `<option ${a === address ? "selected" : ""}>${a}</option>`).join("")}</select></label><label>起点组合<select id="template-select"><option value="random">全国分散起点（最短行车至少5小时）</option>${config.templates.map((t) => `<option value="${t.template_id}" ${template === t.template_id ? "selected" : ""}>${t.station_a} / ${t.station_b} / ${t.station_c} （最短间距${duration(t.min_distance_ticks * 15)}）</option>`).join("")}</select></label><p>${config.publicAddress ? "已优先使用配置的公网地址。" : "优先使用系统默认网络；有手机成功加入后会记住该地址。"}${state.joinAddress ? `上次手机成功加入：${esc(state.joinAddress)}。` : "尚未有手机加入。"}请先用一部手机扫码确认能打开，无法连接时切换地址。127.0.0.1 仅供本机测试。</p></details>` : ""}</section></div>`;
  } else if (state.phase === "ended") {
    panel.innerHTML = replayView
      ? `<div class="eyebrow">TRAVEL RECORD / 本局实际已出发行程</div><h2>沿着约定，重走一遍。</h2><p id="replay-caption" class="muted">行程回放 ${rail(replayTick || 0)}</p><div class="replay-legend">${state.players.map((p, i) => `<p><i style="background:${["#c48122", "#9754a9", "#215669"][i]}"></i>${p.seat}号 / ${esc(p.name)} / ${esc(names[p.role] || "尚未分配")}</p>`).join("")}</div><p class="muted">20 秒回顾旅行路线</p>`
      : ending();
  } else {
    panel.innerHTML =
      timerStrip() +
      `<div class="section-title"><h3>最近到站</h3><span>记录 ≠ 当前位置</span></div><div class="arrival-list">${state.players.map((p) => `<div>${publicAvatar(p)}<span class="seat-number">${p.seat}</span><span class="traveler"><span class="traveler-role">${esc(names[p.role] || "身份未公开")}</span><span class="traveler-name">${esc(p.name)}</span></span><span class="station-arrival"><b>${esc(station(p.lastArrival.station))}</b><small>${rail(p.lastArrival.tick)}</small></span></div>`).join("")}</div>`;
  }
  const echo = document.querySelector("#board-dialogue");
  const latest = (state.publicDialogues || []).at(-1);
  echo.hidden = !latest || state.phase === "ended";
  echo.innerHTML = echo.hidden
    ? ""
    : `<span class="eyebrow">旅途回声 / 系统剧情</span>${dialogueCard(latest)}`;
  document.querySelector(".atlas-postcard").hidden = !echo.hidden;
  if (state.phase === "ended")
    panel.insertAdjacentHTML("beforeend", dialogueHistory());
  renderMarkers(replayView ? replayTick : null);
}
function goalKey(me) {
  return me.role === "maribel" && me.corrupted ? "maribel_corrupted" : me.role;
}
function goal(me) {
  return esc(gameText.goals[goalKey(me)]);
}
function renderPhone() {
  const view =
    state.phase === "lobby" || state.phase === "ended"
      ? state.phase
      : state.me?.submitted
        ? "submitted"
        : state.me?.motion
          ? "moving"
          : `station:${state.me?.station}`;
  const same = phoneView === `${room}:${view}`;
  preserveView(app, renderPhoneContent, same);
  phoneView = `${room}:${view}`;
  const offer = state.me?.hand?.[0]?.id;
  if (view.startsWith("station:") && (!same || offer !== axisOffer)) {
    const axis = document.querySelector(".station-axis"),
      current = axis?.querySelector(".current");
    if (axis && current) {
      const top = Math.max(
        0,
        Math.min(
          axis.scrollHeight - axis.clientHeight,
          current.offsetTop - axis.clientHeight / 2 + current.offsetHeight / 2,
        ),
      );
      const stop = [...axis.children].filter((e) => e.offsetTop <= top).at(-1);
      axis.scrollTop = stop?.offsetTop || 0;
      const ticket = document
        .querySelector(".destination-actions")
        .getBoundingClientRect();
      const box = axis.getBoundingClientRect();
      const bottom = Math.min(innerHeight, ticket.top) - 12;
      if (box.bottom > bottom) window.scrollBy(0, box.bottom - bottom);
      if (axis.getBoundingClientRect().top < 8)
        window.scrollBy(0, axis.getBoundingClientRect().top - 8);
    }
  }
  updateAxisHint();
  axisOffer = offer;
  setConnection(connectionOK);
}
function renderPhoneContent() {
  const me = state.me;
  if (!me) return;
  if (state.phase === "lobby") {
    app.innerHTML = `<main class="phone-shell lobby-phone"><div class="eyebrow">HIFUU / BOARDING PASS</div><div class="waiting-identity">${roleBadge(null, "秘")}<div class="big-seat">${me.seat}</div></div><h1>${esc(me.name)}，欢迎乘车。</h1><p class="muted">房间 ${room} 已保留你的座位</p><div class="instruction"><h3>出发前，请记住</h3><p><b>先当面说理由，再在手机上选路线。</b></p><p>路线是私密的；到站记录是公开事实。</p><p>有人到站后铁路暂停；全部确认后3秒自动继续，主持人可暂停讨论。</p><p>被请求留站时，先口头回答，再点接受或拒绝。</p><p><b>不要展示手机。</b>可以说谎，也可以质疑。</p></div><button class="primary" data-action="ready" ${state.players.find((p) => p.id === me.id)?.ready ? "disabled" : ""}>${state.players.find((p) => p.id === me.id)?.ready ? "已准备，等待主持人" : "已了解，准备出发"}</button><p class="muted center">保持此页在前台。刷新可回到原座位。</p><div id="connection" class="connection">● 已连接</div></main>`;
    return;
  }
  if (state.phase === "ended") {
    app.innerHTML = `<main class="phone-shell">${ending()}${dialogueHistory()}<div class="notice">等待主持人开启下一局。<br>本局身份和侵蚀已经公开。</div></main>`;
    return;
  }
  if (me.submitted) {
    renderSubmittedPhone(me);
    return;
  }
  if (me.motion) {
    renderMovingPhone(me);
    return;
  }
  renderStationPhone(me);
}
function decisionClock() {
  return state.decisionRemaining === null
    ? ""
    : `<span class="decision-clock" data-clock="decision">${Math.ceil(state.decisionRemaining / 1000)}秒后自动继续${state.technical ? "（连接暂停）" : ""}</span>`;
}
function renderSubmittedPhone(me) {
  const waiting = (state.pendingPlayers || [])
    .map((id) => state.players.find((p) => p.id === id)?.seat + "号")
    .join("、");
  app.innerHTML = `<main class="phone-shell submitted-screen"><header class="phone-header"><span>秘封铁道纪行</span><span>${me.seat}号</span></header><div class="private-label">仅你可见 / 请勿向他人展示手机 <span id="connection">● 已连接</span></div>${techBanner()}
    <section class="submitted-ticket paper-ticket" role="status"><div class="eyebrow">${me.autoWait ? "主持人已安排留站" : "车票已确认"}</div><h1>${me.autoWait ? "已自动留站" : "已提交"}</h1><h2>${esc(station(me.station))}${me.chosenDestination ? " → " + esc(station(me.chosenDestination)) : " · 留站等候30分钟"}</h2>
    <p>${waiting ? "等待 " + waiting + " 玩家确认。" : state.phase === "briefing" ? "三人已提交，等待主持人正式发车。" : state.decisionPaused ? "主持人已暂停，等待继续。" : "全部确认，即将自动继续。"}</p><button disabled>${me.chosenDestination ? "行程已锁定" : "已安排留站等候"}</button><p class="muted">${continueStatus()}</p></section>${requestPanel(me)}${privateNotebook(me)}${publicNotebook()}</main>`;
}
function decisionNotice(me) {
  if (!me.otherPlayersDeciding) return "";
  const waiting = (state.pendingPlayers || [])
    .map((id) => state.players.find((p) => p.id === id)?.seat + "号")
    .filter(Boolean)
    .join("、");
  return `<div class="decision-notice" role="status"><b>${waiting ? `讨论中 · 等待 ${waiting} 确认` : "已全部确认"}</b><span>${continueStatus()}</span></div>`;
}
function requestPanel(me) {
  if (state.phase !== "decision") return "";
  const request = state.request;
  if (request?.status === "pending" && request.target === me.id) {
    return `<section class="request-panel request-response" aria-label="留站请求"><div class="section-title"><h3>${request.requesterSeat}号请求你留站</h3><span>请先口头回答</span></div><p>接受将留站30分钟并锁定本轮行动；拒绝后仍可选择路线。</p><div class="request-actions"><button class="primary" data-action="respond-request" data-accept="true">接受 · 留站30分钟</button><button data-action="respond-request" data-accept="false">拒绝 · 保留路线</button></div></section>`;
  }
  if (!me.requestAvailable)
    return request?.requester === me.id
      ? '<div class="request-status">本局请求机会已使用。请继续讨论并确认路线。</div>'
      : "";
  const targets = (state.pendingPlayers || [])
    .filter((id) => id !== me.id)
    .map((id) => state.players.find((p) => p.id === id))
    .filter(Boolean);
  if (!targets.length) return "";
  return `<section class="request-panel" aria-label="请求留站"><div class="section-title"><h3>请求一名玩家留站</h3><span>本局一次</span></div><p>先当面说明理由，再选择对象。对方接受后会实际留站30分钟。</p><div class="request-targets">${targets.map((p) => `<button data-action="request-wait" data-target="${p.id}">${p.seat}号 · ${esc(p.name)}<small>请求留站</small></button>`).join("")}</div></section>`;
}
function privateNotebook(me) {
  return `<details id="private-notebook" class="private-notebook"><summary>旅途札记 ${me.messages?.length || 0} 条私密讯息</summary><p class="notebook-goal">当前目标：${goal(me)}</p>${me.protectionUntil > state.tick && !me.corrupted ? "<p>现世庇护 · 次日06:00前不会被侵蚀</p>" : ""}${
    (me.messages || [])
      .slice()
      .reverse()
      .map(
        (m) =>
          `<article><small>${rail(m.tick)} （仅你可见）</small><p>${esc(m.text)}</p></article>`,
      )
      .join("") || "<p>尚无新的旅途讯息。</p>"
  }</details>`;
}
function renderMovingPhone(me) {
  const m = me.motion;
  const moving = connectionOK && !m.paused && !m.held && !m.station;
  app.innerHTML = `<main class="phone-shell moving-screen ${moving ? "is-traveling" : "is-stopped"}" style="--line-color:${esc(m.color)}">
    <header class="phone-header"><span>秘封铁道纪行</span><span>${me.seat}号</span></header><div class="private-label">仅你可见 / 请勿向他人展示手机 <span id="connection">● 已连接</span></div>
    ${decisionNotice(me)}${techBanner()}${requestPanel(me)}
    <div class="moving-clock"><span>游戏时间</span><b data-clock="rail">${rail((state.railMinutes ?? state.tick * 15) / 15)}</b></div>
    <div class="train-window" aria-label="列车窗外氛围插画"><div class="window-landscape"></div><span class="window-caption">车窗之外 : ${document.documentElement.dataset.theme === "night" ? "夜行" : "日行"}</span></div>
    <section class="journey-countdown"><span class="eyebrow">距离 ${esc(station(m.destination))}</span><strong data-clock="journey">${journeyClock(m.remainingRailMinutes)}</strong><p>游戏时间： 小时:分钟${m.paused ? " （已暂停）" : ""}</p></section>
    ${me.trip ? `<section class="stop-request" aria-live="polite"><button data-action="stop" ${me.canStop ? "" : "disabled"}>${m.stopRequested ? "已申请下一站下车" : "下一站下车，重新选路"}</button><p>${m.stopRequested ? `将在 ${esc(station(m.target))} 下车，届时重新抽线并进入选路。` : `下一站：${esc(station(m.target))}。申请后完成当前区间，到站再选路，不消耗改签次数。`}</p></section>` : ""}
    <section class="live-position"><div class="section-title"><h2>实时位置</h2><span>仅你可见</span></div><p class="motion-line">${esc(m.line)}</p><h3>${m.station ? esc(station(m.station)) : `${esc(station(m.source))} → ${esc(station(m.target))}`}</h3>
    <div class="position-track"><span class="position-dot" style="left:${m.progress * 100}%"></span></div><div class="position-endpoints"><span>${esc(station(m.source))}</span><span>${esc(station(m.target))}</span></div>
    <p class="position-status">${m.held ? "行程暂停，当前区间进度保持不变" : m.station ? (me.queued ? "等待发车" : "中途停站中") : `当前区间已行进 ${Math.floor(m.progress * 100)}%`}</p></section>
    ${me.corrupted ? '<p class="current-private-state">目标已改变：协助紫，诱导莲子会合。</p>' : ""}${privateNotebook(me)}${publicNotebook()}
  </main>`;
}
function renderStationPhone(me) {
  const offer = me.hand[0];
  if (selectedCard !== offer?.id) {
    selectedCard = offer?.id;
    selectedDestination = me.chosenDestination || null;
  }
  if (
    !offer?.destinations?.some(
      (d) => !d.current && d.station === selectedDestination,
    )
  )
    selectedDestination = null;
  const selected = offer?.destinations?.find(
    (d) => d.station === selectedDestination,
  );
  const options = (me.availableLines || []).filter(
    (l) => l.id !== offer?.lineId,
  );
  app.innerHTML = `<main class="phone-shell station-screen"><header class="phone-header"><span>秘封铁道纪行</span><span>${room} ${me.seat}号</span></header><div class="private-label">仅你可见 / 请勿向他人展示手机 <span id="connection">● 已连接</span></div>
    <section class="identity">${roleBadge(me.role, "秘")}<div><h1>${esc(names[me.role])}</h1><p class="goal">${esc(gameText.shortGoals[goalKey(me)])}</p></div></section><details id="goal-details" class="goal-details"><summary>身份与完整目标</summary><p>${goal(me)}</p></details>
    <section class="location"><div class="location-heading"><span class="eyebrow">当前车站 / NOW AT</span><span data-clock="rail">${rail((state.railMinutes ?? state.tick * 15) / 15)}</span></div><h2>${esc(station(me.station))}</h2><span class="station-stamp">${state.phase === "briefing" ? "首程选路" : me.canAct ? "等待选路" : "留站等候"}</span>${me.waitUntil > state.tick ? `<p>留站等候中，${rail(me.waitUntil)} 后重新抽线，可选择下一程</p>` : ""}${me.holdUntil > state.tick ? `<div class="status-tag">行程暂停，至 ${rail(me.holdUntil)}</div>` : ""}${me.protectionUntil > state.tick && !me.corrupted ? '<div class="status-tag">现世庇护 · 次日06:00前不会被侵蚀</div>' : ""}</section>${decisionNotice(me)}${techBanner()}${requestPanel(me)}
    <section class="drawn-line" style="--line-color:${esc(offer?.color || "#315c6e")}"><div class="line-heading"><span class="line-code">${esc(offer?.lineId || "线路")}</span><div><span class="eyebrow">本次抽到的线路</span><h2>${esc(offer?.name)}</h2></div><button class="change-shortcut" data-action="open-change">改签：${me.baseChanges + me.bonusChanges}</button></div><p class="line-guidance">上下滑动查看全线，本站两侧均可选。预计时刻含沿途每站15分钟停靠。</p>
    <div class="station-axis-wrap"><div class="station-axis" role="group" aria-label="沿线站点与预计到达时刻">${(offer?.destinations || []).map((d) => `<button class="axis-stop ${d.current ? "current" : ""} ${d.station === selectedDestination ? "selected" : ""}" data-action="select-station" data-card="${esc(offer.id)}" data-destination="${d.station}" ${d.current || !me.canAct ? "disabled" : ""} aria-pressed="${d.station === selectedDestination}"><span class="axis-dot"></span><span class="stop-name">${esc(station(d.station))}${d.current ? "<small>本站 · 出发</small>" : ""}</span><span class="stop-time"><span data-arrival="${d.station}">${d.current ? "现在" : rail(d.arrivalTick)}</span><small>${d.current ? "" : `行程 ${duration(d.ticks * 15)}`}</small></span></button>`).join("")}</div><button class="axis-scroll-hint" data-action="scroll-stations" aria-label="向下滚动查看更多站点" hidden><span>下滑查看更多站点</span><b aria-hidden="true">↓</b></button></div>
    <p class="line-estimate">到达时刻按当前可出发时间估算；途中事件可能延后抵达。</p></section>
    <div class="destination-actions"><div class="ticket-heading"><span>本次旅程 / TRAVEL TICKET</span><span>${me.seat}号 / 单程</span></div><div class="ticket-route"><b>${esc(station(me.station))}</b><span>→</span><b>${selected ? esc(station(selected.station)) : "选择目的地"}</b></div><p aria-live="polite">${decisionClock()} ${selected ? `已选 ${esc(station(selected.station))}，预计 <span data-arrival="${selected.station}">${rail(selected.arrivalTick)}</span> 到达` : "请在站点轴上选择目的地"}</p><div class="action-bar"><button class="primary" data-action="travel" ${!me.canAct || !selected ? "disabled" : ""}>确认此程 →</button><button data-action="wait" ${!me.canAct ? "disabled" : ""}>留站等候30分钟</button></div></div>
    ${me.submitted ? `<div class="notice">${me.autoWait ? "选路时间已到，已自动安排留站等候30分钟。" : "已收到你的选择。"}${state.phase === "briefing" ? "等待主持人正式发车。" : "等待主持人确认讨论结束后继续。"}</div>` : ""}
    <details id="change-panel" class="change-panel"><summary>改签线路：基础 ${me.baseChanges} / 规划修订 ${me.bonusChanges}</summary><p>将抽到的线路更换为本站另一条线路，再选择沿线任意站点。确认更换时扣除1次，优先使用规划修订。</p><label>改为<select id="change-edge">${options.map((l) => `<option value="${l.edge}">${esc(l.name)} · ${esc(station(l.start))} ↔ ${esc(station(l.end))} · 本站邻站：${(l.neighbors || []).map((id) => esc(station(id))).join(" / ")} (${esc(l.id)})</option>`).join("")}</select></label><p id="change-line-detail" class="change-line-detail"></p><button data-action="change" ${!options.length || !me.canChange || !(me.baseChanges + me.bonusChanges) ? "disabled" : ""}>确认改签（扣除1次）</button></details>
    ${privateNotebook(me)}${publicNotebook()}
    <footer>途中可申请下一站下车，到站后重新抽线选路；未申请则继续原行程</footer></main>`;
}

function renderDialogueMap() {
  const layer = document.querySelector("#dialogue-map-markers");
  if (!layer || !state) return;
  const settings = config.dialogueMap;
  const active = !!(
    settings &&
    state.dialogueEnabled &&
    state.tick >= settings.startTick &&
    ["running", "decision"].includes(state.phase)
  );
  document.querySelector("#dialogue-map-legend").hidden = !active;
  if (layer.dataset.active === String(active)) return;
  layer.dataset.active = String(active);
  layer.innerHTML = active
    ? settings.locations
        .map((location) => {
          const point = config.positions[location.station];
          if (!point) return "";
          const [x, y] = point;
          const label = `${station(location.station)} · 旅途回声候选地点 · ${location.expressOnly ? "需乘卯酉特急抵达" : "经过即可概率触发，无需停车"}`;
          return `<g class="dialogue-map-marker" data-station="${esc(location.station)}" role="img" aria-label="${esc(label)}"><title>${esc(label)}</title><circle class="echo-ring-base" cx="${x}" cy="${y}" r="22"/><circle class="echo-ring" cx="${x}" cy="${y}" r="22"/></g>`;
        })
        .join("")
    : "";
}
function updateAxisHint() {
  const axis = document.querySelector(".station-axis");
  const hint = document.querySelector(".axis-scroll-hint");
  if (axis && hint)
    hint.hidden = axis.scrollHeight - axis.clientHeight - axis.scrollTop <= 4;
  const select = document.querySelector("#change-edge"),
    detail = document.querySelector("#change-line-detail");
  if (select && detail)
    detail.textContent =
      select.selectedOptions[0]?.textContent || "本站暂无其他可改签线路";
}
document.addEventListener(
  "scroll",
  (e) => {
    if (e.target.matches?.(".station-axis")) updateAxisHint();
  },
  true,
);
window.addEventListener("resize", updateAxisHint);
document.addEventListener("change", (e) => {
  if (e.target.id === "change-edge") updateAxisHint();
});
function renderMarkers(tick = null) {
  const layer = document.querySelector("#live-markers");
  if (!layer || !state) return;
  const history =
    state.phase === "ended" && tick !== null
      ? state.replay.filter((e) => e.tick <= tick)
      : null;
  const groups = new Map();
  for (const player of state.players) {
    let stationId = player.lastArrival?.station;
    if (history)
      stationId = [...history]
        .reverse()
        .find(
          (event) =>
            event.player === player.id &&
            ["start", "arrival", "pass"].includes(event.type),
        )?.station;
    if (!stationId || !config.positions[stationId]) continue;
    if (!groups.has(stationId)) groups.set(stationId, []);
    groups.get(stationId).push(player);
  }
  layer.innerHTML = [...groups]
    .map(([stationId, players]) => {
      const [x, y] = config.positions[stationId],
        height = players.length * 62 + 6,
        top = y - height / 2;
      return `<g class="station-player-pill" data-station="${esc(stationId)}"><rect x="${x - 34}" y="${top}" width="68" height="${height}" rx="34"/>${players
        .sort((a, b) => a.seat - b.seat)
        .map((player, index) => {
          const cy = top + 34 + index * 62,
            avatar = assets.roles[publicRole(player)],
            clipId = `marker-avatar-${player.id}`;
          const label = `${player.seat}号 · ${names[player.role] || "身份未公开"} · ${player.name} · 最近到站：${station(stationId)}`;
          return `<g class="player-marker" data-player="${player.id}" role="img" aria-label="${esc(label)}"><title>${esc(label)}</title><defs><clipPath id="${clipId}"><circle cx="${x}" cy="${cy}" r="31"/></clipPath></defs><text x="${x}" y="${cy + 10}" text-anchor="middle" font-size="28" fill="currentColor">秘</text>${avatar ? `<image href="${esc(avatar)}" x="${x - 31}" y="${cy - 31}" width="62" height="62" preserveAspectRatio="xMidYMid meet" clip-path="url(#${clipId})"/>` : ""}</g>`;
        })
        .join("")}</g>`;
    })
    .join("");
  const trails = document.querySelector("#replay-trails");
  trails.replaceChildren();
  if (history)
    for (const e of history.filter((e) => e.type === "depart" && e.edge)) {
      const path = document.querySelector(
        `#network #${e.edge} path:last-child`,
      );
      if (!path) continue;
      const line = path.cloneNode();
      line.removeAttribute("id");
      line.setAttribute(
        "stroke",
        ["#c48122", "#9754a9", "#215669"][
          state.players.findIndex((p) => p.id === e.player)
        ],
      );
      line.setAttribute("stroke-width", "19");
      line.setAttribute("opacity", ".65");
      trails.append(line);
    }
}
function updateClocks() {
  if (!state) return;
  renderDialogueMap();
  document.querySelectorAll("[data-clock]").forEach((el) => {
    const type = el.dataset.clock;
    el.textContent =
      type === "decision"
        ? state.decisionRemaining > 0
          ? `${Math.ceil(state.decisionRemaining / 1000)}秒后自动继续${state.technical ? "（连接暂停）" : ""}`
          : "即将继续"
        : type === "journey"
          ? journeyClock(state.me?.motion?.remainingRailMinutes || 0)
          : rail((state.railMinutes ?? state.tick * 15) / 15);
  });
  const m = state.me?.motion;
  if (m) {
    const dot = document.querySelector(".position-dot");
    if (dot) dot.style.left = `${m.progress * 100}%`;
    const label = document.querySelector(".live-position h3");
    if (label)
      label.textContent = m.station
        ? station(m.station)
        : `${station(m.source)} → ${station(m.target)}`;
    const ends = document.querySelectorAll(".position-endpoints span");
    if (ends.length === 2) {
      ends[0].textContent = station(m.source);
      ends[1].textContent = station(m.target);
    }
    const status = document.querySelector(".position-status");
    if (status)
      status.textContent = m.held
        ? "行程暂停，当前区间进度保持不变"
        : m.station
          ? state.me.queued
            ? "等待发车"
            : "中途停站中"
          : `当前区间已行进 ${Math.floor(m.progress * 100)}%`;
  }
  for (const el of document.querySelectorAll("[data-arrival]")) {
    const d = state.me?.hand?.[0]?.destinations?.find(
      (d) => d.station === el.dataset.arrival,
    );
    if (d) el.textContent = d.current ? "现在" : rail(d.arrivalTick);
  }
  document
    .querySelectorAll(".period-seal")
    .forEach(
      (el) =>
        (el.textContent =
          document.documentElement.dataset.theme === "night" ? "夜行" : "日行"),
    );
  document
    .querySelectorAll(".window-caption")
    .forEach(
      (el) =>
        (el.textContent =
          "车窗之外 : " +
          (document.documentElement.dataset.theme === "night"
            ? "夜行"
            : "日行")),
    );
}
function structuralKey(s) {
  const me = s.me,
    m = me?.motion;
  // Progress and estimated times update in place; keep touch targets alive between ticks.
  const own = me
    ? [
        me.id,
        me.role,
        me.corrupted,
        m ? null : me.station,
        me.canAct,
        me.canChange,
        me.version,
        me.batch,
        me.submitted,
        me.autoWait,
        me.chosenDestination,
        me.waitUntil,
        me.holdUntil,
        me.protectionUntil > s.tick,
        me.baseChanges,
        me.bonusChanges,
        me.messages,
        me.availableLines,
        me.hand?.map((h) => h.id),
        me.requestAvailable,
        me.requestTarget,
        me.canStop,
        m
          ? [m.destination, m.target, m.line, m.held, m.paused, m.stopRequested]
          : null,
      ]
    : null;
  return JSON.stringify([
    s.phase,
    s.decisionPaused,
    s.decisionRemaining !== null,
    s.readyCount,
    s.autoWaitCount,
    s.pendingPlayers,
    s.requests,
    s.request,
    isPhone && m
      ? s.players.map((p) => [p.id, p.seat, p.name, p.role])
      : s.players,
    s.result,
    s.joinAddress,
    own,
    s.dialogueEnabled,
    s.publicDialogues,
    s.technical
      ? s.technical.countdown === null
        ? "offline"
        : "recovering"
      : null,
  ]);
}
function receive(next, force = false) {
  const arrivals = next.arrivals || [];
  if (!arrivalSoundInitialized) {
    for (const event of arrivals)
      seenArrivalKeys.add(`${event.player}:${event.station}:${event.tick}`);
    arrivalSoundInitialized = true;
  } else if (sound) {
    for (const event of arrivals) {
      const key = `${event.player}:${event.station}:${event.tick}`;
      if (seenArrivalKeys.has(key)) continue;
      beep(event.terminal ? 3 : 1);
      seenArrivalKeys.add(key);
    }
    if (seenArrivalKeys.size > 64)
      seenArrivalKeys = new Set([...seenArrivalKeys].slice(-32));
  }
  state = next;
  if (
    isHost &&
    !config.publicAddress &&
    state.joinAddress &&
    state.joinAddress !== learnedAddress &&
    config.addresses.includes(state.joinAddress)
  ) {
    learnedAddress = state.joinAddress;
    address = state.joinAddress;
    localStorage.setItem("hifuu-address", address);
  }
  applyTheme(replayTick ?? state.tick);
  const newest = state.me?.messages?.at(-1);
  if (newest && newest.id > lastMessageId) {
    lastMessageId = newest.id;
    if (state.me.motion) toast(newest.text);
  }
  sequence = Math.max(sequence, state.me?.lastSeq || 0);
  const key = structuralKey(state);
  if (force || key !== renderKey) {
    renderKey = key;
    if (isPhone) renderPhone();
    else renderBoard();
  }
  updateClocks();
  setConnection(true);
}
function setConnection(ok) {
  connectionOK = ok;
  document.querySelectorAll("#connection").forEach((el) => {
    el.textContent = ok ? "● 已连接" : "○ 正在重连…";
    el.classList.toggle("offline", !ok);
  });
  const m = state?.me?.motion,
    moving = document.querySelector(".moving-screen");
  if (moving) {
    const active = ok && m && !m.paused && !m.held && !m.station;
    moving.classList.toggle("is-traveling", !!active);
    moving.classList.toggle("is-stopped", !active);
  }
}
async function poll() {
  if (!room || (!credential && !isScreen) || polling || busy) return;
  polling = true;
  const requestedRoom = room;
  try {
    const next = await request(`/api/state?room=${requestedRoom}`);
    if (room !== requestedRoom) return;
    if (isScreen && next.nextRoom) {
      location.replace(`/screen?room=${encodeURIComponent(next.nextRoom)}`);
      return;
    }
    receive(next);
  } catch (e) {
    setConnection(false);
    if (e.status === 401 || e.status === 404) {
      toast(e.message);
      credential = "";
      if (isPhone) joinForm();
      else if (isHost) hostEntry();
    }
  } finally {
    polling = false;
  }
}
async function command(kind, extra = {}) {
  const endpoint = isHost ? "/api/host" : "/api/player";
  const fields =
    isPhone && kind !== "ready"
      ? { seq: ++sequence, version: state.me.version, batch: state.me.batch }
      : {};
  try {
    receive(await request(endpoint, { room, kind, ...fields, ...extra }), true);
  } catch (error) {
    if (error.status !== 409 || error.code !== "stale_action") throw error;
    // Refresh without replaying a choice against a different ticket or station.
    receive(await request(`/api/state?room=${room}`), true);
    if (kind === "stop") toast(error.message);
  }
}
function beep(times = 1) {
  try {
    audioCtx ??= new AudioContext();
    const count = Math.max(1, Math.min(3, times));
    for (let index = 0; index < count; index++) {
      const start = audioCtx.currentTime + index * 0.32;
      const o = audioCtx.createOscillator(),
        g = audioCtx.createGain();
      o.connect(g);
      g.connect(audioCtx.destination);
      o.frequency.value = index ? 760 : 660;
      g.gain.setValueAtTime(0.06, start);
      g.gain.exponentialRampToValueAtTime(0.001, start + 0.2);
      o.start(start);
      o.stop(start + 0.22);
    }
  } catch {}
}
document.addEventListener("submit", async (e) => {
  if (e.target.id !== "join-form") return;
  e.preventDefault();
  if (busy) return;
  busy = true;
  try {
    const form = new FormData(e.target);
    room = String(form.get("room")).trim().toUpperCase();
    if (!/^[A-HJ-NP-Z2-9]{5}$/.test(room))
      throw new Error(
        "房间码为5位大写字母或数字，例如 ABCDE；不含 I、O、0、1。",
      );
    if (!String(form.get("name") || "").trim())
      throw new Error("请填写旅人昵称（最多12个字）。");
    const prior = loadCredential(room);
    if (prior) {
      try {
        const restored = await request(
          `/api/state?room=${room}`,
          undefined,
          prior,
        );
        credential = prior;
        receive(restored, true);
        return;
      } catch (err) {
        if (![401, 404].includes(err.status)) throw err;
      }
    }
    const result = await request(
      "/api/join",
      { room, name: form.get("name") },
      "",
    );
    credential = result.token;
    saveCredential(room, credential);
    history.replaceState(null, "", `/join?room=${room}`);
    receive(await request(`/api/state?room=${room}`), true);
  } catch (err) {
    toast(err.message);
  } finally {
    busy = false;
  }
});
document.addEventListener("change", (e) => {
  if (e.target.id === "address-select") {
    address = e.target.value;
    localStorage.setItem("hifuu-address", address);
    renderBoard();
  }
  if (e.target.id === "template-select") template = e.target.value;
});
document.addEventListener(
  "error",
  (e) => {
    if (e.target.tagName === "IMG") {
      e.target.hidden = true;
      const parent = e.target.closest(".role-icon, .public-avatar");
      if (parent) parent.textContent = "秘";
    }
  },
  true,
);
document.addEventListener(
  "pointerdown",
  () => {
    if (sound) {
      try {
        audioCtx ??= new AudioContext();
        audioCtx.resume();
      } catch {}
    }
  },
  { once: true },
);
document.addEventListener("click", async (e) => {
  const button = e.target.closest("[data-action]");
  if (!button || button.disabled || busy) return;
  const kind = button.dataset.action;
  if (kind === "select-station") {
    selectedCard = button.dataset.card;
    selectedDestination = button.dataset.destination;
    renderPhone();
    updateClocks();
    return;
  }
  if (kind === "open-change") {
    const panel = document.querySelector("#change-panel");
    panel.open = true;
    panel.scrollIntoView({ block: "start" });
    panel.querySelector("summary").focus({ preventScroll: true });
    return;
  }
  if (kind === "fullscreen") {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await document.documentElement.requestFullscreen();
    } catch {
      toast("请使用浏览器全屏功能。");
    }
    return;
  }
  if (kind === "sound") {
    sound = !sound;
    button.textContent = `到站音：${sound ? "开" : "关"}`;
    if (sound) beep();
    return;
  }
  if (kind === "screen") {
    window.open(`/screen?room=${room}`, "_blank", "noopener");
    return;
  }
  if (kind === "ending") {
    clearInterval(replayTimer);
    replayView = false;
    replayTick = null;
    applyTheme(state.tick);
    renderBoard();
    return;
  }
  if (kind === "replay") {
    clearInterval(replayTimer);
    const started = performance.now(),
      max = Math.max(1, state.tick);
    replayTick = 0;
    replayView = true;
    renderBoard();
    replayTimer = setInterval(() => {
      replayTick = Math.min(
        max,
        Math.floor(((performance.now() - started) / 20000) * max),
      );
      renderMarkers(replayTick);
      applyTheme(replayTick);
      const caption = document.querySelector("#replay-caption");
      if (caption)
        caption.textContent = `${replayTick >= max ? "回放结束" : "行程回放"} / ${rail(replayTick)} / 彩色线条为真实已出发行程`;
      if (replayTick >= max) clearInterval(replayTimer);
    }, 100);
    return;
  }
  if (kind === "abort" && !confirm("中止当前对局？本局不会计为任何阵营获胜。"))
    return;
  if (kind === "kick" && !confirm("移除此席位？该玩家可重新扫码加入。")) return;
  if (
    ["continue-force", "launch-force"].includes(kind) &&
    !confirm("让尚未确认的玩家全部留站，并继续行车？")
  )
    return;
  busy = true;
  try {
    if (kind === "create" || kind === "new-room") {
      clearInterval(replayTimer);
      replayTick = null;
      seenArrivalKeys = new Set();
      arrivalSoundInitialized = false;
      const result = await request(
        "/api/rooms",
        kind === "new-room" ? { previousRoom: room } : {},
      );
      room = result.code;
      credential = result.token;
      saveCredential(room, credential);
      history.replaceState(null, "", `/host?room=${room}`);
      initBoard();
      receive(await request(`/api/state?room=${room}`), true);
    } else if (kind === "travel") {
      await command("travel", {
        card: selectedCard,
        destination: selectedDestination,
      });
    } else if (kind === "stop") {
      await command("stop", {
        card: state.me.trip.card.id,
        segment: state.me.trip.segment,
      });
    } else if (kind === "wait") {
      await command("wait");
    } else if (kind === "setup") {
      await command("setup", { template });
    } else if (kind === "dialogue") {
      await command("dialogue", { enabled: !state.dialogueEnabled });
    } else if (kind === "kick") {
      await command("kick", { player: Number(button.dataset.player) });
    } else if (kind === "change") {
      await command("change", {
        slot: 0,
        edge: document.querySelector("#change-edge").value,
      });
    } else if (kind === "launch-force") {
      await command("launch", { force: true });
    } else if (kind === "pause") {
      await command("pause", { paused: !state.decisionPaused });
    } else if (kind === "scroll-stations") {
      document
        .querySelector(".station-axis")
        ?.scrollBy({
          top: 130,
          behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
            ? "instant"
            : "smooth",
        });
    } else if (kind === "continue") {
      await command("continue");
    } else if (kind === "continue-force") {
      await command("continue", { force: true });
    } else if (kind === "request-wait") {
      await command("request_wait", { target: Number(button.dataset.target) });
    } else if (kind === "respond-request") {
      await command("respond_request", {
        accept: button.dataset.accept === "true",
      });
    } else if (kind === "export") {
      const data = await request(`/api/export?room=${room}`);
      const url = URL.createObjectURL(
        new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
      );
      const a = document.createElement("a");
      a.href = url;
      a.download = `秘封纪行-${room}.json`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } else {
      if (kind === "rematch") {
        clearInterval(replayTimer);
        replayTick = null;
        selectedCard = null;
        selectedDestination = null;
        lastMessageId = 0;
        seenArrivalKeys = new Set();
        arrivalSoundInitialized = false;
      }
      await command(kind);
    }
  } catch (err) {
    toast(err.message);
  } finally {
    busy = false;
    poll();
  }
});
async function init() {
  applyTheme(0);
  [config, assets, mapText, gameText] = await Promise.all([
    request("/api/config", undefined, ""),
    fetch("/assets/manifest.json").then((r) => r.json()),
    fetch("/map.svg").then((r) => r.text()),
    request("/game-text.json", undefined, ""),
  ]);
  names = gameText.roles;
  configureArt();
  const saved = localStorage.getItem("hifuu-address");
  address =
    config.publicAddress ||
    (config.addresses.includes(saved) ? saved : config.addresses[0]);
  credential = isScreen ? "" : loadCredential(room);
  if (!isHost && !isScreen && !isPhone) {
    initHome();
    return;
  }
  if (isPhone && !credential) {
    joinForm();
  } else if (isHost && !credential) {
    hostEntry();
  } else {
    if (!isPhone) initBoard();
    await poll();
  }
  setInterval(poll, 700);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) poll();
  });
}
init().catch((e) => {
  app.innerHTML =
    '<main class="welcome"><h1>暂时无法打开时刻表</h1><p>请确认 Python 服务正在运行，然后刷新页面。</p></main>';
  toast(e.message);
});
