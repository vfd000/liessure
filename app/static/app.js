// Liessure front end: an endless feed of things that are better than a scroll.
"use strict";

const LABELS = {
  wikipedia: "Wikipedia", news: "News", game: "Games", video: "Videos", movie: "Movies",
  show: "Series", song: "Music", photo: "Photos", language: "Telugu", homelab: "Home lab",
  job: "Jobs", skill: "Skills", telemetry: "Lab health", home: "Home", math: "Math", recall: "Recall", todo: "To do", infosec: "Infosec", bite: "Bite-size", lesson: "Lessons", career: "Careers", kquiz: "K-drama",
};
const android = window.LiessureAndroid || null; // injected by the Android app
// The page's own words in the reader's language (/api/me's lang); cards carry their own.
let UI_LANG = "en";
const UI = {
  es: {
    "Open": "Abrir", "Details": "Detalles", "Hide details": "Ocultar", "Hide": "Ocultar", "Play here": "Reproducir",
    "Everything": "Todo", "Check": "Comprobar", "Hint": "Pista", "Show answer": "Ver respuesta",
    "Scratch pad": "Borrador", "Help me fix it": "Ayúdame", "Your answer": "Tu respuesta",
    "Wikipedia": "Wikipedia", "News": "Noticias", "Videos": "Vídeos", "Movies": "Películas", "Series": "Series",
    "Music": "Música", "Photos": "Fotos", "Math": "Mates", "Recall": "Repaso", "Bite-size": "Bocados",
    "Lessons": "Lecciones", "Home": "Casa", "Languages": "Idiomas", "Careers": "Carreras",
    "Not interested": "No me interesa", "Right!": "¡Correcto!", "Not quite.": "Casi, pero no.",
    "Right. You remembered it.": "Correcto. Lo recordaste.", "Not quite. It'll come back sooner.": "Casi. Volverá pronto.",
    "K-drama": "K-drama", "Hide everything about:": "Ocultar todo sobre:", "Hide": "Ocultar",
    "a word, e.g. golf": "una palabra, p. ej. golf", "Just this card": "Solo esta tarjeta",
    "Hidden topics: ": "Temas ocultos: ", "sports": "deportes", "alcohol": "alcohol",
    "animal products": "productos animales",
  },
};
const t = (s) => (UI[UI_LANG] && UI[UI_LANG][s]) || s;
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

let lastCard = null; // the card last opened or played, offered as context for an idea
function report(c, action) {
  lastCard = c;
  if (!c.ref) return;
  // The card's own words go along so it can come back as a recall question later.
  const body = JSON.stringify({ kind: c.kind, ref: c.ref, action, title: c.title, subtitle: c.subtitle || "",
    text: (c.blurb || "").slice(0, 4000), url: c.url || "" });
  fetch("/api/event", { method: "POST", headers: { "content-type": "application/json" }, body, keepalive: true }).catch(() => {});
}

// Google's Translate app claims translate.google.com links and drops the text in them, so on Android
// the text goes to the app the way the text-selection "Translate" does (PROCESS_TEXT); the web link is
// the fallback (newer app versions open it in place when Translate isn't installed).
function openTranslate(tr, url) {
  if (!android) return openUrl(url);
  const e = encodeURIComponent;
  openUrl("intent:#Intent;action=android.intent.action.PROCESS_TEXT;type=text/plain;" +
    "package=com.google.android.apps.translate;B.android.intent.extra.PROCESS_TEXT_READONLY=true;" +
    `S.android.intent.extra.PROCESS_TEXT=${e(tr.text)};S.browser_fallback_url=${e(url)};end`);
}

