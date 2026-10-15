/* Enrich and verify: adds Treg contact lookups to the finder's results.
   Loaded after the page's own script; wraps renderResults and exportRows. */
(function () {
  if (typeof renderResults !== "function" || typeof exportRows !== "function") return;

  const BADGE = {
    valid: ["Email verified", "#1e7b4f"],
    risky: ["Email risky", "#9a6212"],
    invalid: ["Email invalid", "#b23a3a"],
    unknown: ["Not verified", "#5e6b66"],
  };
  const css = document.createElement("style");
  css.textContent = `
    .contact { margin-top: 10px; padding: 10px 12px; border-radius: 8px; background: #fff; border: 1px solid var(--line); font-size: 13px; }
    .contact .who { font-weight: 600; }
    .contact .who span { font-weight: 400; color: var(--muted); }
    .contact .mail { margin-top: 4px; display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; }
    .contact .badge { display: inline-block; padding: 1px 8px; border-radius: 10px; color: #fff; font-size: 11.5px; font-weight: 600; }
    .contact .note { color: var(--muted); }
    .contact .cost { color: var(--muted); font-size: 12px; margin-top: 4px; }
    #enrichStatus { margin: 0 0 8px; font-size: 12.5px; color: var(--muted); }
    #enrichStatus.err { color: var(--low); }`;
  document.head.appendChild(css);

  let data = {}, timer = null, resultMode = null, budget = 0.25;
  fetch("/api/enrich-config").then(r => r.json()).then(c => { budget = c.budget; hint(); }).catch(() => {});

  function statusEl() {
    let el = document.getElementById("enrichStatus");
    if (!el) {
      el = document.createElement("p");
      el.id = "enrichStatus";
      document.getElementById("summary").after(el);
    }
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

  function contactHtml(d) {
    if (d.status === "found" || d.status === "person_only") {
      const [label, color] = BADGE[d.email_verdict] || BADGE.unknown;
      let h = `<div class="who">${esc(d.contact_name)}${d.contact_title ? ` <span>${esc(d.contact_title)}</span>` : ""}</div>`;
      if (d.contact_email) {
        h += `<div class="mail"><a href="mailto:${esc(d.contact_email)}">${esc(d.contact_email)}</a>
              <span class="badge" style="background:${color}">${label}</span>
              ${d.contact_linkedin ? `<a href="${esc(d.contact_linkedin)}" target="_blank" rel="noopener">LinkedIn</a>` : ""}</div>`;
      } else if (d.contact_linkedin) {
        h += `<div class="mail"><a href="${esc(d.contact_linkedin)}" target="_blank" rel="noopener">LinkedIn</a></div>`;
      }
      if (d.note) h += `<div class="note">${esc(d.note)}</div>`;
      h += `<div class="cost">Lookup cost $${Number(d.cost_usd || 0).toFixed(3)}</div>`;
      return h;
    }
    return `<div class="note">${esc(d.note || "No contact found.")}</div>` +
           (d.cost_usd ? `<div class="cost">Lookup cost $${Number(d.cost_usd).toFixed(3)}</div>` : "");
  }

  function decorate() {
    ensureButton();
    document.querySelectorAll(".res").forEach(li => {
      const d = data[li.dataset.pid];
      let box = li.querySelector(".contact");
      if (!d) { if (box) box.remove(); return; }
      if (!box) { box = document.createElement("div"); box.className = "contact"; li.querySelector(".why").after(box); }
      box.innerHTML = contactHtml(d);
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
        contact_name: d.contact_name || "", contact_title: d.contact_title || "",
        contact_email: d.contact_email || "", email_verdict: d.email_verdict || "",
        contact_linkedin: d.contact_linkedin || "", enrich_note: d.note || "",
        enrich_cost_usd: d.cost_usd != null ? d.cost_usd : "",
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
      Object.assign(data, j.results || {});
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
})();
