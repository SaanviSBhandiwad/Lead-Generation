/* PromptIQ Growth Finder add-on (v2):
   - shows the ICP tier, hook and pilot use case on each result
   - Enrich and verify: decision-maker + work email + verification via Treg
   - Write email: a personalised draft you can edit, then send (or open in your mail app)
   Loaded after the page's own script; wraps renderResults and exportRows. */
(function () {
  if (typeof renderResults !== "function" || typeof exportRows !== "function") return;

  const BADGE = {
    valid: ["Email verified", "#1e7b4f"],
    risky: ["Email risky", "#9a6212"],
    invalid: ["Email invalid", "#b23a3a"],
    unknown: ["Not verified", "#5e6b66"],
  };
  const TIER = { A: "#1e7b4f", B: "#2e5c8a", C: "#5e6b66" };
  const css = document.createElement("style");
  css.textContent = `
    .icp { margin-top: 8px; font-size: 13px; }
    .icp .tier { display: inline-block; padding: 1px 8px; border-radius: 10px; color: #fff; font-size: 11.5px; font-weight: 700; margin-right: 6px; }
    .icp .uc { margin-top: 3px; color: var(--muted); }
    .contact { margin-top: 10px; padding: 10px 12px; border-radius: 8px; background: #fff; border: 1px solid var(--line); font-size: 13px; }
    .contact .who { font-weight: 600; }
    .contact .who span { font-weight: 400; color: var(--muted); }
    .contact .mail { margin-top: 4px; display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; }
    .contact .badge { display: inline-block; padding: 1px 8px; border-radius: 10px; color: #fff; font-size: 11.5px; font-weight: 600; }
    .contact .note, .contact .cost, .contact .sent { color: var(--muted); }
    .contact .cost, .contact .sent { font-size: 12px; margin-top: 4px; }
    .contact .sent { color: var(--good); font-weight: 600; }
    .contact .write { margin-top: 8px; }
    #enrichStatus { margin: 0 0 8px; font-size: 12.5px; color: var(--muted); }
    #enrichStatus.err { color: var(--low); }
    .mail-overlay { position: fixed; inset: 0; background: rgba(23,33,43,.45); z-index: 3000; display: flex; align-items: center; justify-content: center; padding: 16px; }
    .mail-box { background: var(--surface); border-radius: 12px; width: min(640px, 100%); max-height: calc(100vh - 32px); overflow: auto; padding: 18px 20px; box-shadow: 0 12px 40px rgba(0,0,0,.25); }
    .mail-box h2 { margin: 0 0 12px; font-size: 17px; }
    .mail-box label { margin-bottom: 10px; }
    .mail-box input, .mail-box textarea { font-weight: 400; }
    .mail-box textarea { font: inherit; font-weight: 400; color: var(--ink); width: 100%; min-height: 260px; padding: 10px 11px; border: 1px solid var(--line); border-radius: 8px; resize: vertical; }
    .mail-box .row { display: flex; gap: 10px; justify-content: flex-end; align-items: center; flex-wrap: wrap; margin-top: 12px; }
    .mail-box .check { display: flex; flex-direction: row; gap: 8px; align-items: flex-start; font-weight: 400; color: var(--ink); }
    .mail-box .check input { width: auto; margin-top: 3px; }
    .mail-box .msg { font-size: 13px; margin-top: 8px; }
    .mail-box .msg.err { color: var(--low); } .mail-box .msg.ok { color: var(--good); }
    .mail-box .send { border: 0; border-radius: 8px; background: var(--metro); color: #fff; padding: 9px 18px; font-weight: 650; cursor: pointer; }
    .mail-box .send:disabled { background: #a994bd; cursor: default; }`;
  document.head.appendChild(css);

  let data = {}, timer = null, resultMode = null, budget = 0.25, mailCfg = { smtp: false, daily_limit: 20, sent_today: 0 };
  fetch("/api/enrich-config").then(r => r.json()).then(c => { budget = c.budget; hint(); }).catch(() => {});
  function refreshMailCfg() { return fetch("/api/mail-config").then(r => r.json()).then(c => { mailCfg = c; }).catch(() => {}); }
  refreshMailCfg();

  const resultById = pid => (lastResult && lastResult.results.find(r => r.place_id === pid)) || {};

  function statusEl() {
    let el = document.getElementById("enrichStatus");
    if (!el) { el = document.createElement("p"); el.id = "enrichStatus"; document.getElementById("summary").after(el); }
    return el;
  }
  function say(msg, isErr) { const el = statusEl(); el.className = isErr ? "err" : ""; el.textContent = msg; }
  function hint() {
    if (document.getElementById("resultsHead").hidden) return;
    if (!Object.keys(data).length) say(`Enrich and verify finds a decision-maker and a verified email for each result using your Treg credits. It stops at $${budget.toFixed(2)} per run.`);
  }

  function ensureButton() {
    const tools = document.querySelector(".results-head .tools");
    if (!tools || document.getElementById("enrichBtn")) return;
    const b = document.createElement("button");
    b.className = "ghost"; b.id = "enrichBtn"; b.textContent = "Enrich and verify";
    tools.insertBefore(b, document.getElementById("csvBtn"));
    b.addEventListener("click", run);
  }

  function icpHtml(r) {
    if (!r.tier && !r.hook && !r.use_case) return "";
    let h = "";
    if (r.tier) h += `<span class="tier" style="background:${TIER[r.tier] || TIER.C}">Tier ${esc(r.tier)}</span>`;
    if (r.hook) h += esc(r.hook);
    if (r.use_case) h += `<div class="uc">Pilot idea: ${esc(r.use_case)}</div>`;
    return h;
  }

  function contactHtml(d) {
    let h = "";
    if (d.status === "found" || d.status === "person_only") {
      const [label, color] = BADGE[d.email_verdict] || BADGE.unknown;
      h += `<div class="who">${esc(d.contact_name)}${d.contact_title ? ` <span>${esc(d.contact_title)}</span>` : ""}</div>`;
      if (d.contact_email) {
        h += `<div class="mail"><a href="mailto:${esc(d.contact_email)}">${esc(d.contact_email)}</a>
              <span class="badge" style="background:${color}">${label}</span>
              ${d.contact_linkedin ? `<a href="${esc(d.contact_linkedin)}" target="_blank" rel="noopener">LinkedIn</a>` : ""}</div>`;
      } else if (d.contact_linkedin) {
        h += `<div class="mail"><a href="${esc(d.contact_linkedin)}" target="_blank" rel="noopener">LinkedIn</a></div>`;
      }
      if (d.note) h += `<div class="note">${esc(d.note)}</div>`;
    } else {
      h += `<div class="note">${esc(d.note || "No contact found.")}</div>`;
    }
    if (d.cost_usd) h += `<div class="cost">Lookup cost $${Number(d.cost_usd).toFixed(3)}</div>`;
    if (d.sent) h += `<div class="sent">${esc(d.sent)}</div>`;
    else if (d.contact_email && d.email_verdict !== "invalid") h += `<button class="ghost write" data-write="1">Write email</button>`;
    return h;
  }

  function decorate() {
    ensureButton();
    document.querySelectorAll(".res").forEach(li => {
      const pid = li.dataset.pid, r = resultById(pid);
      const icp = icpHtml(r);
      let ib = li.querySelector(".icp");
      if (icp && !ib) { ib = document.createElement("div"); ib.className = "icp"; li.querySelector(".why").after(ib); }
      if (ib) ib.innerHTML = icp;
      const d = data[pid];
      let box = li.querySelector(".contact");
      if (!d) { if (box) box.remove(); return; }
      if (!box) { box = document.createElement("div"); box.className = "contact"; (li.querySelector(".icp") || li.querySelector(".why")).after(box); }
      box.innerHTML = contactHtml(d);
      const w = box.querySelector("[data-write]");
      if (w) w.addEventListener("click", e => { e.stopPropagation(); openMail(pid); });
    });
  }

  const originalRender = renderResults;
  renderResults = function (res) {
    clearInterval(timer); data = {}; resultMode = mode;
    originalRender(res);
    decorate();
    const b = document.getElementById("enrichBtn");
    if (b) { b.disabled = false; b.textContent = "Enrich and verify"; }
    hint();
  };

  const originalExport = exportRows;
  exportRows = function () {
    return originalExport().map((row, i) => {
      const r = (lastResult && lastResult.results[i]) || {};
      const d = data[r.place_id] || {};
      return Object.assign(row, {
        tier: r.tier || "", hook: r.hook || "", pilot_use_case: r.use_case || "",
        contact_name: d.contact_name || "", contact_title: d.contact_title || "",
        contact_email: d.contact_email || "", email_verdict: d.email_verdict || "",
        contact_linkedin: d.contact_linkedin || "", enrich_note: d.note || "",
        enrich_cost_usd: d.cost_usd != null ? d.cost_usd : "", email_sent: d.sent || "",
      });
    });
  };

  async function run() {
    if (!lastResult || !lastResult.results.length) return;
    const btn = document.getElementById("enrichBtn");
    btn.disabled = true; btn.textContent = "Enriching...";
    say("Starting...");
    let resp;
    try {
      resp = await (await fetch("/api/enrich", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mode: resultMode || mode,
          leads: lastResult.results.map(r => ({ place_id: r.place_id, name: r.name, website: r.website || "" })),
        }),
      })).json();
    } catch (e) { finish("Cannot reach the app. Check that python3 app.py is running.", true); return; }
    if (!resp.ok) { finish(resp.error, true); return; }
    timer = setInterval(async () => {
      let j;
      try { j = await (await fetch("/api/enrich/" + resp.job_id)).json(); } catch (e) { return; }
      if (!j.ok) { clearInterval(timer); finish(j.error, true); return; }
      for (const [k, v] of Object.entries(j.results || {})) data[k] = Object.assign(data[k] || {}, v);
      decorate();
      const spent = `Spent $${Number(j.spent || 0).toFixed(3)} of $${Number(j.budget).toFixed(2)}.`;
      if (j.status === "running") { say(`${j.message}. ${spent}`); return; }
      clearInterval(timer);
      if (j.status === "error") { finish(j.error, true); return; }
      finish(`${j.message}. ${spent}${j.stopped ? " Stopped early: " + j.stopped : ""}`, !!j.stopped);
    }, 1500);
  }

  function finish(msg, isErr) {
    const btn = document.getElementById("enrichBtn");
    if (btn) { btn.disabled = false; btn.textContent = "Enrich and verify"; }
    say(msg, isErr);
  }

  /* ---------- email writer ---------- */
  async function openMail(pid) {
    await refreshMailCfg();
    const r = resultById(pid), d = data[pid] || {};
    const verdict = d.email_verdict || "unknown";
    const verified = verdict === "valid";
    const ov = document.createElement("div");
    ov.className = "mail-overlay";
    ov.innerHTML = `
      <div class="mail-box" role="dialog" aria-modal="true" aria-labelledby="mailTitle">
        <h2 id="mailTitle">Email to ${esc(d.contact_name || "this contact")}</h2>
        <label>To <input type="text" value="${esc(d.contact_email)}" readonly></label>
        <label>Subject <input type="text" id="mSubject" value="Writing your draft..."></label>
        <label>Message <textarea id="mBody">Writing your draft...</textarea></label>
        ${verified ? "" : `<label class="check"><input type="checkbox" id="mAllow">
          <span>This address is ${verdict === "risky" ? "risky (the company accepts all addresses)" : "not verified"}, so it may not reach a real inbox. Send anyway.</span></label>`}
        <p class="note" style="margin:6px 0 0">${mailCfg.mock ? "Sample mode: nothing will actually be sent." :
          mailCfg.smtp ? `Sends from ${esc(mailCfg.from)}. ${mailCfg.sent_today} of ${mailCfg.daily_limit} sent today.` :
          mailCfg.method === "gmail" ? "This opens Gmail with the draft filled in. Check it there and press Send." :
          "No mail account is set up, so this opens in your mail app and you press Send there."}</p>
        <p class="msg" id="mMsg" role="status"></p>
        <div class="row">
          <button class="ghost" id="mCancel">Cancel</button>
          <button class="send" id="mSend">${mailCfg.smtp || mailCfg.mock ? "Send email" : mailCfg.method === "gmail" ? "Open in Gmail" : "Open in mail app"}</button>
        </div>
      </div>`;
    document.body.appendChild(ov);
    const close = () => { ov.remove(); document.removeEventListener("keydown", onKey); };
    const onKey = e => { if (e.key === "Escape") close(); };
    document.addEventListener("keydown", onKey);
    ov.addEventListener("click", e => { if (e.target === ov) close(); });
    ov.querySelector("#mCancel").addEventListener("click", close);
    const sendBtn = ov.querySelector("#mSend"), allow = ov.querySelector("#mAllow"), msg = ov.querySelector("#mMsg");
    const sync = () => { sendBtn.disabled = !!allow && !allow.checked; };
    if (allow) allow.addEventListener("change", sync);
    sync();
    try {
      const dr = await (await fetch("/api/draft", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: resultMode || mode, lead: r, contact: d }) })).json();
      ov.querySelector("#mSubject").value = dr.subject;
      ov.querySelector("#mBody").value = dr.body;
    } catch (e) { msg.className = "msg err"; msg.textContent = "Could not write a draft. Is python3 app.py running?"; }
    ov.querySelector("#mSubject").focus();
    sendBtn.addEventListener("click", async () => {
      const tab = (!mailCfg.smtp && !mailCfg.mock && mailCfg.method === "gmail") ? window.open("about:blank", "_blank") : null; // gmail-tab
      sendBtn.disabled = true; msg.className = "msg"; msg.textContent = "Sending...";
      let res;
      try {
        res = await (await fetch("/api/send", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ to: d.contact_email, subject: ov.querySelector("#mSubject").value,
            body: ov.querySelector("#mBody").value, verdict, company: r.name || "",
            allow_unverified: !!(allow && allow.checked) }) })).json();
      } catch (e) { res = { ok: false, message: "Cannot reach the app." }; }
      if (!res.ok) { if (tab) tab.close(); msg.className = "msg err"; msg.textContent = res.message; sendBtn.disabled = false; return; }
      if (res.mailto) window.location.href = res.mailto;
      if (res.url) { if (tab) { tab.opener = null; tab.location.href = res.url; } else window.open(res.url, "_blank", "noopener"); }
      d.sent = res.status === "sent" ? `Emailed on ${new Date().toLocaleDateString()}` :
               res.status === "sample" ? "Sample send (nothing sent)" : res.status === "opened in gmail" ? `Opened in Gmail on ${new Date().toLocaleDateString()}` : `Opened in mail app on ${new Date().toLocaleDateString()}`;
      data[pid] = d;
      msg.className = "msg ok"; msg.textContent = res.message;
      decorate();
      setTimeout(close, 1200);
    });
  }
})();
