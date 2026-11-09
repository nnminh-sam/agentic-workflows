/* learning-kit quiz app. One app for every session mode (gate / practice / review) and every
 * question type. To add a type: add an entry to TYPES with render(item, saved) -> {el, collect}.
 * collect() returns a response object, or null when the answer is incomplete. */
"use strict";

const app = document.getElementById("app");
const S = { session: null, idx: 0, answers: {}, shownAt: 0, view: "intro" };

// ------------------------------------------------------------------ utils
const h = (tag, attrs = {}, ...kids) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false)
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return el;
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function inlineMd(s) {
  return esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
}
function md(src) {
  const out = [];
  const parts = String(src ?? "").split(/```[^\n]*\n([\s\S]*?)```/g);
  parts.forEach((part, i) => {
    if (i % 2 === 1) { out.push(`<pre><code>${esc(part.replace(/\n$/, ""))}</code></pre>`); return; }
    for (const block of part.split(/\n{2,}/)) {
      const b = block.trim();
      if (!b) continue;
      const lines = b.split("\n");
      if (lines.every((l) => /^\s*[-*] /.test(l))) out.push("<ul>" + lines.map((l) => `<li>${inlineMd(l.replace(/^\s*[-*] /, ""))}</li>`).join("") + "</ul>");
      else if (lines.every((l) => /^\s*\d+[.)] /.test(l))) out.push("<ol>" + lines.map((l) => `<li>${inlineMd(l.replace(/^\s*\d+[.)] /, ""))}</li>`).join("") + "</ol>");
      else out.push(`<p>${lines.map(inlineMd).join("<br>")}</p>`);
    }
  });
  return out.join("");
}
const plain = (s) => String(s ?? "").replace(/```[\s\S]*?```/g, "[code]").replace(/\{\{\d+\}\}/g, "___")
  .replace(/\*\*|`/g, "").replace(/(^|\s)\*([^*]+)\*/g, "$1$2").replace(/\s+/g, " ").trim();
const pct = (x) => `${Math.round((x || 0) * 100)}%`;
const store = {
  get(k, d) { try { const v = localStorage.getItem("lk:" + k); return v === null ? d : v; } catch { return d; } },
  set(k, v) { try { localStorage.setItem("lk:" + k, v); } catch { /* storage unavailable */ } },
};

async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
  return j;
}

// ------------------------------------------------------------------ theme
(function theme() {
  const root = document.documentElement;
  const saved = store.get("theme", "");
  if (saved) root.dataset.theme = saved;
  document.getElementById("theme").onclick = () => {
    const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    store.set("theme", root.dataset.theme);
  };
})();

// ------------------------------------------------------------------ platform
const PLATFORM = String(navigator.userAgentData?.platform || navigator.platform || navigator.userAgent || "").toLowerCase();
const IS_MAC = /mac|iphone|ipad/.test(PLATFORM);
const IS_WIN = /win/.test(PLATFORM);
const MOD = IS_MAC ? "⌘" : "Ctrl";
const OS_DICTATION = IS_WIN ? "Win+H" : IS_MAC ? "macOS Dictation (press Fn twice, or Edit → Start Dictation)"
  : "your desktop's dictation tool";

// ------------------------------------------------------------------ speech (dictation)
// Web Speech works in Chrome, Edge and Safari. Firefox and most Linux Chromium builds don't have it,
// so the OS dictation tool is the fallback (it types into the focused textarea anyway).
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
function micTools(textarea) {
  const tools = h("div", { class: "tools" });
  const nextHint = h("span", { class: "hint" }, `${MOD}+Enter = next`);
  if (!SR) { tools.append(h("span", { class: "hint" }, `Dictation: use ${OS_DICTATION}. This browser has no speech API.`), nextHint); return tools; }
  let rec = null;
  const lang = h("select", { "aria-label": "Dictation language" },
    ...[["en-US", "English"], ["vi-VN", "Tiếng Việt"]].map(([v, t]) => h("option", { value: v }, t)));
  lang.value = store.get("speech", S.session.speech_lang || "en-US");
  lang.onchange = () => store.set("speech", lang.value);
  const btn = h("button", { class: "mic", type: "button" }, "🎙 Dictate");
  btn.onclick = () => {
    if (rec) { rec.stop(); return; }
    rec = new SR(); rec.lang = lang.value; rec.continuous = true; rec.interimResults = false;
    rec.onresult = (ev) => {
      for (let i = ev.resultIndex; i < ev.results.length; i++) if (ev.results[i].isFinal) {
        const t = ev.results[i][0].transcript.trim();
        textarea.value += (textarea.value && !/\s$/.test(textarea.value) ? " " : "") + t;
        textarea.dispatchEvent(new Event("input"));
      }
    };
    rec.onend = () => { rec = null; btn.classList.remove("on"); btn.textContent = "🎙 Dictate"; };
    rec.onerror = (e) => {
      btn.title = e.error;
      if (["network", "not-allowed", "service-not-allowed", "language-not-supported"].includes(e.error))
        nextHint.textContent = `Browser dictation unavailable (${e.error}). Use ${OS_DICTATION}. ${MOD}+Enter = next`;
    };
    try { rec.start(); } catch (err) { rec = null; nextHint.textContent = `Dictation failed to start. Use ${OS_DICTATION}.`; return; }
    btn.classList.add("on"); btn.textContent = "■ Stop";
  };
  tools.append(btn, lang, nextHint);
  return tools;
}

// ------------------------------------------------------------------ question types
const TYPES = {
  mcq: {
    label: "Single choice",
    render(item, saved) {
      const name = "o" + Math.random().toString(36).slice(2);
      const wrap = h("div", { class: "options", role: "radiogroup" });
      item.options.forEach((o, i) => {
        const input = h("input", { type: "radio", name, value: o.key, checked: saved?.choice === o.key });
        const lab = h("label", { class: "opt" + (saved?.choice === o.key ? " sel" : "") }, input,
          h("span", { class: "key" }, i + 1), h("span", { html: inlineMd(o.text) }));
        input.onchange = () => { wrap.querySelectorAll(".opt").forEach((x) => x.classList.remove("sel")); lab.classList.add("sel"); changed(); };
        wrap.append(lab);
      });
      return {
        el: wrap,
        key: (n) => { const r = wrap.querySelectorAll("input")[n - 1]; if (r) { r.checked = true; r.dispatchEvent(new Event("change")); } },
        collect: () => { const c = wrap.querySelector("input:checked"); return c ? { choice: Number(c.value) } : null; },
      };
    },
  },
  multi: {
    label: "Select all that apply",
    render(item, saved) {
      const wrap = h("div", { class: "options" });
      const sel = new Set(saved?.choices || []);
      item.options.forEach((o, i) => {
        const input = h("input", { type: "checkbox", value: o.key, checked: sel.has(o.key) });
        const lab = h("label", { class: "opt" + (sel.has(o.key) ? " sel" : "") }, input,
          h("span", { class: "key" }, i + 1), h("span", { html: inlineMd(o.text) }));
        input.onchange = () => { lab.classList.toggle("sel", input.checked); changed(); };
        wrap.append(lab);
      });
      return {
        el: wrap,
        key: (n) => { const r = wrap.querySelectorAll("input")[n - 1]; if (r) { r.checked = !r.checked; r.dispatchEvent(new Event("change")); } },
        collect: () => {
          const c = [...wrap.querySelectorAll("input:checked")].map((x) => Number(x.value));
          return c.length ? { choices: c } : null;
        },
      };
    },
  },
  order: {
    label: "Put in order",
    render(item, saved) {
      const steps = saved?.order?.length === item.steps.length ? [...saved.order] : [...item.steps];
      const ul = h("ul", { class: "order" });
      let touched = !!saved?.order;
      let dragFrom = null;
      const draw = () => {
        ul.replaceChildren(...steps.map((s, i) => {
          const li = h("li", { draggable: "true" }, h("span", { class: "pos" }, i + 1), h("span", { class: "txt", html: inlineMd(s) }),
            h("button", { type: "button", "aria-label": "Move up", onclick: () => move(i, i - 1) }, "↑"),
            h("button", { type: "button", "aria-label": "Move down", onclick: () => move(i, i + 1) }, "↓"));
          li.ondragstart = () => { dragFrom = i; li.classList.add("drag"); };
          li.ondragend = () => li.classList.remove("drag");
          li.ondragover = (e) => e.preventDefault();
          li.ondrop = (e) => { e.preventDefault(); if (dragFrom !== null) move(dragFrom, i); dragFrom = null; };
          return li;
        }));
      };
      const move = (a, b) => {
        if (b < 0 || b >= steps.length) return;
        const [x] = steps.splice(a, 1); steps.splice(b, 0, x); touched = true; draw(); changed();
      };
      draw();
      const box = h("div", {}, ul, h("p", { class: "hint" }, "Drag, or use ↑ ↓. Top = first."),
        h("button", { type: "button", class: "btn ghost small", onclick: () => { touched = true; changed(); } }, "This order is my answer"));
      return { el: box, collect: () => (touched ? { order: [...steps] } : null) };
    },
  },
  cloze: {
    label: "Fill in the blanks",
    ownsPrompt: true,
    render(item, saved) {
      const vals = saved?.blanks || [];
      const box = h("div", { class: "cloze prompt" });
      const inputs = [];
      const html = esc(item.prompt).replace(/\n/g, "<br>").replace(/\{\{(\d+)\}\}/g, (_, n) => `<span data-blank="${n}"></span>`);
      box.innerHTML = html.replace(/`([^`]+)`/g, "<code>$1</code>");
      box.querySelectorAll("[data-blank]").forEach((sp) => {
        const n = Number(sp.dataset.blank);
        const inp = h("input", { type: "text", "aria-label": `Blank ${n}`, value: vals[n - 1] || "", autocomplete: "off", spellcheck: "false" });
        inp.oninput = changed;
        inputs[n - 1] = inp;
        sp.replaceWith(inp);
      });
      return {
        el: box, focus: () => inputs[0]?.focus(),
        collect: () => { const b = inputs.map((i) => i.value); return b.some((v) => v.trim()) ? { blanks: b } : null; },
      };
    },
  },
  predict: { label: "Predict the output", render: (item, saved) => freeText(saved, "What happens, and why? Reason it through before you run anything.") },
  explain: { label: "Explain", render: (item, saved) => freeText(saved, "Explain in your own words. Write or dictate; aim for the why, not just the what.") },
};

