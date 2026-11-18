// Liessure front end: an endless feed of things that are better than a scroll.
"use strict";

const LABELS = {
  wikipedia: "Wikipedia", news: "News", game: "Games", video: "Videos", movie: "Movies",
  show: "Series", song: "Music", photo: "Photos", language: "Telugu", homelab: "Home lab",
  job: "Jobs", skill: "Skills", telemetry: "Lab health", home: "Home",
};
const android = window.LiessureAndroid || null; // injected by the Android app
const feed = document.getElementById("feed");
const sentinel = document.getElementById("sentinel");
const chips = document.getElementById("chips");
let filter = new Set();
let loading = false;
let generation = 0;
let configured = [];
let offline = false;
let shown = new Set(); // keys of cards on screen, so saved ones aren't repeated

// The last cards the server sent, kept so there's something to read when the
// network drops (see sw.js for the page and images).
const SAVED_KEY = "liessure.lastCards";
const SAVED_MAX = 60;
const cardKey = (c) => c.kind + ":" + (c.ref || c.url);
function savedCards() {
  try { return JSON.parse(localStorage.getItem(SAVED_KEY)) || []; } catch { return []; }
}
function saveCards(cards) {
  const keys = new Set(cards.map(cardKey));
  const kept = [...cards, ...savedCards().filter((c) => !keys.has(cardKey(c)))].slice(0, SAVED_MAX);
  try { localStorage.setItem(SAVED_KEY, JSON.stringify(kept)); } catch {}
}

const params = new URLSearchParams(location.search);
if (params.get("from") === "reddit") document.getElementById("banner").hidden = false;

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text) e.textContent = text;
  return e;
}

function report(c, action) {
  if (!c.ref) return;
  const body = JSON.stringify({ kind: c.kind, ref: c.ref, action, title: c.title });
  fetch("/api/event", { method: "POST", headers: { "content-type": "application/json" }, body, keepalive: true }).catch(() => {});
}

function openUrl(url) {
  if (android) android.openUrl(url);
  else window.open(url, "_blank", "noopener");
}

function renderCard(c) {
  const card = el("article", "card " + c.kind);
  if (c.image) {
    const img = el("img", "media");
    img.loading = "lazy";
    img.alt = "";
    img.src = c.image;
    img.onerror = () => img.remove();
    card.append(img);
  }
  const body = el("div", "body");
  body.append(el("div", "label", c.label));
  body.append(el("h2", "title", c.title));
  if (c.subtitle) body.append(el("div", "subtitle", c.subtitle));
  if (c.why) body.append(el("p", "why", c.why));
  if (c.blurb) {
    const p = el("p", "blurb clamp", c.blurb);
    p.onclick = () => p.classList.toggle("clamp");
    body.append(p);
  }

  const actions = el("div", "actions");
  if (c.embed && c.embed.src) {
    const play = el("button", "primary", "▶ " + (c.embed.label || "Play here"));
    play.onclick = () => { playInline(card, c.embed); play.remove(); report(c, "play"); };
    actions.append(play);
  }
  const go = el("a", c.embed && c.embed.src ? "" : "primary", "Open");
  go.href = c.url;
  go.target = "_blank";
  go.rel = "noopener";
  go.onclick = (e) => { e.preventDefault(); report(c, "open"); openUrl(c.url); };
  actions.append(go);
  for (const a of c.actions || []) {
    if (a.app) {
      if (!android) continue; // app launch only makes sense on the phone
      const b = el("button", "", a.label);
      b.onclick = () => android.launchApp(a.app);
      actions.append(b);
    } else if (a.url) {
      if (a.android && !android) continue; // e.g. argosy:// only means something on the phone
      const l = el("a", "", a.label);
      l.href = a.url;
      l.onclick = (e) => { e.preventDefault(); openUrl(a.url); };
      actions.append(l);
    }
  }
  body.append(actions);
  card.append(body);

  if (c.image) card.querySelector(".media").onclick = () => { report(c, "open"); openUrl(c.url); };
  return card;
}

// Only one thing plays at a time, like a feed should.
let playing = null;
function playInline(card, embed) {
  if (playing && playing !== card) stopInline(playing);
  playing = card;
  let player;
  if (embed.type === "audio") {
    player = el("audio", "player");
    player.controls = true;
    player.autoplay = true;
    player.src = embed.src;
  } else {
    player = el("div", "player video");
    const f = document.createElement("iframe");
    f.src = embed.src;
    f.allow = "autoplay; fullscreen; picture-in-picture";
    f.allowFullscreen = true;
    player.append(f);
  }
  const media = card.querySelector(".media");
  if (embed.type === "video" && media) media.replaceWith(player);
  else card.querySelector(".body").append(player);
}
function stopInline(card) {
  card.querySelectorAll("audio").forEach((a) => a.pause());
  card.querySelectorAll(".player.video").forEach((v) => v.remove());
}

async function load(n = 6) {
  if (loading) return;
  loading = true;
  const gen = generation;
  try {
    const q = new URLSearchParams({ n });
    if (filter.size) q.set("kinds", [...filter].join(","));
    const r = await fetch("/api/feed?" + q);
    if (!r.ok) throw new Error(r.status);
    const { cards } = await r.json();
    if (gen !== generation) return;
    offline = false;
    if (!cards.length && !feed.children.length) {
      feed.append(el("div", "empty", "Nothing came back. Pull ↻ to try again."));
    }
    saveCards(cards);
    for (const c of cards) show(c);
  } catch (e) {
    if (gen === generation) {
      if (!offline) showSaved();
      offline = true;
    }
  } finally {
    loading = false;
  }
  // Keep filling until the sentinel is off screen (not while offline: that would spin).
  if (gen === generation && !offline && sentinel.getBoundingClientRect().top < innerHeight + 400) load();
}

function show(c) {
  shown.add(cardKey(c));
  feed.append(renderCard(c));
}

// Offline: whatever was saved last time that isn't on screen yet.
function showSaved() {
  const rest = savedCards().filter((c) => !shown.has(cardKey(c)) && (!filter.size || filter.has(c.kind)));
  if (!rest.length) {
    if (!feed.children.length) feed.append(el("div", "empty", "Couldn't reach Liessure. ↻ to retry."));
    return;
  }
  feed.append(el("div", "offline", "Offline. Here's what you had last time; new cards come back with the connection."));
  for (const c of rest) show(c);
}

function reset() {
  generation++;
  loading = false;
  offline = false;
  shown = new Set();
  feed.replaceChildren();
  scrollTo({ top: 0 });
  load();
}

function renderChips() {
  chips.replaceChildren();
  const all = el("button", "chip", "Everything");
  all.setAttribute("aria-pressed", String(filter.size === 0));
  all.onclick = () => { filter.clear(); renderChips(); reset(); };
  chips.append(all);
  for (const k of configured) {
    const b = el("button", "chip", LABELS[k] || k);
    b.setAttribute("aria-pressed", String(filter.has(k)));
    b.onclick = () => {
      filter.has(k) ? filter.delete(k) : filter.add(k);
      renderChips();
      reset();
    };
    chips.append(b);
  }
}

document.getElementById("refresh").onclick = reset;
addEventListener("online", () => { if (offline) reset(); });
new IntersectionObserver((entries) => {
  if (entries.some((e) => e.isIntersecting)) load();
}, { rootMargin: "600px" }).observe(sentinel);

fetch("/api/sources").then((r) => r.json()).then(({ language, sources }) => {
  configured = Object.keys(sources).filter((k) => sources[k].configured && sources[k].weight > 0);
  if (language) LABELS.language = language;
  renderChips();
}).catch(() => {});
load();

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
