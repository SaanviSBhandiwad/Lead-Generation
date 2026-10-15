import os
base = os.path.expanduser(os.environ.get("BASE", "~/promptiq_ui"))
p = os.path.join(base, "app.py")
s = open(p, encoding="utf-8").read()
if "import enrich as treg_enrich" in s:
    print("app.py: already updated")
else:
    imp = "from flask import Flask, jsonify, request, send_from_directory\n"
    assert imp in s, "app.py: flask import line not found"
    s = s.replace(imp, imp + "\nimport enrich as treg_enrich\n", 1)
    hermes_fail = '''            add("Hermes", False, "The 'hermes' command is not on this terminal's PATH.")\n'''
    assert hermes_fail in s, "app.py: setup-check Hermes line not found (is the Check setup update installed?)"
    s = s.replace(hermes_fail, hermes_fail + '''        add("Treg (optional)", True, "Token found. Enrich and verify is ready." if treg_enrich.load_token()
            else "No TREG_TOKEN set. Only needed for Enrich and verify.")\n''', 1)
    routes = '''@app.route("/api/enrich-config")
def api_enrich_config():
    return jsonify({"budget": treg_enrich.RUN_BUDGET_USD, "token": bool(treg_enrich.load_token()),
                    "mock": treg_enrich.MOCK_MODE})


@app.route("/api/enrich", methods=["POST"])
def api_enrich():
    b = request.get_json(force=True, silent=True) or {}
    leads = [l for l in (b.get("leads") or []) if isinstance(l, dict) and l.get("place_id")]
    if not leads:
        return jsonify({"ok": False, "error": "There are no results to enrich."}), 400
    mode = b.get("mode") if b.get("mode") in MODE_QUERIES else "investors"
    return jsonify({"ok": True, "job_id": treg_enrich.start(leads, mode)})


@app.route("/api/enrich/<job_id>")
def api_enrich_job(job_id):
    j = treg_enrich.get(job_id)
    if not j:
        return jsonify({"ok": False, "error": "Unknown enrichment run (the app may have restarted)."}), 404
    out = {k: j[k] for k in ("status", "message", "results", "spent", "budget", "stopped", "error")}
    out["ok"] = True
    return jsonify(out)


'''
    marker = 'if __name__ == "__main__":'
    assert marker in s
    s = s.replace(marker, routes + marker, 1)
    open(p, "w", encoding="utf-8").write(s)
    print("app.py: updated")

p = os.path.join(base, "templates", "index.html")
s = open(p, encoding="utf-8").read()
if "/static/enrich.js" in s:
    print("index.html: already updated")
else:
    assert s.rstrip().endswith("</html>") and "</body>" in s, "index.html: </body> not found"
    i = s.rindex("</body>")
    s = s[:i] + '<script src="/static/enrich.js"></script>\n' + s[i:]
    open(p, "w", encoding="utf-8").write(s)
    print("index.html: updated")