function openUrl(url, inApp) {
  // Jellyfin's phone app takes no deep links, so the Android app shows its web page itself.
  if (android && inApp && android.openInApp) android.openInApp(url);
  else if (android) android.openUrl(url);
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
  const nope = el("button", "nope", "✕");
  nope.title = nope.ariaLabel = t("Not interested");
  nope.onclick = () => notInterested(c, card);
  body.append(nope);
  body.append(el("div", "label", c.label));
  body.append(el("h2", "title", c.title));
  if (c.subtitle) body.append(el("div", "subtitle", c.subtitle));
  if (c.why) body.append(el("p", "why", c.why));
  if (c.blurb) {
    const p = el("p", "blurb clamp", c.blurb);
    let read = false;
    p.onclick = () => {
      p.classList.toggle("clamp");
      // Reading it in the feed counts too: it can come back as a recall question.
      if (!read && !p.classList.contains("clamp")) { read = true; report(c, "read"); }
    };
    body.append(p);
  }

  if (c.quiz && (c.quiz.choices || c.quiz.answer !== undefined)) body.append(renderQuiz(c));

  const actions = el("div", "actions");
  if (c.more && c.more.url) {
    // Long text (a job description) fetched the first time it's opened, shown as text.
    const box = el("div", "more");
    box.hidden = true;
    const btn = el("button", "", c.more.label || "More");
    let loaded = false;
    btn.onclick = async () => {
      box.hidden = !box.hidden;
      btn.textContent = box.hidden ? (c.more.label || "More") : "Hide";
      if (box.hidden || loaded) return;
      box.textContent = "Loading…";
      try {
        const r = await fetch(c.more.url);
        if (!r.ok) throw new Error(r.status);
        box.textContent = (await r.json()).text || "Nothing to show.";
        loaded = true;
      } catch {
        box.textContent = "Couldn't load it right now.";
      }
    };
    actions.append(btn);
    body.append(box);
  }
  if ((c.details && c.details.length) || (c.photo && c.photo.id)) {
    const more = el("div", "details");
    more.hidden = true;
    if (c.details && c.details.length) {
      const dl = el("dl");
      for (const d of c.details) {
        if (d.url) continue;
        dl.append(el("dt", "", d.label), el("dd", "", d.value));
      }
      more.append(dl);
      const links = el("div", "actions");
      for (const d of c.details.filter((x) => x.url)) {
        const l = el("a", "", d.label);
        l.href = d.url;
        l.onclick = (e) => { e.preventDefault(); openUrl(d.url); };
        links.append(l);
      }
      more.append(links);
    }
    if (c.photo && c.photo.id) {
      const found = el("p", "why");
      const ident = el("button", "", "Identify");
      ident.onclick = async () => {
        ident.disabled = true;
        found.textContent = "Looking at it…";
        try {
          const r = await fetch(`/api/photo/${c.photo.id}/identify`, { method: "POST" });
          if (!r.ok) throw new Error(r.status);
          found.textContent = (await r.json()).text;
        } catch {
          found.textContent = "Couldn't identify it right now.";
          ident.disabled = false;
        }
      };
      const claude = el("button", "", "Ask Claude");
      claude.onclick = () => askClaude(c);
      const row = el("div", "actions");
      row.append(ident, claude);
      more.append(row, found);
    }
    const label = c.details_label || t("Details");
    const toggle = el("button", "", label);
    toggle.onclick = () => { more.hidden = !more.hidden; toggle.textContent = more.hidden ? label : t("Hide details"); };
    actions.append(toggle);
    body.append(more);
  }
  if (c.embed && c.embed.src) {
    const play = el("button", "primary", "▶ " + (c.embed.label || t("Play here")));
    play.onclick = () => { playInline(card, c.embed); play.remove(); report(c, "play"); };
    actions.append(play);
  }
  const go = el("a", (c.embed && c.embed.src) || c.quiz ? "" : "primary", c.open_label || t("Open"));
  go.href = c.url;
  go.target = "_blank";
  go.rel = "noopener";
  go.onclick = (e) => {
    e.preventDefault();
    report(c, "open");
    if (c.translate && c.translate.text) openTranslate(c.translate, c.url); else openUrl(c.url, c.open_in_app);
  };
  actions.append(go);
  for (const a of c.actions || []) {
    if (a.speak) {
      const b = el("button", "", a.label);
      b.onclick = () => speak(a.speak);
      actions.append(b);
      continue;
    }
    if (a.app) {
      if (!android) continue; // app launch only makes sense on the phone
      const b = el("button", "", a.label);
      b.onclick = () => {
        if (a.song && android.playSong) {
          android.playSong(a.app, a.song.title || "", a.song.artist || "", a.song.album || "");
          report(c, "play");
        } else android.launchApp(a.app);
      };
      actions.append(b);
    } else if (a.url) {
      if (a.android && !android) continue; // e.g. argosy:// only means something on the phone
      const l = el("a", "", a.label);
      l.href = a.url;
      l.onclick = (e) => {
        e.preventDefault();
        if (a.assist) assisted(c, a.assist);
        if (a.translate) openTranslate(a.translate, a.url); else openUrl(a.url, a.in_app);
      };
      actions.append(l);
    }
  }
  body.append(actions);
  card.append(body);

  if (c.image) card.querySelector(".media").onclick = () => { report(c, "open"); openUrl(c.url, c.open_in_app); };
  return card;
}