function freeText(saved, placeholder) {
  const ta = h("textarea", { placeholder, spellcheck: "true" });
  ta.value = saved?.text || "";
  ta.oninput = changed;
  return { el: h("div", { class: "free" }, ta, micTools(ta)), focus: () => ta.focus(), collect: () => (ta.value.trim() ? { text: ta.value } : null) };
}

// ------------------------------------------------------------------ views
let current = null; // { widget, confidence }

function setProgress() {
  const n = S.session.items.length;
  const done = S.session.items.filter((i) => S.answers[i.uid]).length;
  document.getElementById("progress-fill").style.width = `${(done / n) * 100}%`;
  document.getElementById("progress-text").textContent = `${done}/${n} answered`;
}

const MODE_INFO = {
  gate: ["Mastery gate", "Pass bar: 80% overall, and every core concept needs a correct explain/predict answer. Below the bar you get a targeted reteach and a fresh round."],
  practice: ["Practice", "No stakes: nothing here changes your topic stage or schedule. Mistakes still go to your mistakes log."],
  review: ["Spaced recall", "Questions are due today across your topics, often reworded. Your answers set when each one comes back."],
};

function viewIntro() {
  S.view = "intro";
  const s = S.session;
  const [mname, mdesc] = MODE_INFO[s.mode];
  const answered = s.items.filter((i) => S.answers[i.uid]).length;
  const types = {};
  s.items.forEach((i) => (types[i.type] = (types[i.type] || 0) + 1));
  app.replaceChildren(h("div", { class: "card" },
    h("div", { class: "meta" }, h("span", { class: "chip" }, mname), s.round ? h("span", { class: "chip" }, `round ${s.round}`) : null),
    h("h1", {}, s.title),
    h("p", { class: "muted" }, mdesc),
    h("p", {}, `${s.items.length} questions: `, Object.entries(types).map(([t, n]) => `${n} ${TYPES[t]?.label.toLowerCase() || t}`).join(", "), "."),
    h("p", { class: "muted small" }, "Answer from memory: no notes, no searching. After each answer, say how sure you are. Being sure and wrong is the most useful thing to find out. If you don't know, press \"I don't know\". It's honest and better than a guess."),
    h("div", { class: "actions" }, h("span"), h("div", { class: "right" },
      h("button", { class: "btn primary", onclick: () => go(firstUnanswered()) }, answered ? `Resume (${answered}/${s.items.length})` : "Start")))));
}

