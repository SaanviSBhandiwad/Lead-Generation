import os
base = os.path.expanduser(os.environ.get("BASE", "~/promptiq_ui"))
p = os.path.join(base, "app.py")
s = open(p, encoding="utf-8").read()
if "import chatparse" in s:
    print("app.py: already updated")
else:
    imp = "import enrich as treg_enrich\n"
    assert imp in s, "app.py: install the Treg (enrich) update first"
    s = s.replace(imp, imp + "import chatparse\n", 1)
    marker = 'if __name__ == "__main__":'
    assert marker in s
    s = s.replace(marker, '''@app.route("/chat")
def chat_page():
    return send_from_directory(app.template_folder, "chat.html")


@app.route("/api/parse", methods=["POST"])
def api_parse():
    b = request.get_json(force=True, silent=True) or {}
    text = (b.get("text") or "").strip()[:500]
    if not text:
        return jsonify({"ok": False, "error": "Type what leads you want."}), 400
    pending = b.get("pending") if isinstance(b.get("pending"), dict) else None
    r = chatparse.parse(text, pending)
    r["ok"] = True
    if not r["missing"]:
        r["reply"] = chatparse.describe(r["search"])
    return jsonify(r)


''' + marker, 1)
    open(p, "w", encoding="utf-8").write(s)
    print("app.py: updated")

p = os.path.join(base, "templates", "landing.html")
if os.path.exists(p):
    s = open(p, encoding="utf-8").read()
    if 'href="/app"' in s:
        open(p, "w", encoding="utf-8").write(s.replace('href="/app"', 'href="/chat"'))
        print("landing.html: buttons now open the chat")
    else:
        print("landing.html: already updated")