// "Not interested": hide this card, and (optionally) never show its topic again.
const avoidDialog = (() => {
  const d = el("dialog", "ideas");
  d.innerHTML = `<form method="dialog"><h2></h2><p class="why"></p><div class="chips"></div>
    <div class="quiz-row"><input type="text" maxlength="60"><button class="primary" value="add"></button></div>
    <div class="actions"><button value="close"></button></div><p class="why avoid-list"></p></form>`;
  document.body.append(d);
  return d;
})();
async function notInterested(c, cardEl) {
  cardEl.remove();
  const d = avoidDialog;
  d.querySelector("h2").textContent = t("Not interested");
  d.querySelector("p.why").textContent = t("Hide everything about:");
  const input = d.querySelector("input");
  input.value = "";
  input.placeholder = t("a word, e.g. golf");
  d.querySelector('button[value="add"]').textContent = t("Hide");
  d.querySelector('button[value="close"]').textContent = t("Just this card");
  const chips = d.querySelector(".chips");
  const list = d.querySelector(".avoid-list");
  const show = (terms) => { list.textContent = terms.length ? t("Hidden topics: ") + terms.join(", ") : ""; };
  const add = async (term) => {
    const r = await fetch("/api/me/avoid", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ term }) }).catch(() => null);
    if (r && r.ok) show((await r.json()).avoid);
  };
  chips.replaceChildren();
  try {
    const { avoid: terms, suggested } = await (await fetch("/api/me/avoid")).json();
    show(terms);
    for (const s of suggested) {
      const b = el("button", "chip", t(s));
      b.type = "button";
      b.setAttribute("aria-pressed", String(terms.includes(s)));
      b.onclick = async () => { await add(s); b.setAttribute("aria-pressed", "true"); };
      chips.append(b);
    }
  } catch {}
  d.querySelector("form").onsubmit = async (e) => {
    if (e.submitter && e.submitter.value === "add" && input.value.trim()) {
      e.preventDefault();
      await add(input.value.trim());
      input.value = "";
    }
  };
  d.showModal();
}

// Say a phrase: Android's text-to-speech in the app, the browser's voices elsewhere,
// Google Translate (which reads anything aloud) when neither has the language.
function speak({ text, lang, fallback }) {
  if (android && android.speak) return android.speak(text, lang, fallback || "");
  const synth = window.speechSynthesis;
  const base = (lang || "").split("-")[0];
  const voice = synth && synth.getVoices().find((v) => v.lang === lang) ||
    synth && synth.getVoices().find((v) => v.lang.split("-")[0] === base);
  if (!voice) return fallback && openUrl(fallback);
  const u = new SpeechSynthesisUtterance(text);
  u.voice = voice;
  u.lang = voice.lang;
  u.rate = 0.85;
  synth.cancel();
  synth.speak(u);
}

// Hand a photo to Claude: the Claude app (Android share), the share sheet (iPhone, other
// browsers), or a text-only new chat when sharing a file isn't possible.
async function askClaude(c) {
  const p = c.photo || {};
  const where = [p.place, p.lat != null ? `${p.lat.toFixed(4)}, ${p.lon.toFixed(4)}` : ""].filter(Boolean).join(" · ");
  const text = UI_LANG === "es"
    ? `¿Qué hay en esta foto? La tomé${p.when ? " el " + p.when : ""}${where ? " en " + where : ""}. ` +
      "Identifica lo que puedas (lugares, plantas, animales, el tipo de sitio) y cuéntame sobre la zona. Responde en español."
    : `What's in this photo? I took it${p.when ? " on " + p.when : ""}${where ? " at " + where : ""}. ` +
      "Identify what you can (landmarks, plants, animals, the kind of place) and tell me about the area.";
  try {
    const blob = await (await fetch(c.image)).blob();
    if (android && android.shareImage) {
      const b64 = await new Promise((ok, bad) => {
        const r = new FileReader();
        r.onload = () => ok(String(r.result).split(",")[1]);
        r.onerror = bad;
        r.readAsDataURL(blob);
      });
      return android.shareImage(b64, blob.type || "image/jpeg", text);
    }
    const file = new File([blob], "photo.jpg", { type: blob.type || "image/jpeg" });
    if (navigator.canShare && navigator.canShare({ files: [file] })) {
      return await navigator.share({ files: [file], text });
    }
  } catch (e) {
    if (e && e.name === "AbortError") return; // the share sheet was dismissed
  }
  openUrl("https://claude.ai/new?" + new URLSearchParams({ q: text }));
}