function firstUnanswered() {
  const i = S.session.items.findIndex((it) => !S.answers[it.uid]);
  return i < 0 ? S.session.items.length : i;
}

function go(i) {
  if (i >= S.session.items.length) return viewOverview();
  S.idx = i; S.view = "q"; S.shownAt = Date.now();
  const item = S.session.items[i];
  const saved = S.answers[item.uid];
  const T = TYPES[item.type];
  const widget = T.render(item, saved?.response && !saved.response.dont_know ? saved.response : null);
  current = { widget, confidence: saved?.confidence ?? null };
  const confBtns = [[1, "Guessing"], [2, "Unsure"], [3, "Sure"]].map(([v, t]) =>
    h("button", { type: "button", class: current.confidence === v ? "sel" : "", onclick: (e) => {
      current.confidence = v; e.target.parentNode.querySelectorAll("button").forEach((b) => b.classList.remove("sel"));
      e.target.classList.add("sel"); changed();
    } }, t));
  const next = h("button", { class: "btn primary", id: "next", onclick: () => save(i + 1) }, i === S.session.items.length - 1 ? "Review & finish" : "Next");
  app.replaceChildren(h("div", { class: "card" },
    h("div", { class: "meta" }, h("span", {}, `Question ${i + 1} of ${S.session.items.length}`), h("span", { class: "chip" }, T.label),
      h("span", { title: "difficulty" }, "●".repeat(item.difficulty || 2) + "○".repeat(3 - (item.difficulty || 2))),
      S.session.mode === "review" ? h("span", { class: "chip" }, item.topic) : null),
    T.ownsPrompt ? null : h("div", { class: "prompt", html: md(item.prompt) }),
    item.code ? h("pre", {}, h("code", {}, item.code)) : null,
    widget.el,
    h("div", { class: "confidence" }, h("span", { class: "muted small" }, "How sure?"), h("div", { class: "seg" }, ...confBtns)),
    h("div", { class: "actions" },
      h("div", { class: "right" },
        h("button", { class: "btn ghost", onclick: () => save(i - 1), disabled: i === 0 }, "Back"),
        h("button", { class: "btn ghost", onclick: () => dontKnow(i) }, "I don't know")),
      h("div", { class: "right" }, next))));
  (widget.focus || (() => {}))();
  changed();
  setProgress();
}

