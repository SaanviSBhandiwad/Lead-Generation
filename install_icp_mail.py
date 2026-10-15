import os, re, shutil
base = os.path.expanduser(os.environ.get("BASE", "~/promptiq_ui"))
skill = os.path.expanduser(os.environ.get("PROMPTIQ_SKILL", "~/.hermes/skills/promptiq-growth/SKILL.md"))

ICP_BLOCK = """<!-- ICP:START -->
## PromptIQ ICP (the web app reads this section for CLIENTS searches)

**Tier A, sell to first:** regulated, data-heavy, non-tech-native companies.
- Industries: NBFCs, housing finance companies, small finance banks, insurers; hospitals, diagnostics chains, pharma and API manufacturers; mid-size manufacturers and logistics firms running SAP, Oracle or Tally
- Size: 100 to 2,000 employees (has data and budget, no in-house AI team)
- Geography: India first (Mumbai, Bengaluru, Delhi-NCR, Hyderabad, Chennai, Pune, Ahmedabad), UAE second
- Tech signal: core data in SAP, Oracle, MySQL, MSSQL or file shares; on-prem data centre or private cloud; cannot send data to cloud AI
- Triggers: DPDP Act compliance pressure, RBI / IRDAI / CERT-In audits, the board asking for an AI plan, a CIO or CTO new in role (under 12 months)
- Buyers: CIO, CTO or Head of IT (economic and technical buyer); Chief Data or Digital Officer (champion); CFO or COO (owns the pain: MIS, collections, inventory)
- Pain in their words: "Reports take 3 days from IT", "We can't use ChatGPT because of compliance", "AI vendors quoted Rs 1 Cr and 6 months"

**Tier B, design partners or channel, not first pilots:** fintech and healthtech startups and AI-native firms that build in-house.

**Tier C, nurture only:** regulators, MNC subsidiaries and very large groups with long procurement cycles.

**Anti-ICP, not relevant:** defence and PSU tenders, companies under 50 people, pure cloud-native SaaS companies.
<!-- ICP:END -->"""

# ---- 1. SKILL.md gets the ICP ------------------------------------------------
if os.path.exists(skill):
    t = open(skill, encoding="utf-8").read()
    shutil.copy(skill, skill + ".bak-icp")
    if "<!-- ICP:START -->" in t and "<!-- ICP:END -->" in t:
        t = re.sub(r"<!-- ICP:START -->.*?<!-- ICP:END -->", lambda m: ICP_BLOCK, t, flags=re.DOTALL)
    elif "## Modes" in t:
        t = t.replace("## Modes", ICP_BLOCK + "\n\n## Modes", 1)
    else:
        t = t.rstrip() + "\n\n" + ICP_BLOCK + "\n"
    open(skill, "w", encoding="utf-8").write(t)
    print("SKILL.md: ICP added")
else:
    print("SKILL.md not found at", skill, "- the app will use its built-in copy of the ICP")

# ---- 2. app.py follows the ICP and can draft and send email -------------------
p = os.path.join(base, "app.py")
s = open(p, encoding="utf-8").read()
if "# v4 ICP" in s:
    print("app.py: already updated")