// A finger-and-mouse scratch pad, for the working you'd do on paper.
function scratchPad() {
  const wrap = el("div", "scratch");
  const canvas = document.createElement("canvas");
  const bar = el("div", "scratch-bar");
  const clear = el("button", "", "Clear");
  bar.append(el("span", "why", "Scratch pad: work it out here."), clear);
  wrap.append(canvas, bar);
  let ctx, last = null;
  const fit = () => {
    const r = window.devicePixelRatio || 1, w = wrap.clientWidth || 300, h = Math.round(w * 0.6);
    canvas.width = w * r; canvas.height = h * r;
    canvas.style.width = w + "px"; canvas.style.height = h + "px";
    ctx = canvas.getContext("2d");
    ctx.scale(r, r);
    ctx.lineWidth = 2.2; ctx.lineCap = ctx.lineJoin = "round";
    ctx.strokeStyle = getComputedStyle(document.body).color;
  };
  const at = (e) => { const b = canvas.getBoundingClientRect(); return [e.clientX - b.left, e.clientY - b.top]; };
  canvas.onpointerdown = (e) => { if (!ctx) fit(); canvas.setPointerCapture(e.pointerId); last = at(e); };
  canvas.onpointermove = (e) => {
    if (!last) return;
    const p = at(e);
    ctx.beginPath(); ctx.moveTo(...last); ctx.lineTo(...p); ctx.stroke();
    last = p;
  };
  canvas.onpointerup = canvas.onpointercancel = () => { last = null; };
  clear.onclick = fit;
  requestAnimationFrame(fit);
  return wrap;
}

// Help used on a math problem (hint, fix-it, tutor): kept, so progress shows where help was needed.
function assisted(c, kind) {
  const q = c.quiz || {};
  fetch("/api/math/assist", { method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ ref: c.ref, topic: q.topic || "", kind }) }).catch(() => {});
}

// "3/8", "-12", "1,200", "28.3", "$0.05": a number, or NaN.
function parseNum(s) {
  s = String(s).trim().replace(/[\s,$%°]/g, "").replace(/−/g, "-");
  const m = s.match(/^(-?\d+(?:\.\d+)?)\/(-?\d+(?:\.\d+)?)$/);
  if (m) return Number(m[2]) ? Number(m[1]) / Number(m[2]) : NaN;
  return s === "" ? NaN : Number(s);
}