function changed() {
  const btn = document.getElementById("next");
  if (!btn || !current) return;
  btn.disabled = !(current.widget.collect() && current.confidence);
}

async function post(i, response, confidence) {
  const item = S.session.items[i];
  const prev = S.answers[item.uid];
  const time_ms = (prev?.time_ms || 0) + (Date.now() - S.shownAt);
  await api("/api/answer", { uid: item.uid, response, confidence, time_ms });
  S.answers[item.uid] = { response, confidence, time_ms };
}

async function save(target) {
  const i = S.idx;
  const resp = current.widget.collect();
  try {
    if (resp && current.confidence) await post(i, resp, current.confidence);
    else if (target > i) return; // can't move forward without a complete answer
  } catch (e) { return alert("Could not save: " + e.message); }
  go(Math.max(0, target));
}

async function dontKnow(i) {
  try { await post(i, { dont_know: true }, 1); } catch (e) { return alert("Could not save: " + e.message); }
  go(i + 1);
}

function viewOverview() {
  S.view = "overview";
  const items = S.session.items;
  const missing = items.filter((i) => !S.answers[i.uid]).length;
  app.replaceChildren(h("div", { class: "card" },
    h("h2", {}, "Ready to grade?"),
    h("p", { class: "muted" }, missing ? `${missing} unanswered. They count as wrong. Click a number to go back.` : "All answered. Click a number to revisit one."),
    h("div", { class: "grid" }, ...items.map((it, i) => {
      const a = S.answers[it.uid];
      return h("button", { class: a ? (a.response?.dont_know ? "idk" : "done") : "", onclick: () => go(i), title: a?.response?.dont_know ? "I don't know" : "" }, i + 1);
    })),
    h("p", { class: "muted small" }, "Choice and ordering questions are graded instantly. Explain/predict answers are graded by Claude against a rubric, usually within a minute."),
    h("div", { class: "actions" }, h("button", { class: "btn ghost", onclick: () => go(items.length - 1) }, "Back"),
      h("button", { class: "btn primary", onclick: finish }, "Finish & grade"))));
  setProgress();
}

async function finish() {
  try { await api("/api/finish", {}); } catch (e) { return alert(e.message); }
  viewGrading();
}