else:
    def swap(old, new):
        global s
        assert old in s, "app.py: could not find: " + old.strip()[:70]
        s = s.replace(old, new, 1)

    swap("import enrich as treg_enrich\n", "import enrich as treg_enrich\nimport mailer\n")
    swap('''    "clients": ["fintech company", "NBFC non banking financial company", "healthtech company",
                "software company"],''',
         '''    "clients": ["NBFC", "housing finance company", "small finance bank", "insurance company", "hospital",
                "diagnostic centre", "pharmaceutical company", "manufacturing company", "logistics company"],''')
    swap('''    "clients": ("Industry / compliance fit (+25), mid-market and no government tender needed (+25), evidence "
                "of legacy-system or cloud-hesitancy pain (+20), startup / new-vendor friendly (+15), "
                "decision-maker identifiable (+10), location (+5)."),''',
         '''    "clients": ("Follows the ICP: Tier A industry (+30; Tier B +15; Tier C +5), 100-2,000 employees (+20), "
                "tech signal such as SAP, Oracle, Tally or on-prem data (+20), a trigger such as DPDP, RBI or "
                "IRDAI pressure or a CIO/CTO new in role (+15), an ICP buyer such as a CIO, CTO, Head of IT or "
                "Chief Data Officer is identifiable (+10), India or UAE (+5)."),''')
    swap("MODE_NOTES = {", '''# v4 ICP: the CLIENTS definition comes from the ICP section of the promptiq-growth SKILL.md
SKILL_PATH = os.path.expanduser(os.environ.get("PROMPTIQ_SKILL", "~/.hermes/skills/promptiq-growth/SKILL.md"))
DEFAULT_ICP = r"""''' + ICP_BLOCK.split("\n", 1)[1].rsplit("\n", 1)[0] + '''"""


def load_icp():
    try:
        text = open(SKILL_PATH, encoding="utf-8").read()
        m = re.search(r"<!-- ICP:START -->(.*?)<!-- ICP:END -->", text, re.DOTALL)
        if m and m.group(1).strip():
            return m.group(1).strip()
    except OSError:
        pass
    return DEFAULT_ICP.strip()


def mode_definition(mode):
    if mode == "clients":
        return ("A company that matches PromptIQ's ICP below. It is relevant only if it is Tier A, B or C; "
                "anything on the anti-ICP list is not relevant.\\n" + load_icp())
    return MODE_DEFINITIONS[mode]


def extra_fields(mode):
    if mode == "clients":
        return (' Add these fields too: "tier" ("A", "B" or "C" per the ICP, or "" if not relevant), '
                '"hook" (one sentence on why PromptIQ fits them now, based on your evidence) and '
                '"use_case" (one concrete pilot use case, for example "MIS report from SAP in under a minute").')
    return ' Add this field too: "hook" (one sentence on why they would care about PromptIQ, based on your evidence).'


MODE_NOTES = {''')
    swap('''f"What counts as relevant for {mode}: {MODE_DEFINITIONS[mode]}\\n"''',
         '''f"What counts as relevant for {mode}: {mode_definition(mode)}\\n"''')
    swap('''        'breakdown, or the reason it was rejected), "source" (a URL you actually used, or "" if none). '\n''',
         '''        'breakdown, or the reason it was rejected), "source" (a URL you actually used, or "" if none). '\n        f"{extra_fields(mode)} "\n''')
    swap('''            "source": str(item.get("source", "")).strip(),\n        }''',
         '''            "source": str(item.get("source", "")).strip(),
            "tier": str(item.get("tier", "") or "").strip().upper()[:1],
            "hook": str(item.get("hook", "") or "").strip(),
            "use_case": str(item.get("use_case", "") or "").strip(),
        }''')
    swap('''            "source": "https://example.com/source"}''',
         '''            "source": "https://example.com/source",
            "tier": ("A" if n % 3 else "B") if relevant else "",
            "hook": "Sample hook: why PromptIQ fits them now." if relevant else "",
            "use_case": "MIS report from SAP in under a minute" if relevant and mode == "clients" else ""}''')
    swap('if __name__ == "__main__":', '''@app.route("/api/mail-config")
def api_mail_config():
    return jsonify(mailer.public_config())


@app.route("/api/draft", methods=["POST"])
def api_draft():
    b = request.get_json(force=True, silent=True) or {}
    mode = b.get("mode") if b.get("mode") in MODE_QUERIES else "clients"
    d = mailer.draft(mode, b.get("lead") or {}, b.get("contact") or {}, mailer.config()["from_name"])
    return jsonify({"ok": True, **d})


@app.route("/api/send", methods=["POST"])
def api_send():
    b = request.get_json(force=True, silent=True) or {}
    r = mailer.send(b.get("to"), b.get("subject"), b.get("body"), b.get("verdict"),
                    company=b.get("company", ""), allow_unverified=bool(b.get("allow_unverified")))
    return jsonify(r), (200 if r.get("ok") else 400)


if __name__ == "__main__":''')
    open(p, "w", encoding="utf-8").write(s)
    print("app.py: updated")

# ---- 3. enrich.py looks for ICP buyers, and 'director' no longer counts as 'cto' ----
p = os.path.join(base, "enrich.py")
s = open(p, encoding="utf-8").read()
if '"clients": ["CIO", "CTO", "Head of IT"]' in s:
    print("enrich.py: already updated")
else:
    for old, new in [
        ('    "clients": ["CTO", "Founder"],', '    "clients": ["CIO", "CTO", "Head of IT"],'),
        ('''    "clients": ["cto", "chief technology", "cio", "chief information", "head of it", "it director",
                "vp engineering", "head of engineering", "ceo", "founder", "director"],''',
         '''    "clients": ["chief information officer", "cio", "chief technology officer", "cto", "head of it", "it head",
                "vp it", "chief data officer", "chief digital officer", "cdo", "chief financial officer", "cfo",
                "chief operating officer", "coo", "it director", "director"],'''),
        ('        pos = next((i for i, k in enumerate(kws) if k in t), len(kws))',
         '        pos = next((i for i, k in enumerate(kws) if re.search(r"\\b" + re.escape(k) + r"\\b", t)), len(kws))'),
    ]:
        assert old in s, "enrich.py: could not find: " + old.strip()[:70]
        s = s.replace(old, new, 1)
    open(p, "w", encoding="utf-8").write(s)
    print("enrich.py: updated")
