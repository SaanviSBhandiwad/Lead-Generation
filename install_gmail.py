import os
base = os.path.expanduser(os.environ.get("BASE", "~/promptiq_ui"))

def patch(rel, edits, marker):
    p = os.path.join(base, rel)
    if not os.path.exists(p):
        print(f"{rel}: not found, skipped"); return
    s = open(p, encoding="utf-8").read()
    if marker in s:
        print(f"{rel}: already updated"); return
    for old, new in edits:
        assert old in s, f"{rel}: could not find: {old.strip()[:70]}"
        s = s.replace(old, new, 1)
    open(p, "w", encoding="utf-8").write(s)
    print(f"{rel}: updated")

# ---- mailer.py: open Gmail's compose window when no SMTP account is set up ----
patch("mailer.py", [
    ('MOCK_MODE = os.environ.get("MOCK", "0") == "1"',
     'MOCK_MODE = os.environ.get("MAIL_SAMPLE", os.environ.get("MOCK", "0")) == "1"'),
    ('''            "daily_limit": int(env_value("MAIL_DAILY_LIMIT", "20") or 20), "smtp": bool(user and pw)}''',
     '''            "daily_limit": int(env_value("MAIL_DAILY_LIMIT", "20") or 20), "smtp": bool(user and pw),
            "method": (env_value("MAIL_METHOD", "gmail") or "gmail").lower(),
            "gmail_account": env_value("MAIL_GMAIL_ACCOUNT")}'''),
    ('''    return {"smtp": c["smtp"], "from": c["user"], "daily_limit": c["daily_limit"],''',
     '''    return {"smtp": c["smtp"], "from": c["user"], "daily_limit": c["daily_limit"],
            "method": c["method"], "gmail_account": c["gmail_account"],'''),
    ('''r.get("status") in ("sent", "opened in mail app", "sample"))''',
     '''r.get("status") in ("sent", "opened in mail app", "opened in gmail", "sample"))'''),
    ('''r.get("status") in ("sent", "opened in mail app"):''',
     '''r.get("status") in ("sent", "opened in mail app", "opened in gmail"):'''),
    ('''    if not cfg["smtp"]:
        link = "mailto:"''',
     '''    if not cfg["smtp"] and cfg["method"] == "gmail":   # gmail-compose
        params = {"view": "cm", "fs": "1", "to": to, "su": subject, "body": body}
        if cfg["gmail_account"]:
            params = {"authuser": cfg["gmail_account"], **params}
        url = "https://mail.google.com/mail/?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        _log(to, company, subject, "opened in gmail")
        return {"ok": True, "status": "opened in gmail", "url": url,
                "message": "Opened in Gmail. Check the draft and press Send there."}

    if not cfg["smtp"]:
        link = "mailto:"'''),
], "# gmail-compose")

# ---- chat.html: open Gmail in a new tab ----
patch("templates/chat.html", [
    ('''cfg.smtp ? `Sends from ${esc(cfg.from)}. ${cfg.sent_today} of ${cfg.daily_limit} sent today.` : "No mail account''',
     '''cfg.smtp ? `Sends from ${esc(cfg.from)}. ${cfg.sent_today} of ${cfg.daily_limit} sent today.` : cfg.method === "gmail" ? `This opens Gmail with the draft filled in. Check it there and press Send. ${cfg.sent_today} of ${cfg.daily_limit} today.` : "No mail account'''),
    ('''${cfg.smtp || cfg.mock ? "Send email" : "Open in mail app"}''',
     '''${cfg.smtp || cfg.mock ? "Send email" : cfg.method === "gmail" ? "Open in Gmail" : "Open in mail app"}'''),
    ('''  send.onclick = async () => {
    send.disabled = true;''',
     '''  send.onclick = async () => {
    const tab = (!cfg.smtp && !cfg.mock && cfg.method === "gmail") ? window.open("about:blank", "_blank") : null; // gmail-tab
    send.disabled = true;'''),
    ('''if (!res.ok) { msg.className = "msgline err"; msg.textContent = res.message; send.disabled = false; return; }''',
     '''if (!res.ok) { if (tab) tab.close(); msg.className = "msgline err"; msg.textContent = res.message; send.disabled = false; return; }'''),
    ('''    if (res.mailto) window.location.href = res.mailto;''',
     '''    if (res.mailto) window.location.href = res.mailto;
    if (res.url) { if (tab) { tab.opener = null; tab.location.href = res.url; } else window.open(res.url, "_blank", "noopener"); }'''),
    (''': `Opened in mail app on ${new Date().toLocaleDateString()}`;''',
     ''': res.status === "opened in gmail" ? `Opened in Gmail on ${new Date().toLocaleDateString()}` : `Opened in mail app on ${new Date().toLocaleDateString()}`;'''),
], "gmail-tab")

# ---- enrich.js (classic view): same behaviour ----
patch("static/enrich.js", [
    ('''          mailCfg.smtp ? `Sends from ${esc(mailCfg.from)}. ${mailCfg.sent_today} of ${mailCfg.daily_limit} sent today.` :
          "No mail account''',
     '''          mailCfg.smtp ? `Sends from ${esc(mailCfg.from)}. ${mailCfg.sent_today} of ${mailCfg.daily_limit} sent today.` :
          mailCfg.method === "gmail" ? "This opens Gmail with the draft filled in. Check it there and press Send." :
          "No mail account'''),
    ('''${mailCfg.smtp || mailCfg.mock ? "Send email" : "Open in mail app"}''',
     '''${mailCfg.smtp || mailCfg.mock ? "Send email" : mailCfg.method === "gmail" ? "Open in Gmail" : "Open in mail app"}'''),
    ('''    sendBtn.addEventListener("click", async () => {
      sendBtn.disabled = true;''',
     '''    sendBtn.addEventListener("click", async () => {
      const tab = (!mailCfg.smtp && !mailCfg.mock && mailCfg.method === "gmail") ? window.open("about:blank", "_blank") : null; // gmail-tab
      sendBtn.disabled = true;'''),
    ('''if (!res.ok) { msg.className = "msg err"; msg.textContent = res.message; sendBtn.disabled = false; return; }''',
     '''if (!res.ok) { if (tab) tab.close(); msg.className = "msg err"; msg.textContent = res.message; sendBtn.disabled = false; return; }'''),
    ('''      if (res.mailto) window.location.href = res.mailto;''',
     '''      if (res.mailto) window.location.href = res.mailto;
      if (res.url) { if (tab) { tab.opener = null; tab.location.href = res.url; } else window.open(res.url, "_blank", "noopener"); }'''),
    (''': `Opened in mail app on ${new Date().toLocaleDateString()}`;''',
     ''': res.status === "opened in gmail" ? `Opened in Gmail on ${new Date().toLocaleDateString()}` : `Opened in mail app on ${new Date().toLocaleDateString()}`;'''),
], "gmail-tab")