function viewGrading() {
  S.view = "grading";
  const free = S.session.items.filter((i) => ["explain", "predict"].includes(i.type) && S.answers[i.uid] && !S.answers[i.uid].response?.dont_know).length;
  app.replaceChildren(h("div", { class: "card", style: "text-align:center" }, h("div", { class: "spinner" }),
    h("h2", {}, "Grading…"), h("p", { class: "muted" }, free ? `Claude is grading ${free} free-recall answer(s).` : "Almost there.")));
  poll();
}

async function poll() {
  try {
    const r = await api("/api/result");
    if (r.status === "applied") return viewResults(r);
    if (r.status === "grade_failed") return viewFailed(r);
  } catch { /* server busy; keep polling */ }
  setTimeout(poll, 2000);
}

function viewFailed(r) {
  app.replaceChildren(h("div", { class: "card" }, h("h2", {}, "Grading failed"),
    h("pre", {}, r.error || "unknown error"),
    h("p", { class: "muted" }, "Your answers are saved. Retry here, or run `lk grade <session>` in the terminal (the agent can also grade them by hand)."),
    h("div", { class: "actions" }, h("span"), h("button", { class: "btn primary", onclick: finish }, "Retry grading"))));
}

function nextStep(r) {
  const slug = S.session.topic;
  if (r.mode === "gate" && r.gate) {
    const ns = r.gate.next_stage;
    const msg = {
      reteach: ["Below the bar. That's normal on a first round.", `Next: a targeted reteach on the concepts you missed, then a fresh round. In the terminal: \`/learn ${slug}\``],
      note: ["Passed.", `Next: write your note in your own words: \`/note ${slug}\``],
      lab: ["Passed.", `Next: the lab. Predict, run, compare: \`/learn ${slug}\``],
      scheduled: ["Passed.", "These concepts are now in spaced recall. Come back with `/recall` when they're due."],
    }[ns] || ["", ""];
    const names = {};
    r.items.forEach((it) => (names[it.concept] = it.concept_name));
    const weak = (r.gate.weak_concepts || []).map((c) => names[c] || c);
    return h("div", { class: "next" }, h("strong", {}, msg[0] + " "), h("span", { html: inlineMd(msg[1]) }),
      weak.length ? h("div", { class: "small", style: "margin-top:6px" }, h("span", { class: "chip warn" }, "weak spot"),
        ` ${weak.join(", ")}: below 80% on its own. Its missed questions come back in recall tomorrow. Cover it in your note.`) : null);
  }
  if (r.mode === "review") {
    const again = (r.srs || []).filter((x) => x.grade === 1).length;
    return h("div", { class: "next" }, again ? `${again} card(s) lapsed: their concepts are marked for relearning (/learn <topic>).` : "All recalled. See you at the next due date.");
  }
  return null;
}

