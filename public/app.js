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
  phoneEncounter = "",
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
  draw();
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
const roleArt = (role) => assets.roles?.[role] || "";
const appearanceArt = (appearance) => assets.appearance?.[appearance] || "";
const roleBadge = (role, label) => {
  const key = names[role] ? role : "unknown";
  return `<span class="role-icon" aria-label="${esc(names[key] || "身份未公开")}">${roleArt(key) ? `<img src="${esc(roleArt(key))}" alt="${esc(names[key] || "身份未公开")}">` : esc(label)}</span>`;
};
const publicRole = (player) => (names[player?.role] ? player.role : "unknown");
// The hat shares the brown portrait's canvas, so both layers take the same crop.
const avatarHat = () => {
  const hat = appearanceArt("hat");
  return hat
    ? `<img class="avatar-hat" src="${esc(hat)}" alt="">`
    : '<i class="avatar-hat-tag" aria-hidden="true">帽</i>';
};
const publicAvatar = (player, hat = false) => {
  const appearance = state?.phase !== "ended" ? player?.appearance : null;
  const path = appearance
    ? appearanceArt(appearance)
    : roleArt(publicRole(player));
  const label = appearance
    ? appearance === "brown"
      ? "棕色旅人"
      : "紫色旅人"
    : names[player?.role] || "身份未公开";
  return `<span class="public-avatar" ${appearance ? `data-appearance="${esc(appearance)}"` : ""} aria-label="${esc(label)}${hat ? "（戴帽）" : ""}">${path ? `<img src="${esc(path)}" alt="${esc(label)}">` : appearance === "brown" ? "棕" : appearance === "purple" ? "紫" : "秘"}${hat ? (path ? avatarHat() : '<i class="avatar-hat-tag" aria-hidden="true">帽</i>') : ""}</span>`;
};
function activityEntries(limit = 8) {
  const players = new Map(
    (state?.players || []).map((player) => [player.id, player]),
  );
  const arrivals = (state?.arrivals || []).map((event) => ({
    kind: "arrival",
    id: event.id || `arrival:${event.player}:${event.station}:${event.tick}`,
    appearance: event.appearance,
    player: event.player,
    seat: players.get(event.player)?.seat,
    name: players.get(event.player)?.name,
    tick: event.tick,
    station: event.station,
  }));
  const dialogues = (state?.publicDialogues || []).map((event) => ({
    kind: "dialogue",
    ...event,
  }));
  return [...arrivals, ...dialogues]
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
      return `<div class="activity-item arrival-item"><span class="activity-badge">${event.appearance === "brown" ? "棕" : `${event.seat}号`}</span><div><b>${esc(event.appearance === "brown" ? "棕色旅人" : event.name || "旅人")}</b><small>${rail(event.tick)} · 到站记录</small><p>到达 ${esc(station(event.station))}</p></div></div>`;
    })
    .join("")}</div>`;
}
function dialogueCard(event) {
  const player = state.players.find((p) => p.id === event.player);
  return `<article class="dialogue-card" data-dialogue="${event.id}"><div class="dialogue-heading">${publicAvatar(player)}<b>旅人${player?.seat || ""} · ${esc(player?.name || "旅人")}</b></div><small>${rail(event.tick)} · 系统剧情</small><p>「${esc(event.text)}」</p>${event.source ? `<p class="dialogue-source"><a href="${esc(event.sourceUrl)}" target="_blank" rel="noopener">${esc(event.source)}</a><br>${esc(event.mapping)}</p>` : ""}</article>`;
}
function dialogueHistory() {
  const events = state.publicDialogues || [];
  return events.length
    ? `<details id="dialogue-history" class="dialogue-history"><summary>旅途回声 · ${events.length}条系统剧情</summary>${events.slice().reverse().map(dialogueCard).join("")}</details>`
    : "";
}
function publicNotebook() {
  return `${dialogueHistory()}<details id="phone-public" class="phone-public"><summary>查看公开记录</summary>${activityFeed(8)}<p class="activity-note">记录显示上次目击的地点与时刻。梅莉被侵蚀后仍会触发旅途回声。</p></details>`;
}
function applyTheme(tick = state?.tick || 0) {
  const minute = (960 + tick * 15) % 1440;
  document.documentElement.dataset.theme =
    minute >= 360 && minute < 1080 ? "day" : "night";
}
function initHome() {
  app.innerHTML = `<main class="title-scene title-home"><img class="title-art" src="${esc(assets.scenes.title)}" alt="莲子与梅莉坐在夕阳下的列车车厢中"><div class="title-content"><div class="eyebrow">HIFUU RAILWAY JOURNEY</div><div class="title-logo">${emblem}<h1><span>秘封</span>铁道纪行</h1></div><p class="lead">下一站，<br>去见你的旅伴。</p><nav class="title-menu" aria-label="游戏入口"><a href="/host"><small>01</small><span>开启旅程<em>主持端</em></span><b>→</b></a><a href="/join"><small>02</small><span>持票入场<em>玩家端</em></span><b>→</b></a><a href="/rules.html" target="_blank" rel="noopener"><small>03</small><span>玩法说明<em>旅行须知</em></span><b>↗</b></a></nav></div></main>`;
}
function joinForm() {
  app.innerHTML = `<main class="entry-scene"><a class="entry-brand" href="/">秘封铁道纪行</a><section class="join-page paper-ticket"><div class="eyebrow">加入房间</div><h1>登上这趟列车</h1><p class="muted">扫描大屏幕上的二维码或输入房间码加入游戏。</p><form id="join-form" novalidate><label>房间码<input name="room" value="${esc(room)}" maxlength="5" placeholder="ABCDE" required autocomplete="off" autocapitalize="characters"></label><label>你的旅人昵称<input name="name" maxlength="12" placeholder="最多 12 个字" required autocomplete="nickname"></label><button class="primary" type="submit">领取车票 →</button></form><p class="entry-note">可以自由交谈、说谎，请勿展示手机。</p></section></main>`;
}
function hostEntry() {
  app.innerHTML = `<main class="title-scene host-entry"><section class="host-entry-ticket paper-ticket"><div class="eyebrow">主持人</div>${emblem}<h1>准备发车</h1><p class="lead">3–6人同玩：手机各自选路，大屏显示地图。</p><button class="primary" data-action="create">创建房间</button><p class="muted">玩家通过主持人提供的网址加入。<br>主持端请通过 localhost 打开，保持此页面在前台。</p><a href="/">← 返回入口</a></section></main>`;
}
function atlasFurniture() {
  return `<header class="atlas-heading"><div class="atlas-title"><span class="atlas-kicker">秘封倶楽部</span><h1>秘封铁道纪行<span>轨道线网图</span></h1><p><b id="public-renko"></b>头像停在最近一次到站的地方，不是实时位置。</p></div><div class="atlas-edition">${emblem}</div></header><figure class="atlas-postcard" aria-label="随公共时钟切换的日夜窗景"><div class="atlas-window"></div><figcaption><span>車窓の向こう</span><small>沿途风景</small></figcaption></figure><div id="atlas-status" class="atlas-alert" role="status"></div>`;
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
  collectAtlasLayout(svg);
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
  return `<div class="notice danger">${state.technical.countdown !== null ? "连接已恢复，即将继续" : "连接中断，游戏时间已暂停"}<small>请保持大屏和所有玩家手机在前台。</small></div>`;
}
function joinCode() {
  const joinAddress = config.publicAddress || state.joinAddress || address;
  return `<section class="rejoin-card"><div class="join-qr">${config.qrAvailable ? `<img alt="扫码返回当前房间" src="/api/qr?room=${room}&address=${encodeURIComponent(joinAddress)}">` : "<span>使用右侧网址加入</span>"}</div><div><b>扫码返回本局</b><p>房间码 ${esc(room)} · 请用原手机浏览器恢复座位</p><div class="join-address">${esc(joinAddress)}/join?room=${esc(room)}</div></div></section>`;
}
function continueStatus() {
  if (state.phase === "briefing") return "首程不限时，等待主持人发车";
  if (state.decisionPaused) return "主持人已暂停，等待手动继续";
  if (state.decisionRemaining !== null) return decisionClock();
  return "全部确认后3秒自动继续";
}
function timerStrip() {
  const paused = state.phase === "decision" || state.phase === "briefing";
  const status = state.technical
    ? "连接恢复中，游戏时间暂停"
    : state.phase === "briefing"
      ? "首轮选路，等待发车"
      : paused
        ? "等待确认，游戏时间暂停"
        : state.phase === "ended"
          ? "旅程已结束"
          : "列车运行中";
  const night = document.documentElement.dataset.theme === "night";
  const decisionSummary = paused
    ? `已确认 ${state.readyCount} 人${state.pendingCount ? ` · 待确认 ${state.pendingCount} 人` : ""} · ${continueStatus()}`
    : "";
  return `<div class="timer-strip"><div class="clock-heading"><span class="eyebrow">铁路时刻</span><span class="period-seal">${night ? "夜行" : "日行"}</span></div><div class="game-clock"><strong data-clock="rail">${rail((state.railMinutes ?? state.tick * 15) / 15)}</strong><span class="clock-unit">游戏时间</span></div><div class="rail-time"><span>${status}</span>${paused ? `<span>${decisionSummary}</span>` : ""}</div></div>`;
}

function outcomeKey() {
  if (state.result.outcome !== "yukari") return state.result.outcome;
  return state.result.reason.startsWith("她记得")
    ? "yukari_corrupted"
    : "yukari_direct";
}
function ending() {
  const titles = gameText.endingTitles;
  const sumireko = state.result.sumireko;
  const collectiveLoss =
    sumireko?.total > 0 &&
    sumireko.won === false &&
    state.result.outcome !== "aborted";
  const lossArt = collectiveLoss ? assets.endings.sumireko_loss : "";
  const ownLoss = state.me?.role === "sumireko" && lossArt;
  const cg = ownLoss || assets.endings[outcomeKey()];
  const artTitle = ownLoss ? "堇子集体未获胜" : titles[state.result.outcome];
  const collective =
    sumireko?.total && state.result.outcome !== "aborted"
      ? `<p class="collective-result">${sumireko.won ? "堇子全员共同获胜" : "堇子集体未获胜"} · ${sumireko.remaining}/${sumireko.total} 人未被紫抓到</p>`
      : "";
  const collectiveArt =
    lossArt && !ownLoss
      ? `<figure class="collective-ending"><img class="sumireko-loss-cg" src="${esc(lossArt)}" alt="堇子集体未获胜结局画面"><figcaption>堇子集体未获胜</figcaption></figure>`
      : "";
  return `<section class="ending ${cg ? "" : "ending-typeset"}" data-outcome="${esc(outcomeKey())}"><div class="ending-art"><div class="ending-placeholder" aria-label="结局插画预留">${emblem}<span>${esc(artTitle)}</span><small>${state.result.station ? esc(station(state.result.station)) : "HIFUU RAILWAY"} · ${rail(state.result.tick)}</small></div>${cg ? `<img class="ending-cg" src="${esc(cg)}" alt="${esc(artTitle)}结局画面">` : ""}</div><div class="ending-story"><div class="eyebrow">END OF THE JOURNEY / 终着</div><h2>${esc(titles[state.result.outcome])}</h2><p>${esc(state.result.reason)}</p>${state.result.station ? `<p class="ending-place">${esc(station(state.result.station))} · ${rail(state.result.tick)}</p>` : ""}${collective}${collectiveArt}<div class="reveal-list">${state.players.map((p) => `<div>${roleBadge(p.role, p.seat)}<span>${esc(p.name)}<b>${esc(names[p.role] || "尚未分配")}${p.corrupted ? "（已侵蚀）" : ""}</b>${personalResult(p)}</span></div>`).join("")}</div></section>`;
}
function personalResult(player) {
  const result = state.result.individuals?.find(
    (item) => item.player === player.id,
  );
  if (!result) return "";
  if (state.result.outcome === "aborted")
    return "<small>对局中止 · 不计胜负</small>";
  return `<small>${result.eligible ? "没被紫抓到" : "被紫抓到过"}${result.hatEligible ? "" : " · 不能再借帽子"}</small>`;
}
// Purple markers carry a public seat; brown ones a letter and seat only until
// Renko first meets a Sumireko, and nothing at all from then on.
const markerTag = (marker) =>
  marker.appearance === "brown" ? marker.label || "" : String(marker.seat);
function seatName(seat) {
  const player = state.players.find((p) => p.seat === seat);
  return `${seat}号 · ${player?.name || "旅人"}`;
}
// Seats the server may name while a decision waits; the rest are brown and stay a count.
// The list rows and map rings mark them; the centre slot stays with the echo card.
function waitingParts() {
  const seats = state.pendingSeats || [];
  return { seats, others: Math.max(0, (state.pendingCount || 0) - seats.length) };
}
function waitingSummary() {
  const { seats, others } = waitingParts();
  const named = seats.map(seatName).join("、");
  const rest = others ? `${named ? "另有" : ""}${others}名棕色旅人` : "";
  return [named, rest].filter(Boolean).join("，");
}
function sightingName(marker) {
  if (marker.appearance !== "brown") return seatName(marker.seat);
  return `棕色旅人${marker.label ? ` ${marker.label}` : ""}${marker.seat ? ` · ${seatName(marker.seat)}` : ""}`;
}
function sightingLabel(marker) {
  return `${sightingName(marker)}${marker.hat ? "（戴帽）" : ""}${marker.appearance === "brown" ? "" : " · 紫色旅人"}`;
}
function sortedMarkers() {
  return (state.markers || [])
    .slice()
    .sort((a, b) =>
      a.appearance !== b.appearance
        ? a.appearance === "brown"
          ? -1
          : 1
        : a.appearance === "brown"
          ? String(a.label).localeCompare(String(b.label))
          : a.seat - b.seat,
    );
}
function sightingList() {
  const waitingSeats = new Set(state.pendingSeats || []);
  return `<div class="section-title"><h3>旅人目击</h3><span>棕色＝莲子或堇子，紫色＝梅莉或紫；莲子初遇堇子后棕色匿名</span></div><div class="arrival-list anonymous-arrivals">${sortedMarkers()
    .map(
      (m) =>
        `<div data-marker="${esc(m.id)}"${waitingSeats.has(m.seat) ? ' class="is-waiting"' : ""}><span class="sighting-avatar">${publicAvatar(m, m.hat)}${markerTag(m) ? `<b class="sighting-tag" data-appearance="${esc(m.appearance)}">${esc(markerTag(m))}</b>` : ""}</span><span class="traveler"><span class="traveler-role">${esc(sightingName(m))}${m.hat ? '<em class="hat-note">戴帽</em>' : ""}${waitingSeats.has(m.seat) ? '<em class="waiting-note">选路中</em>' : ""}</span><span class="traveler-station"><b>${esc(station(m.lastArrival.station))}</b><small>${rail(m.lastArrival.tick)}</small></span></span></div>`,
    )
    .join("")}</div>`;
}
function renderBoard() {
  preserveView(app, renderBoardContent);
}
function renderBoardContent() {
  const lobby = state.phase === "lobby";
  const atlasStatus = document.querySelector("#atlas-status");
  // Renko's seat is public by the rules; the board says so instead of the host.
  const renko = state.players.find((p) => p.role === "renko");
  document.querySelector("#public-renko").textContent =
    renko && state.phase !== "ended" ? `莲子（公开）：${renko.seat}号 · ${renko.name}` : "";
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
      html += `<button data-action="launch" ${state.readyCount < state.players.length ? "disabled" : ""}>正式发车开启旅程</button><button data-action="launch-force" ${state.readyCount < state.players.length ? "" : "disabled"}>未确认者留站并发车</button>`;
    if (isHost && state.phase === "decision") {
      const pending = state.pendingCount || 0;
      html += `<button data-action="pause" aria-pressed="${state.decisionPaused}">${state.decisionPaused ? "恢复自动继续" : "暂停自动继续"}</button><button class="primary" data-action="continue" ${pending || state.technical ? "disabled" : ""}>立即继续行车</button><button data-action="continue-force" ${state.canForceContinue && !state.technical ? "" : "disabled"}>未选路者留站并继续</button>`;
    }
    controls.innerHTML = html;
  }
  if (controls && isHost && lobby)
    controls.innerHTML = `<button class="primary start-game" data-action="setup" aria-describedby="host-start-status" ${state.players.length < 3 || state.players.some((p) => !p.ready || !p.connected) ? "disabled" : ""}>开始游戏并分配身份 →</button>`;
  if (
    controls &&
    isHost &&
    state.phase !== "ended" &&
    typeof state.dialogueEnabled === "boolean"
  ) {
    controls.insertAdjacentHTML(
      "beforeend",
      `<button data-action="dialogue" aria-pressed="${state.dialogueEnabled}" title="第二日00:00起，经过相关站点有机会触发角色对白">旅途回声：${state.dialogueEnabled ? "开" : "关"} · 第二日00:00起</button>`,
    );
  }
  // The destructive control stays last, apart from the ones used every stop.
  if (controls && isHost && !["lobby", "ended"].includes(state.phase))
    controls.insertAdjacentHTML(
      "beforeend",
      '<button data-action="abort">中止本局</button>',
    );
  if (controls && state.phase === "ended")
    controls.innerHTML = `<button data-action="${replayView ? "ending" : "replay"}">${replayView ? "← 返回结局" : "▶ 行程回放"}</button>${replayView ? '<button data-action="replay">重新播放</button>' : ""}${isHost ? '<button data-action="export">导出本局</button><button data-action="rematch">同组玩家再来一局</button><button data-action="new-room">创建新房间</button>' : ""}`;
  const startStatus = document.querySelector("#host-start-status");
  if (startStatus) {
    const ready = state.players.filter((p) => p.ready && p.connected).length;
    const disconnected = state.players.filter((p) => !p.connected).length;
    const waiting = state.pendingCount ? waitingSummary() : "";
    const requiredCount = state.readyCount + (state.pendingCount || 0);
    startStatus.textContent =
      isHost && lobby
        ? `已加入 ${state.players.length}/6 人 · 已准备 ${ready}/${state.players.length} 人。${state.players.length < 3 ? `还需 ${3 - state.players.length} 人扫码加入。` : disconnected ? "请等待断线玩家重新连接。" : ready < state.players.length ? "请让玩家在手机上点击“准备出发”。" : "全员已准备，可以开局。"}`
        : isHost && state.phase === "briefing"
          ? `首程不限时 · 已确认 ${state.readyCount}/${state.players.length} 人${waiting ? ` · 还没选：${waiting}` : "，等待发车。"}`
          : isHost && state.phase === "decision"
            ? `讨论中 · 已确认 ${state.readyCount}/${requiredCount} 人${waiting ? ` · 待确认：${waiting}` : state.decisionPaused ? " · 主持人已暂停。" : " · 全部确认，3秒后自动继续。"}`
            : "";
    document.querySelector(".host-control-strip").hidden =
      !controls?.children.length && !startStatus.textContent;
    controls
      ?.querySelector('[data-action="launch"]')
      ?.classList.add("primary", "start-game");
  }
  if (lobby) {
    panel.innerHTML = `<img class="title-art" src="${esc(assets.scenes.title)}" alt="莲子与梅莉坐在夕阳下的列车车厢中"><div class="title-content"><div class="eyebrow">HIFUU RAILWAY JOURNEY</div><div class="title-logo">${emblem}<h1><span>秘封</span>铁道纪行</h1></div><section class="boarding-ticket paper-ticket"><div class="ticket-heading"><span class="eyebrow">BOARDING / 正在检票</span><span>3–6人席 ${state.players.length}/6</span></div><h2>候车大厅</h2><div class="boarding"><div class="join-qr">${config.qrAvailable ? `<img alt="扫码加入房间" src="/api/qr?room=${room}&address=${encodeURIComponent(address)}">` : "<span>使用下方网址加入</span>"}</div><div><span class="muted">房间码</span><strong class="room-code">${room}</strong><span class="muted">手机扫码或输入房间码加入</span></div></div><div class="join-address">${esc(address)}/join</div><div class="seat-list">${[
      0, 1, 2, 3, 4, 5,
    ]
      .map((i) => {
        const p = state.players.find((p) => p.seat === i + 1);
        return `<div>${publicAvatar()}<span class="seat-number">${i + 1}</span><span class="seat-name">${p ? esc(p.name) : "等待旅人"}</span><span class="seat-status ${p?.ready && p?.connected ? "ready" : ""}">${p ? (!p.connected ? "已断线" : p.ready ? "已准备" : "阅读须知中") : "空席"}</span>${p && isHost ? `<button class="text-button" data-action="kick" data-player="${p.id}" aria-label="移除 ${esc(p.name)}">×</button>` : ""}</div>`;
      })
      .join(
        "",
      )}</div><p class="lobby-note">可以自由交流与说谎，请勿展示手机。</p><p class="lobby-note">观众屏会自动跟随主持人切换到新房间。</p>${isHost ? `<details id="host-settings" class="host-settings"><summary>开局设置与网络地址</summary><label>手机加入地址<select id="address-select">${config.addresses.map((a) => `<option ${a === address ? "selected" : ""}>${a}</option>`).join("")}</select></label><label>起点组合<select id="template-select"><option value="random">全国分散起点（最短行车至少5小时）</option>${config.templates.map((t) => `<option value="${t.template_id}" ${template === t.template_id ? "selected" : ""}>${t.station_a} / ${t.station_b} / ${t.station_c} （最短间距${duration(t.min_distance_ticks * 15)}）</option>`).join("")}</select></label><p>${config.publicAddress ? "已配置公网地址。" : "请选择手机可访问的地址。"}${state.joinAddress ? `上次手机成功加入：${esc(state.joinAddress)}。` : ""}手机扫码无法连接时，请切换地址。127.0.0.1 仅供本机使用。</p></details>` : ""}</section></div>`;
  } else if (state.phase === "ended") {
    panel.innerHTML = replayView
      ? `<div class="eyebrow">本局行程</div><h2>重看这段旅程</h2><p id="replay-caption" class="muted">行程回放 ${rail(replayTick || 0)}</p><div class="replay-legend">${state.players.map((p, i) => `<p><i style="background:${["#c48122", "#9754a9", "#215669", "#a55332", "#63845d", "#ad7292"][i]}"></i>${p.seat}号 ${esc(p.name)} · ${esc(names[p.role] || "尚未分配")}</p>`).join("")}</div><p class="muted">20 秒回顾旅行路线</p>`
      : ending();
  } else {
    panel.innerHTML = timerStrip() + sightingList();
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
  const encounter = state.me?.encounter;
  const encounterKey = encounter ? `${room}:${encounter.id}` : "";
  const pendingEncounter = encounter && !encounter.confirmed;
  const view =
    state.phase === "lobby" || state.phase === "ended"
      ? state.phase
      : state.me?.submitted
        ? "submitted"
        : state.me?.motion
          ? "moving"
          : `station:${state.me?.station}`;
  const same = phoneView === `${room}:${view}`;
  // Fresh from the lobby, the role card matters more than reaching the stop list.
  const reveal =
    phoneView === `${room}:lobby` && view.startsWith("station:");
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
      if (!pendingEncounter && !reveal && box.bottom > bottom)
        window.scrollBy(0, box.bottom - bottom);
      if (!pendingEncounter && !reveal && axis.getBoundingClientRect().top < 8)
        window.scrollBy(0, axis.getBoundingClientRect().top - 8);
    }
  }
  if (pendingEncounter && encounterKey !== phoneEncounter)
    document
      .querySelector(".encounter-panel")
      ?.scrollIntoView({ block: "start" });
  phoneEncounter = encounterKey;
  updateAxisHint();
  axisOffer = offer;
  setConnection(connectionOK);
}
function renderPhoneContent() {
  const me = state.me;
  if (!me) return;
  if (state.phase === "lobby") {
    app.innerHTML = `<main class="phone-shell lobby-phone"><div class="eyebrow">候车中</div><div class="waiting-identity">${roleBadge(null, "秘")}<div class="big-seat">${me.seat}</div></div><h1>${esc(me.name)}，欢迎乘车。</h1><p class="muted">房间 ${room} 已保留你的座位</p><div class="instruction"><h3>怎么玩</h3><p><b>大家当面商量，各自在手机上选路。</b></p><p>开局后，手机会告诉你身份和目标。大屏只显示每人最近一次到站的地方，棕色头像不标是谁。</p><p>有人到站时全场暂停；需要选路的人都提交后，3秒继续行车。</p><p><b>可以说谎，但别给别人看手机。</b></p></div><button class="primary" data-action="ready" ${state.players.find((p) => p.id === me.id)?.ready ? "disabled" : ""}>${state.players.find((p) => p.id === me.id)?.ready ? "已准备，等待主持人" : "已了解，准备出发"}</button><p class="muted center">保持此页在前台。刷新可回到原座位。</p><div id="connection" class="connection">● 已连接</div></main>`;
    return;
  }
  if (state.phase === "ended") {
    app.innerHTML = `<main class="phone-shell">${ending()}${dialogueHistory()}<div class="notice">等待主持人开启下一局。</div></main>`;
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
  const waiting = state.pendingCount ? `${state.pendingCount}名` : "";
  const map = lineMapSpec(me);
  app.innerHTML = `<main class="phone-shell submitted-screen">${phoneHeader(me)}<div class="private-label">仅你可见 · 别给别人看手机 <span id="connection">● 已连接</span></div>${techBanner()}${encounterPanel(me)}
    <section class="submitted-ticket paper-ticket" role="status"><div class="eyebrow">${me.autoWait ? "主持人已安排留站" : "车票已确认"}</div><h1>${me.autoWait ? "已自动留站" : me.chosenDestination ? "已提交" : "已选择留站"}</h1><h2>${esc(station(me.station))}${me.chosenDestination ? " → " + esc(station(me.chosenDestination)) : " · 留站等候30分钟"}</h2>
    <p>${waiting ? "等待 " + waiting + " 玩家确认。" : state.phase === "briefing" ? "全员已提交，等待主持人正式发车。" : state.decisionPaused ? "主持人已暂停，等待继续。" : "全部确认，即将自动继续。"}</p><button disabled>${me.chosenDestination ? "行程已锁定" : me.autoWait ? "主持人已安排留站" : "留站已锁定"}</button><p class="muted">${continueStatus()}</p></section>${lineMapFigure(map)}${privateNotebook(me)}${publicNotebook()}</main>`;
  drawLineMap(map);
}
function decisionNotice(me) {
  if (!me.otherPlayersDeciding) return "";
  const waiting = state.pendingCount ? `${state.pendingCount}人` : "";
  return `<div class="decision-notice" role="status"><b>${waiting ? `讨论中 · 等待 ${waiting} 确认` : "已全部确认"}</b><span>${continueStatus()}</span></div>`;
}
function encounterPanel(me) {
  const encounter = me.encounter;
  if (!encounter || state.phase !== "decision") return "";
  return `<section class="encounter-panel encounter-card" aria-label="本次相遇结果" aria-live="polite"><div class="eyebrow">仅你可见 · ${rail(encounter.tick)}</div><h2>${encounter.changed ? (me.role === "renko" ? "帽子借出去了" : "你戴上了莲子的帽子") : "这次没有换帽"}</h2><p>点确认后，等其他人选好路、看完结果，再继续行车。</p><button class="primary" data-action="confirm-encounter" data-encounter="${esc(encounter.id)}" ${encounter.confirmed || state.technical ? "disabled" : ""}>${encounter.confirmed ? "已确认" : "确认"}</button></section>`;
}
function roleAbilities(me) {
  let html = "";
  if (me.renkoLastArrival) {
    const seen = me.renkoLastArrival;
    html += `<section class="role-ability renko-record"><h3>莲子上次到站</h3><p><b>${esc(station(seen.station))}</b> · ${rail(seen.tick)}</p><small>这是她上次停下的地方，她可能已经走了。</small></section>`;
  }
  const hat = me.hat;
  if (me.role === "sumireko" && hat) {
    html += `<section class="role-ability hat-status"><h3>你正戴着莲子的帽子</h3><p>最晚于 ${rail(hat.until)} 归还；遇到梅莉或紫会立即归还。</p></section>`;
  }
  if (
    me.role !== "renko" ||
    !hat ||
    !state.players.some((p) => p.appearance === "brown" && p.id !== me.id)
  )
    return html;
  const wearer =
    hat.holder !== me.id
      ? state.players.find((p) => p.id === hat.holder)
      : null;
  const status = wearer
    ? `由${wearer.seat}号戴帽，最晚于 ${rail(hat.until)} 归还`
    : hat.cooldownUntil > state.tick
      ? `帽子已归还，${rail(hat.cooldownUntil)} 冷却结束`
      : "帽子在你手中";
  html += `<section class="role-ability hat-status"><h3>借你一顶帽子</h3><p>${esc(status)} · 剩余 ${hat.usesLeft} 次</p><p>遇到堇子时，如果帽子在你手里、冷却已过、还有次数，就有一半机会借给她。结果会弹在手机上。</p><small>借出成功才扣次数。</small></section>`;
  return html;
}
function privateNotebook(me) {
  return `${roleAbilities(me)}<details id="private-notebook" class="private-notebook"><summary>旅途札记 · ${me.messages?.length || 0} 条消息</summary><p class="notebook-goal">当前目标：${goal(me)}</p>${me.protectionUntil > state.tick && !me.corrupted ? "<p>现世庇护 · 次日06:00前不会被侵蚀</p>" : ""}${
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
function phoneHeader(me, withRoom = false) {
  return `<header class="phone-header"><span>秘封铁道纪行</span><span>${withRoom ? `${room} · ` : ""}${me.seat}号 · ${esc(me.name)}</span></header>`;
}
const etaTick = () =>
  ((state.railMinutes ?? state.tick * 15) +
    (state.me?.motion?.remainingRailMinutes || 0)) /
  15;
function renderMovingPhone(me) {
  const m = me.motion;
  const moving = connectionOK && !m.paused && !m.held && !m.station;
  const map = lineMapSpec(me);
  app.innerHTML = `<main class="phone-shell moving-screen ${moving ? "is-traveling" : "is-stopped"}" style="--line-color:${esc(m.color)}">
    ${phoneHeader(me)}<div class="private-label">仅你可见 · 别给别人看手机 <span id="connection">● 已连接</span></div>
    ${decisionNotice(me)}${techBanner()}${encounterPanel(me)}
    <div class="moving-clock"><span>游戏时间</span><b data-clock="rail">${rail((state.railMinutes ?? state.tick * 15) / 15)}</b></div>
    ${lineMapFigure(map)}
    <section class="journey-countdown"><span class="eyebrow">距离 ${esc(station(m.destination))}</span><strong data-clock="journey">${journeyClock(m.remainingRailMinutes)}</strong><p>剩余游戏时间（时:分） · 预计 <b data-clock="eta">${rail(etaTick())}</b> 到达${m.paused ? " · 已暂停" : ""}</p></section>
    ${me.trip ? `<section class="stop-request" aria-live="polite"><button data-action="stop" ${me.canStop ? "" : "disabled"}>${m.stopRequested ? "已申请下一站下车" : "下一站下车，重新选路"}</button><p>${m.stopRequested ? `将在 ${esc(station(m.target))} 下车，重新抽线选路。` : `下一站：${esc(station(m.target))}。下车后重新抽线选路，不耗改签次数。`}</p></section>` : ""}
    <section class="live-position"><div class="section-title"><h2>实时位置</h2><span>仅你可见</span></div><p class="motion-line">${esc(m.line)}</p><h3>${m.station ? esc(station(m.station)) : `${esc(station(m.source))} → ${esc(station(m.target))}`}</h3>
    <div class="position-track"><span class="position-dot" style="left:${m.progress * 100}%"></span></div><div class="position-endpoints"><span>${esc(station(m.source))}</span><span>${esc(station(m.target))}</span></div>
    <p class="position-status">${m.held ? "行程暂停" : m.station ? (me.queued ? "等待发车" : "中途停站中") : `当前区间已行进 ${Math.floor(m.progress * 100)}%`}</p></section>
    ${me.corrupted ? '<p class="current-private-state">目标已改变：协助紫，诱导莲子会合。</p>' : ""}${privateNotebook(me)}${publicNotebook()}
  </main>`;
  drawLineMap(map);
}
function lockedReason(me) {
  if (me.canAct) return "";
  if (state.technical) return "连接恢复中，暂时不能选路";
  if (me.waitUntil > state.tick) return `留站中，${rail(me.waitUntil)} 后重新抽线`;
  if (me.holdUntil > state.tick) return `被耽搁中，${rail(me.holdUntil)} 后可选路`;
  return "现在不能选路，请等待";
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
  const map = lineMapSpec(me);
  app.innerHTML = `<main class="phone-shell station-screen">${phoneHeader(me, true)}<div class="private-label">仅你可见 · 别给别人看手机 <span id="connection">● 已连接</span></div>
    ${encounterPanel(me)}<section class="identity">${roleBadge(me.role, "秘")}<div><h1>${esc(names[me.role])}</h1><p class="goal">${esc(gameText.shortGoals[goalKey(me)])}</p></div></section><details id="goal-details" class="goal-details"><summary>身份与完整目标</summary><p>${goal(me)}</p></details>
    <section class="location"><div class="location-heading"><span class="eyebrow">当前车站</span><span data-clock="rail">${rail((state.railMinutes ?? state.tick * 15) / 15)}</span></div><h2>${esc(station(me.station))}</h2><span class="station-stamp">${state.phase === "briefing" ? "首程选路" : me.canAct ? "等待选路" : "留站等候"}</span>${me.waitUntil > state.tick ? `<p>留站至 ${rail(me.waitUntil)}，随后重新抽线</p>` : ""}${me.holdUntil > state.tick ? `<div class="status-tag">行程暂停，至 ${rail(me.holdUntil)}</div>` : ""}${me.protectionUntil > state.tick && !me.corrupted ? '<div class="status-tag">现世庇护 · 次日06:00前不会被侵蚀</div>' : ""}</section>${decisionNotice(me)}${techBanner()}
    <section class="drawn-line" style="--line-color:${esc(offer?.color || "#315c6e")}"><div class="line-heading"><span class="line-code">${esc(offer?.lineId || "线路")}</span><div><span class="eyebrow">本次抽到的线路</span><h2>${esc(offer?.name)}</h2></div><button class="change-shortcut" data-action="open-change">改签：${me.baseChanges + me.bonusChanges}</button></div><p class="line-guidance">滑动查看全线，点击站名选终点。预计时刻含每站15分钟中停。</p>
    ${lineMapFigure(map)}<div class="station-axis-wrap"><div class="station-axis${me.canAct ? "" : " is-locked"}" role="group" aria-label="沿线站点与预计到达时刻">${(offer?.destinations || []).map((d) => `<button class="axis-stop ${d.current ? "current" : ""} ${d.station === selectedDestination ? "selected" : ""}" data-action="select-station" data-card="${esc(offer.id)}" data-destination="${d.station}" ${d.current || !me.canAct ? "disabled" : ""} aria-pressed="${d.station === selectedDestination}"><span class="axis-dot"></span><span class="stop-name">${esc(station(d.station))}${d.current ? "<small>本站 · 出发</small>" : ""}</span><span class="stop-time"><span data-arrival="${d.station}">${d.current ? "现在" : rail(d.arrivalTick)}</span><small>${d.current ? "" : `行程 ${duration(d.ticks * 15)}`}</small></span></button>`).join("")}</div><button class="axis-scroll-hint" data-action="scroll-stations" aria-label="向下滚动查看更多站点" hidden><span>下滑查看更多站点</span><b aria-hidden="true">↓</b></button></div>
    <p class="line-estimate">预计到达时间随发车时间和途中事件调整。</p></section>
    <div class="destination-actions"><div class="ticket-heading"><span>本次车票</span><span>${me.seat}号 / 单程</span></div><div class="ticket-route"><b>${esc(station(me.station))}</b><span>→</span><b>${selected ? esc(station(selected.station)) : "选择目的地"}</b></div><p aria-live="polite">${decisionClock()} ${selected ? `已选 ${esc(station(selected.station))}，预计 <span data-arrival="${selected.station}">${rail(selected.arrivalTick)}</span> 到达` : lockedReason(me) || "请在站点轴上选择目的地"}</p><div class="action-bar"><button class="primary" data-action="travel" ${!me.canAct || !selected ? "disabled" : ""}>确认此程 →</button><button data-action="wait" ${!me.canAct ? "disabled" : ""}>留站等候30分钟</button></div></div>
    ${me.submitted ? `<div class="notice">${me.autoWait ? "主持人已安排留站30分钟。" : "已收到你的选择。"}${state.phase === "briefing" ? "等待主持人正式发车。" : "等待继续行车。"}</div>` : ""}
    <details id="change-panel" class="change-panel"><summary>改签线路 · 剩余 ${me.baseChanges + me.bonusChanges} 次${me.bonusChanges ? `（含奈良奖励 ${me.bonusChanges} 次）` : ""}</summary><p>换成本站的另一条线路，再选终点。每次扣1次，先扣奈良奖励。</p><label>改为<select id="change-edge">${options.map((l) => `<option value="${l.edge}">${esc(l.name)} · ${esc(station(l.start))} ↔ ${esc(station(l.end))} · 本站邻站：${(l.neighbors || []).map((id) => esc(station(id))).join(" / ")} (${esc(l.id)})</option>`).join("")}</select></label><p id="change-line-detail" class="change-line-detail"></p><button data-action="change" ${!options.length || !me.canChange || !(me.baseChanges + me.bonusChanges) ? "disabled" : ""}>确认改签（扣除1次）</button></details>
    ${privateNotebook(me)}${publicNotebook()}
    <footer>途中可申请下一站下车，重新抽线选路。</footer></main>`;
  drawLineMap(map);
}

// Phone line maps reuse the atlas geometry, so shapes and colors match the big screen.
let mapGeometry;
function edgeGeometry() {
  if (mapGeometry) return mapGeometry;
  const doc = new DOMParser().parseFromString(mapText, "image/svg+xml");
  const edges = {};
  for (const group of doc.querySelectorAll('g[id^="e"][data-category]')) {
    const path = [...group.querySelectorAll("path")].at(-1);
    edges[group.id] = {
      points: [
        ...(path?.getAttribute("d") || "").matchAll(/(-?[\d.]+),(-?[\d.]+)/g),
      ].map((m) => [Number(m[1]), Number(m[2])]),
      dashed: !!path?.hasAttribute("stroke-dasharray"),
    };
  }
  const land = [...doc.querySelectorAll('path[id^="land-"]')].map((p) =>
    p.getAttribute("d"),
  );
  return (mapGeometry = { edges, land });
}
const lineOf = (card) =>
  card?.edges?.length
    ? config.lines.find((line) => line.edges.includes(card.edges[0]))
    : null;
function alongPolyline(points, fraction) {
  const lengths = points
    .slice(1)
    .map((p, i) => Math.hypot(p[0] - points[i][0], p[1] - points[i][1]));
  let remaining = fraction * lengths.reduce((a, b) => a + b, 0);
  for (let i = 0; i < lengths.length; i++) {
    if (remaining <= lengths[i] || i === lengths.length - 1) {
      const t = lengths[i] ? Math.min(1, remaining / lengths[i]) : 0;
      return [
        points[i][0] + (points[i + 1][0] - points[i][0]) * t,
        points[i][1] + (points[i + 1][1] - points[i][1]) * t,
      ];
    }
    remaining -= lengths[i];
  }
  return points[0];
}
function travelPoint(motion) {
  const at = motion.station || (motion.progress ? null : motion.source);
  if (at) return config.positions[at];
  const edge = config.edges.find(
    (e) =>
      (e.source === motion.source && e.target === motion.target) ||
      (e.source === motion.target && e.target === motion.source),
  );
  let points = edge && edgeGeometry().edges[edge.id]?.points;
  if (!points?.length) return config.positions[motion.source];
  const [sx, sy] = config.positions[motion.source];
  const gap = (p) => Math.hypot(p[0] - sx, p[1] - sy);
  if (gap(points.at(-1)) < gap(points[0])) points = points.slice().reverse();
  return alongPolyline(points, motion.progress);
}
function lineMapSpec(me) {
  const m = me.motion;
  if (m) {
    const card = me.trip?.card || me.queued;
    return {
      line: lineOf(card),
      trip: card?.edges || [],
      origin: card?.nodes?.[0],
      destination: m.destination,
      point: travelPoint(m),
    };
  }
  const offer = me.hand?.[0];
  const line = lineOf(offer);
  if (!line) return { line };
  const target = me.chosenDestination || selectedDestination;
  const from = offer.nodes.indexOf(me.station),
    to = offer.nodes.indexOf(target);
  return {
    line,
    trip:
      from >= 0 && to >= 0
        ? offer.edges.slice(Math.min(from, to), Math.max(from, to))
        : [],
    origin: me.station,
    destination: to >= 0 ? target : null,
    point: config.positions[me.station],
    // Leave room for the stop list and the fixed ticket below it.
    maxHeight: Math.min(210, innerHeight * 0.3),
  };
}
function lineMapFigure(spec) {
  if (!spec.line) return "";
  return `<figure class="line-map" style="--route-color:${esc(spec.line.color)}"><svg role="img" aria-label="${esc(`${spec.line.name}全线图${spec.destination ? `，终点${station(spec.destination)}` : ""}`)}"></svg><figcaption><b>${esc(spec.line.name)}</b><span><i class="key-you"></i>你的位置</span>${spec.destination ? '<span><i class="key-goal"></i>终点</span>' : ""}<span><i class="key-trip"></i>本次行程</span></figcaption></figure>`;
}
function drawLineMap(spec) {
  const figure = document.querySelector(".line-map"),
    svg = figure?.querySelector("svg"),
    line = spec.line;
  if (!svg || !line) return;
  const geometry = edgeGeometry();
  const route = line.edges.map((id) => ({
    id,
    trip: spec.trip.includes(id),
    ...(geometry.edges[id] || { points: [] }),
  }));
  const extent = route
    .flatMap((edge) => edge.points)
    .concat(line.nodes.map((id) => config.positions[id]).filter(Boolean));
  const xs = extent.map((p) => p[0]),
    ys = extent.map((p) => p[1]);
  const minX = Math.min(...xs),
    maxX = Math.max(...xs),
    minY = Math.min(...ys),
    maxY = Math.max(...ys);
  // Choose map units per CSS pixel first, so strokes and labels stay a fixed size.
  // A closed <details> has no content box yet; its own width is the same column.
  const width =
      svg.getBoundingClientRect().width ||
      figure.closest("details, section, main")?.clientWidth ||
      360,
    pad = 30,
    maxHeight = spec.maxHeight ?? Math.min(300, innerHeight * 0.42);
  const scale = Math.max(
    (maxX - minX) / (width - 2 * pad),
    (maxY - minY) / (maxHeight - 2 * pad),
    640 / width,
  );
  const height = Math.min(
    maxHeight,
    Math.max(170, (maxY - minY) / scale + 2 * pad),
  );
  const box = {
    x: (minX + maxX) / 2 - (width * scale) / 2,
    y: (minY + maxY) / 2 - (height * scale) / 2,
    w: width * scale,
    h: height * scale,
  };
  const px = (n) => +(n * scale).toFixed(2);
  const pts = (points) => points.map((p) => p.join(",")).join(" ");
  svg.setAttribute("viewBox", `${box.x} ${box.y} ${box.w} ${box.h}`);
  svg.style.aspectRatio = `${width} / ${height}`;
  const others = Object.entries(geometry.edges)
    .filter(([id]) => !line.edges.includes(id))
    .map(
      ([, edge]) =>
        `<polyline class="lm-other" points="${pts(edge.points)}" stroke-width="${px(2)}"/>`,
    )
    .join("");
  const dash = (edge) =>
    edge.dashed ? ` stroke-dasharray="${px(9)} ${px(6)}"` : "";
  const routes =
    route
      .map(
        (edge) =>
          `<polyline class="lm-casing" points="${pts(edge.points)}" stroke-width="${px(edge.trip ? 11 : 8)}"/>`,
      )
      .join("") +
    route
      .map(
        (edge) =>
          `<polyline class="lm-route${edge.trip ? " is-trip" : ""}" data-edge="${edge.id}" points="${pts(edge.points)}" stroke-width="${px(edge.trip ? 6.5 : 4)}"${dash(edge)}/>`,
      )
      .join("");
  const stops = line.nodes
    .map((id) => {
      const [x, y] = config.positions[id] || [];
      return x == null
        ? ""
        : `<circle class="lm-stop" cx="${x}" cy="${y}" r="${px(4.5)}" stroke-width="${px(1.8)}"/>`;
    })
    .join("");
  const goal = spec.destination && config.positions[spec.destination];
  // Greedy labels: important stops first; others only where they fit cleanly.
  const placed = line.nodes
    .map((id) => config.positions[id])
    .filter(Boolean)
    .map(([x, y]) => [x - px(5), y - px(5), x + px(5), y + px(5)]);
  const samples = route.flatMap((edge) => {
    const out = [];
    for (let i = 1; i < edge.points.length; i++) {
      const [a, b] = [edge.points[i - 1], edge.points[i]];
      const steps = Math.ceil(Math.hypot(b[0] - a[0], b[1] - a[1]) / px(5));
      for (let s = 0; s <= steps; s++)
        out.push([
          a[0] + ((b[0] - a[0]) * s) / steps,
          a[1] + ((b[1] - a[1]) * s) / steps,
        ]);
    }
    return out;
  });
  const overlaps = (r, q) =>
    r[0] < q[2] && q[0] < r[2] && r[1] < q[3] && q[1] < r[3];
  const fits = (r, strict) =>
    r[0] >= box.x + px(2) &&
    r[2] <= box.x + box.w - px(2) &&
    r[1] >= box.y + px(2) &&
    r[3] <= box.y + box.h - px(2) &&
    !placed.some((q) => overlaps(r, q)) &&
    (!strict ||
      !samples.some(
        ([x, y]) => x > r[0] && x < r[2] && y > r[1] && y < r[3],
      ));
  const ends = [line.nodes[0], line.nodes.at(-1)];
  const key = [spec.destination, spec.origin, ...ends].filter(Boolean);
  const order = [...new Set([...key, ...line.nodes])];
  const labels = order
    .map((id) => {
      if (!config.positions[id]) return "";
      const [x, y] = config.positions[id],
        index = line.nodes.indexOf(id),
        name = station(id),
        important = id === spec.destination || id === spec.origin,
        size = px(important ? 14 : 12),
        tw = [...name].reduce(
          (sum, c) => sum + (c.charCodeAt(0) > 255 ? 1 : 0.6),
          0,
        ) * size,
        th = size * 1.25,
        g = px(8);
      const [a, b] = [
        config.positions[line.nodes[Math.max(0, index - 1)]],
        config.positions[line.nodes[Math.min(line.nodes.length - 1, index + 1)]],
      ];
      const flat = Math.abs(b[0] - a[0]) >= Math.abs(b[1] - a[1]);
      const side = [
        [x + g, y - th / 2],
        [x - g - tw, y - th / 2],
      ];
      const across = [
        [x - tw / 2, y - g - th],
        [x - tw / 2, y + g],
      ];
      const corners = [
        [x + g * 0.7, y - g * 0.7 - th],
        [x + g * 0.7, y + g * 0.7],
        [x - g * 0.7 - tw, y - g * 0.7 - th],
        [x - g * 0.7 - tw, y + g * 0.7],
      ];
      const candidates = (flat ? [...across, ...side] : [...side, ...across])
        .concat(corners)
        .map(([left, top]) => [left, top, left + tw, top + th]);
      const spot =
        candidates.find((r) => fits(r, true)) ||
        (key.includes(id) && candidates.find((r) => fits(r, false))) ||
        (important && candidates[0]);
      if (!spot) return "";
      placed.push(spot);
      return `<text class="lm-label${important ? " is-key" : ""}" x="${spot[0]}" y="${spot[1] + size * 0.98}" font-size="${size}" stroke-width="${px(3.5)}">${esc(name)}</text>`;
    })
    .join("");
  const [hx, hy] = spec.point || [];
  svg.innerHTML = `<rect class="lm-sea" x="${box.x}" y="${box.y}" width="${box.w}" height="${box.h}"/>${geometry.land.map((d) => `<path class="lm-land" d="${d}" stroke-width="${px(1.2)}"/>`).join("")}${others}${routes}${stops}${goal ? `<circle class="lm-goal" cx="${goal[0]}" cy="${goal[1]}" r="${px(10)}" stroke-width="${px(3)}"/>` : ""}${labels}${hx == null ? "" : `<g class="lm-you" transform="translate(${hx} ${hy})"><circle class="lm-pulse" r="${px(14)}"/><circle class="lm-dot" r="${px(7.5)}" stroke-width="${px(3)}"/></g>`}`;
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
function markerHat(avatar, frame) {
  // Both supplied images share a canvas; retain its transparent padding to align the hat.
  const hat = avatar && appearanceArt("hat");
  return `<g class="hat-marker"><path class="hat-fallback" ${hat ? 'visibility="hidden"' : ""} d="M11,18 v-9 q19-8 38 0 v9 h9 v7 h-56 v-7z" fill="#3e281d" stroke="#ffe2a4" stroke-width="3"/>${hat ? `<image class="hat-overlay" href="${esc(hat)}" ${frame}/>` : ""}</g>`;
}
// Map units: the atlas is 3840 wide, so a 1920px screen shows these at half size.
const MARKER_SIZE = 84,
  MARKER_STEP = MARKER_SIZE + 8;
// Pills stay centered on their stations; covered station names move instead.
let atlasStations = [];
// Sheet-unit boxes of .atlas-heading, .atlas .board-panel and .atlas-postcard.
const ATLAS_FURNITURE = [
  [2.2, 2.7, 63.2, 14.7],
  [2.2, 17.4, 31.2, 55],
  [36.5, 19, 64, 37.5],
].map(([l, t, r, b]) => [l * 38.4, t * 21.6, r * 38.4, b * 21.6]);
function collectAtlasLayout(svg) {
  const num = (node, name) => Number(node.getAttribute(name));
  atlasStations = [...svg.querySelectorAll('g[id^="s"][data-name]')].flatMap(
    (group) => {
      const rect = group.querySelector("rect.station-label"),
        dot = group.querySelector("circle");
      if (!rect || !dot) return [];
      const [x, y] = [num(rect, "x"), num(rect, "y")];
      return [
        {
          dot: [num(dot, "cx"), num(dot, "cy")],
          label: [x, y, x + num(rect, "width"), y + num(rect, "height")],
          nodes: group.querySelectorAll(".station-label"),
        },
      ];
    },
  );
}
const overlapArea = (a, b) =>
  Math.max(0, Math.min(a[2], b[2]) - Math.max(a[0], b[0])) *
  Math.max(0, Math.min(a[3], b[3]) - Math.max(a[1], b[1]));
const pillTop = (y, height) =>
  Math.min(2160 - 6 - height, Math.max(6, y - height / 2));
// Slide each covered name plate just clear of the pill, preferring the shortest move.
function clearStationLabels(pills) {
  const current = atlasStations.map((s) => s.label);
  atlasStations.forEach((s, index) => {
    const hits = pills.filter((box) => overlapArea(s.label, box) > 0);
    let shift = [0, 0];
    if (hits.length) {
      const [l, t, r, b] = s.label,
        gap = 8;
      const candidates = hits.flatMap((box) => [
        [box[2] + gap - l, 0],
        [box[0] - gap - r, 0],
        [0, box[1] - gap - b],
        [0, box[3] + gap - t],
      ]);
      let best = Infinity;
      for (const [dx, dy] of candidates) {
        const moved = [l + dx, t + dy, r + dx, b + dy];
        let score = Math.hypot(dx, dy);
        if (moved[0] < 4 || moved[1] < 4 || moved[2] > 3836 || moved[3] > 2156)
          score += 1e6;
        for (const box of [...pills, ...ATLAS_FURNITURE])
          score += overlapArea(moved, box) * 10;
        current.forEach((other, i) => {
          if (i !== index) score += overlapArea(moved, other) * 2;
        });
        for (const other of atlasStations) {
          const [x, y] = other.dot;
          if (other !== s)
            score += overlapArea(moved, [x - 9, y - 9, x + 9, y + 9]) * 5;
        }
        if (score < best) [best, shift] = [score, [dx, dy]];
      }
      current[index] = [l + shift[0], t + shift[1], r + shift[0], b + shift[1]];
    }
    for (const node of s.nodes)
      if (shift[0] || shift[1])
        node.setAttribute("transform", `translate(${shift[0]} ${shift[1]})`);
      else node.removeAttribute("transform");
  });
}
const tagWidth = (text) => Math.max(46, 24 * String(text).length + 22);
function markerPortrait(marker, x, cy) {
  const avatar = appearanceArt(marker.appearance);
  const clipId = `public-avatar-${marker.id}`;
  const half = MARKER_SIZE / 2;
  // Crop the shared brown portrait and its hat together inside a 60-unit frame.
  const frame =
    marker.appearance === "brown"
      ? 'x="-18" y="-6" width="96" height="96" preserveAspectRatio="xMidYMid meet"'
      : 'x="0" y="0" width="60" height="60" preserveAspectRatio="xMidYMid meet"';
  return `<svg class="marker-portrait" x="${x - half}" y="${cy - half}" width="${MARKER_SIZE}" height="${MARKER_SIZE}" viewBox="0 0 60 60" overflow="hidden"><defs><clipPath id="${esc(clipId)}"><circle cx="30" cy="30" r="30"/></clipPath></defs><g clip-path="url(#${esc(clipId)})">${avatar ? `<image href="${esc(avatar)}" ${frame}/>` : ""}${marker.hat ? markerHat(avatar, frame) : ""}</g></svg>`;
}
// A seat number or letter badge over the portrait's lower right, readable from afar.
function markerTagBadge(text, x, cy, appearance) {
  if (!text) return "";
  const width = tagWidth(text),
    left = x + 10,
    top = cy + 6;
  return `<g class="marker-tag" data-appearance="${esc(appearance)}"><rect x="${left}" y="${top}" width="${width}" height="46" rx="23"/><text x="${left + width / 2}" y="${top + 35}" text-anchor="middle">${esc(text)}</text></g>`;
}
function renderPublicMarkers(layer) {
  const groups = new Map();
  for (const marker of state.markers || []) {
    const id = marker.lastArrival.station;
    if (!config.positions[id]) continue;
    if (!groups.has(id)) groups.set(id, []);
    groups.get(id).push(marker);
  }
  const boxes = [];
  layer.innerHTML = [...groups]
    .map(([stationId, markers]) => {
      const brown = markers.filter((m) => m.appearance === "brown");
      const shown = [
        ...(brown.length
          ? [
              {
                ...brown[0],
                count: brown.length,
                labels: brown.map((m) => m.label || "").sort(),
                hat: brown.some((m) => m.hat),
              },
            ]
          : []),
        ...markers.filter((m) => m.appearance === "purple"),
      ];
      const tags = shown.map((m) => {
        const letters =
          m.count > 1 && m.labels.every(Boolean) ? m.labels.join("") : "";
        const tag = m.count > 1 ? letters || `×${m.count}` : markerTag(m);
        return { letters, tag };
      });
      // Badges reach past the pill's right edge; names must clear them too.
      const extra = Math.max(
        0,
        ...tags.map((t) => (t.tag ? tagWidth(t.tag) - 38 : 0)),
      );
      const waitingSeats = new Set(state.pendingSeats || []);
      const [x, y] = config.positions[stationId],
        height = shown.length * MARKER_STEP + 12,
        top = pillTop(y, height),
        half = MARKER_SIZE / 2;
      boxes.push([x - half - 6, top, x + half + 6 + extra, top + height]);
      return `<g class="station-player-pill" data-station="${esc(stationId)}"><rect x="${x - MARKER_SIZE / 2 - 6}" y="${top}" width="${MARKER_SIZE + 12}" height="${height}" rx="${MARKER_SIZE / 2 + 6}"/>${shown
        .map((m, index) => {
          const cy = top + 10 + MARKER_SIZE / 2 + index * MARKER_STEP;
          const { letters, tag } = tags[index];
          const label = `${m.count > 1 ? `棕色旅人 ${letters || `× ${m.count}`}${m.hat ? "（有人戴帽）" : ""}` : sightingLabel(m)} · 最近目击：${station(stationId)}`;
          const avatar = appearanceArt(m.appearance);
          const group = m.count > 1 ? brown : [m];
          const waiting = group.some((g) => waitingSeats.has(g.seat));
          return `<g class="player-marker${waiting ? " is-waiting" : ""}" data-appearance="${m.appearance}" role="img" aria-label="${esc(label)}${waiting ? " · 正在选路" : ""}"><title>${esc(label)}</title>${waiting ? `<circle class="waiting-ring" cx="${x}" cy="${cy}" r="${MARKER_SIZE / 2 + 10}"/>` : ""}<g class="marker-avatar-placeholder" ${avatar ? 'visibility="hidden"' : ""}><circle cx="${x}" cy="${cy}" r="${MARKER_SIZE / 2 - 2}" fill="${m.appearance === "brown" ? "#8f6749" : "#72518a"}"/><text x="${x}" y="${cy + 12}" text-anchor="middle">秘</text></g>${markerPortrait(m, x, cy)}${markerTagBadge(tag, x, cy, m.appearance)}</g>`;
        })
        .join("")}</g>`;
    })
    .join("");
  clearStationLabels(boxes);
}
function renderMarkers(tick = null) {
  const layer = document.querySelector("#live-markers");
  if (!layer || !state) return;
  if (state.phase !== "ended") {
    renderPublicMarkers(layer);
    document.querySelector("#replay-trails").replaceChildren();
    return;
  }
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
  const boxes = [];
  layer.innerHTML = [...groups]
    .map(([stationId, players]) => {
      const [x, y] = config.positions[stationId],
        half = MARKER_SIZE / 2,
        height = players.length * MARKER_STEP + 12,
        top = pillTop(y, height);
      boxes.push([x - half - 6, top, x + half + 14, top + height]);
      return `<g class="station-player-pill" data-station="${esc(stationId)}"><rect x="${x - half - 6}" y="${top}" width="${MARKER_SIZE + 12}" height="${height}" rx="${half + 6}"/>${players
        .sort((a, b) => a.seat - b.seat)
        .map((player, index) => {
          const cy = top + 10 + half + index * MARKER_STEP,
            avatar = roleArt(publicRole(player)),
            clipId = `marker-avatar-${player.id}`;
          const label = `${player.seat}号 · ${names[player.role] || "身份未公开"} · ${player.name} · 最近到站：${station(stationId)}`;
          return `<g class="player-marker" data-player="${player.id}" role="img" aria-label="${esc(label)}"><title>${esc(label)}</title><defs><clipPath id="${clipId}"><circle cx="${x}" cy="${cy}" r="${half}"/></clipPath></defs><text x="${x}" y="${cy + 13}" text-anchor="middle" font-size="36" fill="currentColor">秘</text>${avatar ? `<image href="${esc(avatar)}" x="${x - half}" y="${cy - half}" width="${MARKER_SIZE}" height="${MARKER_SIZE}" preserveAspectRatio="xMidYMid meet" clip-path="url(#${clipId})"/>` : ""}${markerTagBadge(player.seat, x, cy, "seat")}</g>`;
        })
        .join("")}</g>`;
    })
    .join("");
  clearStationLabels(boxes);
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
        ["#c48122", "#9754a9", "#215669", "#a55332", "#63845d", "#ad7292"][
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
          : type === "eta"
            ? rail(etaTick())
            : rail((state.railMinutes ?? state.tick * 15) / 15);
  });
  const m = state.me?.motion;
  if (m) {
    const you = document.querySelector(".line-map .lm-you");
    const point = you && travelPoint(m);
    if (point) you.setAttribute("transform", `translate(${point[0]} ${point[1]})`);
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
        ? "行程暂停"
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
        me.hat,
        me.renkoLastArrival,
        me.availableLines,
        me.hand?.map((h) => h.id),
        me.encounter,
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
    s.pendingCount,
    s.pendingSeats,
    s.markers,
    s.arrivals,
    s.canForceContinue,
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
      seenArrivalKeys.add(
        event.id || `${event.player}:${event.station}:${event.tick}`,
      );
    arrivalSoundInitialized = true;
  } else if (sound) {
    for (const event of arrivals) {
      const key = event.id || `${event.player}:${event.station}:${event.tick}`;
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
      const tag = '<i class="avatar-hat-tag" aria-hidden="true">帽</i>';
      if (e.target.classList.contains("avatar-hat")) {
        e.target.outerHTML = tag;
        return;
      }
      e.target.hidden = true;
      const parent = e.target.closest(".role-icon, .public-avatar");
      if (parent) {
        const hat = parent.querySelector(".avatar-hat, .avatar-hat-tag");
        parent.textContent =
          parent.dataset.appearance === "brown"
            ? "棕"
            : parent.dataset.appearance === "purple"
              ? "紫"
              : "秘";
        if (hat) parent.insertAdjacentHTML("beforeend", tag);
      }
    } else if (e.target.tagName?.toLowerCase() === "image") {
      e.target.style.display = "none";
      const marker = e.target.closest(".player-marker");
      if (!e.target.classList.contains("hat-overlay")) {
        marker
          ?.querySelector(".marker-avatar-placeholder")
          ?.removeAttribute("visibility");
        const hat = marker?.querySelector(".hat-overlay");
        if (hat) hat.style.display = "none";
      }
      marker?.querySelector(".hat-fallback")?.removeAttribute("visibility");
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
        caption.textContent = `${replayTick >= max ? "回放结束" : "行程回放"} / ${rail(replayTick)} / 彩色线条为本局行程`;
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
    } else if (kind === "confirm-encounter") {
      await command("confirm_encounter", {
        encounter: Number(button.dataset.encounter),
      });
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
      document.querySelector(".station-axis")?.scrollBy({
        top: 130,
        behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
          ? "instant"
          : "smooth",
      });
    } else if (kind === "continue") {
      await command("continue");
    } else if (kind === "continue-force") {
      await command("continue", { force: true });
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
