// Detour front end: an endless feed of things that are better than a scroll.
"use strict";

const LABELS = {
  wikipedia: "Wikipedia", game: "Games", video: "Videos", movie: "Movies",
  show: "Series", song: "Music", photo: "Photos", language: "Telugu",
};
const android = window.DetourAndroid || null; // injected by the Android app
const feed = document.getElementById("feed");
const sentinel = document.getElementById("sentinel");
const chips = document.getElementById("chips");
let filter = new Set();
let loading = false;
let generation = 0;
let configured = [];

const params = new URLSearchParams(location.search);
if (params.get("from") === "reddit") document.getElementById("banner").hidden = false;

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text) e.textContent = text;
  return e;
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
  if (c.blurb) {
    const p = el("p", "blurb clamp", c.blurb);
    p.onclick = () => p.classList.toggle("clamp");
    body.append(p);
  }

  const actions = el("div", "actions");
  if (c.embed && c.embed.src) {
    const play = el("button", "primary", "▶ " + (c.embed.label || "Play here"));
    play.onclick = () => { playInline(card, c.embed); play.remove(); };
    actions.append(play);
  }
  const go = el("a", c.embed && c.embed.src ? "" : "primary", "Open");
  go.href = c.url;
  go.target = "_blank";
  go.rel = "noopener";
  go.onclick = (e) => { e.preventDefault(); openUrl(c.url); };
  actions.append(go);
  for (const a of c.actions || []) {
    if (a.app) {
      if (!android) continue; // app launch only makes sense on the phone
      const b = el("button", "", a.label);
      b.onclick = () => android.launchApp(a.app);
      actions.append(b);
    } else if (a.url) {
      const l = el("a", "", a.label);
      l.href = a.url;
      l.onclick = (e) => { e.preventDefault(); openUrl(a.url); };
      actions.append(l);
    }
  }
  body.append(actions);
  card.append(body);

  if (c.image) card.querySelector(".media").onclick = () => openUrl(c.url);
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
    if (!cards.length && !feed.children.length) {
      feed.append(el("div", "empty", "Nothing came back. Pull ↻ to try again."));
    }
    for (const c of cards) feed.append(renderCard(c));
  } catch (e) {
    if (!feed.children.length) feed.append(el("div", "empty", "Couldn't reach Detour. ↻ to retry."));
  } finally {
    loading = false;
  }
  // Keep filling until the sentinel is off screen.
  if (gen === generation && sentinel.getBoundingClientRect().top < innerHeight + 400) load();
}

function reset() {
  generation++;
  loading = false;
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