// Answer in place: a typed answer (math) or multiple choice (recall).
function renderQuiz(c) {
  const q = c.quiz;
  const box = el("div", "quiz");
  const result = el("p", "quiz-result");
  const solution = el("p", "why", q.solution || "");
  solution.hidden = true;
  if (q.choices) {
    q.choices.forEach((text, i) => {
      const b = el("button", "choice", text);
      b.onclick = () => {
        const right = i === Number(q.answer);
        box.querySelectorAll(".choice").forEach((x, j) => {
          x.disabled = true;
          if (j === Number(q.answer)) x.classList.add("right");
        });
        if (!right) b.classList.add("wrong");
        result.textContent = q.id
          ? (right ? t("Right. You remembered it.") : t("Not quite. It'll come back sooner."))
          : (right ? t("Right!") : t("Not quite."));
        solution.hidden = !q.solution;
        report(c, "play");
        if (!q.id) return; // only recall questions are scheduled
        fetch(`/api/recall/${q.id}`, { method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify({ correct: right }) })
          .then((r) => r.json())
          .then((s) => {
            result.textContent += ` Next in ${s.next_in_days} day${s.next_in_days === 1 ? "" : "s"}.` +
              (s.answered_30d ? ` ${s.correct_30d} of ${s.answered_30d} right this month.` : "");
          }).catch(() => {});
      };
      box.append(b);
    });
  } else {
    const row = el("div", "quiz-row");
    const input = el("input");
    input.type = "text";
    input.inputMode = "text";
    input.placeholder = t("Your answer");
    input.autocomplete = "off";
    const check = el("button", "primary", t("Check"));
    const hint = el("button", "", t("Hint"));
    const show = el("button", "", t("Show answer"));
    if (q.scratch) {
      const pad = el("button", "", "Scratch pad");
      pad.onclick = () => { pad.remove(); box.insertBefore(scratchPad(), result); };
      row.append(pad);
    }
    let tries = 0;
    let lastWrong = "";
    // A wrong answer: the likely mistake and the steps through it, from the household LLM.
    const fix = el("button", "fix", "Help me fix it");
    const help = el("p", "why help");
    fix.onclick = async () => {
      assisted(c, "fix");
      fix.disabled = true;
      help.textContent = "Working out where it went wrong…";
      box.insertBefore(help, result.nextSibling);
      try {
        const r = await fetch("/api/math/help", { method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify({ question: c.title, answer: String(q.answer), theirs: lastWrong,
            solution: q.solution || "" }) });
        if (!r.ok) throw new Error(r.status);
        help.textContent = (await r.json()).help;
      } catch {
        help.textContent = "Couldn't get help right now. Try Tutor me, or the hint.";
      }
      fix.disabled = false;
    };
    // Each answered (or revealed) math card is kept server-side; the reply is the running tally.
    const progress = (correct, revealed) => {
      if (!q.topic) return;
      fetch("/api/math", { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ ref: c.ref, topic: q.topic, correct, tries, revealed }) })
        .then((r) => r.json())
        .then((st) => {
          const bits = [`Today ${st.today.right}/${st.today.tried}`, `this week ${st.week.right}/${st.week.tried}`];
          if (st.streak_days) bits.push(`${st.streak_days}-day streak`);
          if (st.today.helped) bits.push(`help on ${st.today.helped} today`);
          if (st.weakest) bits.push(`weakest lately: ${st.weakest}`);
          if (st.most_help && st.most_help !== st.weakest) bits.push(`most help: ${st.most_help}`);
          box.append(el("p", "why", bits.join(" · ")));
        }).catch(() => {});
    };
    check.onclick = () => {
      const got = parseNum(input.value), want = parseNum(q.answer);
      if (Number.isNaN(got)) { result.textContent = "Type a number (fractions like 3/8 are fine)."; return; }
      tries++;
      if (Math.abs(got - want) <= Math.max(Number(q.tolerance) || 0, 1e-9 * Math.max(1, Math.abs(want)))) {
        result.textContent = tries === 1 ? "Correct, first try." : `Correct, on try ${tries}.`;
        solution.hidden = false;
        check.disabled = input.disabled = true;
        report(c, "play");
        progress(true, false);
      } else {
        result.textContent = "Not that. Try again, or get help fixing it.";
        lastWrong = input.value.trim();
        if (!row.querySelector(".fix")) row.append(fix);
      }
    };
    input.onkeydown = (e) => { if (e.key === "Enter") check.onclick(); };
    hint.onclick = () => { result.textContent = q.hint; assisted(c, "hint"); };
    show.onclick = () => {
      result.textContent = `Answer: ${q.answer}`;
      solution.hidden = false;
      if (!check.disabled) progress(false, true);
      check.disabled = input.disabled = true;
    };
    row.append(input, check, hint, show);
    box.append(row);
  }
  box.append(result, solution);
  return box;
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
    if (r.status === 401) { location.href = "/auth/login"; return; } // signed out (or the session ended)
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
  const all = el("button", "chip", t("Everything"));
  all.setAttribute("aria-pressed", String(filter.size === 0));
  all.onclick = () => { filter.clear(); renderChips(); reset(); };
  chips.append(all);
  for (const k of configured) {
    const b = el("button", "chip", t(LABELS[k] || k));
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

// Pull down at the top for a fresh feed (the ↻ button does the same). Safari tabs already reload on a
// pull, so this is for the Android app and home-screen web apps.
(() => {
  const standalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone;
  if (!android && !standalone) return;
  const ptr = document.getElementById("ptr");
  const NEED = 70;
  let startY = null, pulled = 0;
  const show = (d) => {
    ptr.style.transform = `translateY(${Math.min(d, 90) - 50}px)`;
    ptr.style.opacity = String(Math.min(d / NEED, 1));
    ptr.textContent = d >= NEED ? "↻ Release to refresh" : "↓ Pull to refresh";
  };
  addEventListener("touchstart", (e) => {
    // Not from things that scroll or draw sideways (code blocks, the math scratch pad).
    startY = scrollY <= 0 && !e.target.closest("canvas, pre, input, textarea") ? e.touches[0].clientY : null;
    pulled = 0;
  }, { passive: true });
  addEventListener("touchmove", (e) => {
    if (startY === null) return;
    pulled = (e.touches[0].clientY - startY) * 0.5;
    if (pulled > 0 && scrollY <= 0) show(pulled); else { pulled = 0; show(0); }
  }, { passive: true });
  addEventListener("touchend", () => {
    if (startY !== null && pulled >= NEED) reset();
    startY = null;
    pulled = 0;
    ptr.style.transition = "transform .2s, opacity .2s";
    show(0);
    setTimeout(() => { ptr.style.transition = ""; }, 220);
  }, { passive: true });
})();

// Signed in with SSO: say who, and offer sign-out.
fetch("/api/me").then((r) => r.json()).then((me) => {
  if (!me.sso) return;
  const out = el("a", "icon signout", "⎋");
  out.href = "/auth/logout";
  out.title = out.ariaLabel = `Sign out${me.name ? " " + me.name : ""}`;
  document.getElementById("refresh").before(out);
}).catch(() => {});

// Ideas: jot an improvement down without leaving the feed; they're kept on the server (/api/ideas).
const ideas = document.getElementById("ideas");
const ideaText = document.getElementById("idea-text");
const ideaStatus = document.getElementById("idea-status");
function showIdeas() {
  const list = document.getElementById("idea-list");
  fetch("/api/ideas").then((r) => r.json()).then(({ ideas: all }) => {
    list.replaceChildren(...all.slice(0, 8).map((i) => el("li", i.status, i.text + (i.status !== "new" ? ` (${i.status})` : ""))));
  }).catch(() => list.replaceChildren());
}
document.getElementById("idea").onclick = () => {
  const row = document.getElementById("idea-about-row");
  row.hidden = !lastCard;
  if (lastCard) document.getElementById("idea-about-title").textContent = `${lastCard.label}: ${lastCard.title}`;
  ideaStatus.hidden = true;
  showIdeas();
  ideas.showModal();
  ideaText.focus();
};
document.getElementById("idea-close").onclick = () => ideas.close();
document.getElementById("idea-form").onsubmit = async (e) => {
  e.preventDefault();
  const text = ideaText.value.trim();
  if (!text) return;
  const about = lastCard && document.getElementById("idea-about").checked
    ? `${lastCard.kind}: ${lastCard.title}` : "";
  ideaStatus.hidden = false;
  try {
    const r = await fetch("/api/ideas", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ text, about }) });
    if (!r.ok) throw new Error(r.status);
    ideaText.value = "";
    ideaStatus.textContent = "Saved. It's on the list to build.";
    showIdeas();
  } catch {
    ideaStatus.textContent = "Couldn't save it (offline?). Your text is still here.";
  }
};
addEventListener("online", () => { if (offline) reset(); });
new IntersectionObserver((entries) => {
  if (entries.some((e) => e.isIntersecting)) load();
}, { rootMargin: "600px" }).observe(sentinel);

const whoami = fetch("/api/me").then((r) => r.json()).then((me) => { UI_LANG = me.lang || "en"; return me; })
  .catch(() => ({}));
whoami.then(() => fetch("/api/sources")).then((r) => r.json()).then(({ language, sources }) => {
  configured = Object.keys(sources).filter((k) => sources[k].configured && sources[k].weight > 0);
  if (language) LABELS.language = language;
  renderChips();
}).catch(() => {});
whoami.then(() => load());

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