function viewResults(r) {
  S.view = "results";
  const sm = r.summary || {};
  const g = r.gate;
  const head = h("div", { class: "card" },
    h("div", { class: "meta" }, h("span", { class: "chip" }, MODE_INFO[r.mode][0])),
    h("div", { class: "score" }, pct(sm.overall)),
    g ? h("div", { class: "verdict " + (g.passed ? "ok" : "bad") }, g.passed ? `Passed (bar ${pct(g.pass_score)})` : `Not yet (bar ${pct(g.pass_score)})`) : null,
    h("p", { class: "muted" }, `${sm.correct}/${sm.n} correct · ${sm.confident_wrong} confident-wrong · ${sm.dont_know} "I don't know"`),
    g && g.core_without_free_recall?.length ? h("p", { class: "small" }, h("span", { class: "chip warn" }, "core"),
      " needs a correct explain/predict answer: " + g.core_without_free_recall.join(", ")) : null,
    nextStep(r));

  const concepts = h("div", { class: "card" }, h("h2", {}, "By concept"));
  const bars = h("div", { class: "bars" });
  const rows = g ? Object.entries(g.concepts).map(([k, v]) => [k, v.mean, v.passed, v.core]) :
    Object.entries(sm.by_concept || {}).map(([k, v]) => [k, v.mean, v.mean >= 0.8, false]);
  const names = {};
  r.items.forEach((it) => (names[it.concept] = it.concept_name));
  rows.sort((a, b) => a[1] - b[1]).forEach(([k, mean, ok, core]) => {
    const key = k.includes(":") ? k.split(":")[1] : k;
    bars.append(h("div", { class: "barrow" },
      h("span", { class: "lbl", title: k }, (core ? "★ " : "") + (names[key] || key)),
      h("div", { class: "track" }, h("div", { class: "fill " + (ok ? "ok" : "bad"), style: `width:${Math.round(mean * 100)}%` }),
        h("div", { class: "mark", style: "left:80%" })),
      h("span", { class: "small" }, pct(mean))));
  });
  concepts.append(bars, h("p", { class: "hint" }, "★ core concept · line = 80% bar"));

  const list = h("div", { class: "card" }, h("h2", {}, "Answers"));
  r.items.forEach((it) => {
    const res = it.result || {};
    const cls = res.correct ? "ok" : (res.score >= 0.5 ? "part" : "bad");
    const d = h("details", { class: "res", open: !res.correct && it.confident_wrong ? true : null },
      h("summary", {}, h("span", { class: "dot " + cls }), h("span", { class: "q" }, `${it.n}. ${plain(it.prompt)}`),
        it.confident_wrong ? h("span", { class: "chip bad" }, "sure & wrong") : null, h("span", { class: "small muted" }, pct(res.score))),
      h("div", { class: "body" },
        h("div", { class: "prompt", style: "margin-top:10px", html: md(it.prompt) }),
        it.code ? h("pre", {}, h("code", {}, it.code)) : null,
        kv("Your answer", it.your_answer),
        kv("Expected", res.expected),
        res.feedback ? kv("Feedback", res.feedback) : null,
        res.wrong_claims?.length ? kv("Wrong claims", res.wrong_claims.map((x) => "• " + x).join("\n")) : null,
        res.missed?.length ? kv("Missed", res.missed.map((x) => "• " + x).join("\n")) : null,
        res.explanation ? kv("Why", res.explanation) : null,
        it.sources?.length ? h("div", { class: "kv" }, h("div", { class: "k" }, "Sources"),
          ...it.sources.map((s) => h("div", { class: "small" }, s.url ? h("a", { href: s.url, target: "_blank", rel: "noopener" }, `[${s.id}] ${s.title}`) : `[${s.id}] ${s.title}`))) : null,
        h("div", { class: "kv small muted" }, `concept: ${it.concept_name} · ${it.type} · confidence ${["-", "guessing", "unsure", "sure"][it.confidence || 0]}`)));
    list.append(d);
  });

  const done = h("div", { class: "actions" }, h("span"), h("button", { class: "btn primary", onclick: async () => {
    try { await api("/api/done", {}); } catch { /* already closed */ }
    app.replaceChildren(h("div", { class: "card" }, h("h2", {}, "Session closed"), h("p", { class: "muted" }, "You can close this tab. Results are saved, and the terminal has the summary.")));
  } }, "Close session"));
  app.replaceChildren(head, concepts, list, done);
  document.getElementById("progress-fill").style.width = "100%";
}

function kv(k, v) {
  return h("div", { class: "kv" }, h("div", { class: "k" }, k), h("div", { class: "v" }, v || "—"));
}

// ------------------------------------------------------------------ keyboard
document.addEventListener("keydown", (e) => {
  if (S.view !== "q" || !current || e.target.tagName === "BUTTON") return;
  const inText = /^(TEXTAREA|INPUT|SELECT)$/.test(e.target.tagName) && e.target.type !== "radio" && e.target.type !== "checkbox";
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey || !inText)) {
    const btn = document.getElementById("next");
    if (btn && !btn.disabled) { e.preventDefault(); btn.click(); }
    return;
  }
  if (!inText && /^[1-9]$/.test(e.key) && current.widget.key) current.widget.key(Number(e.key));
});

// ------------------------------------------------------------------ boot
(async function boot() {
  try {
    S.session = await api("/api/session");
  } catch (e) {
    app.replaceChildren(h("div", { class: "card" }, h("h2", {}, "Can't load the session"), h("pre", {}, e.message)));
    return;
  }
  document.title = S.session.title;
  document.getElementById("title").textContent = S.session.title;
  S.answers = S.session.answers || {};
  setProgress();
  if (S.session.status === "applied" || S.session.status === "grade_failed") return poll();
  if (S.session.status === "grading") return viewGrading();
  const deep = /^#q=(\d+)$/.exec(location.hash); // deep link: #q=3 opens question 3
  if (deep) return go(Math.min(Number(deep[1]), S.session.items.length) - 1);
  viewIntro();
})();
