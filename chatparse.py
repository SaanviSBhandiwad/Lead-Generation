"""
Turn a chat message like "5 Tier A NBFC clients near Andheri, Mumbai within 5 km" into a search:
    {mode, area, radius_km, count, keywords, rules}

Uses an OpenAI-compatible model when a key is available (default: Ollama Cloud with OLLAMA_API_KEY,
model gpt-oss:120b), and always falls back to built-in rules, so the chat works without it.
Settings (environment or ~/.hermes/.env): CHAT_LLM_BASE, CHAT_LLM_MODEL, CHAT_LLM_KEY / OLLAMA_API_KEY.
"""

import json
import os
import re
import urllib.error
import urllib.request

MOCK_MODE = os.environ.get("MOCK", "0") == "1"
MODES = ("investors", "clients", "workspaces", "partners")
NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
             "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15}

# words -> Google Maps search phrases, per mode
CLIENT_TERMS = [("housing finance", "housing finance company"), ("small finance bank", "small finance bank"),
                ("nbfc", "NBFC"), ("insur", "insurance company"), ("hospital", "hospital"),
                ("diagnos", "diagnostic centre"), ("pharma", "pharmaceutical company"),
                ("manufactur", "manufacturing company"), ("logistic", "logistics company"),
                ("bank", "bank")]
INVESTOR_TERMS = [("angel", "angel investor network"), ("seed", "seed stage venture capital"),
                  ("venture", "venture capital firm"), ("vc", "venture capital firm"),
                  ("family office", "family office")]
PARTNER_TERMS = [("jetson", "NVIDIA Jetson reseller"), ("integrator", "system integrator IT solutions"),
                 ("reseller", "IT reseller"), ("edge", "edge computing AI hardware company"),
                 ("embedded", "embedded systems company")]
WORKSPACE_TERMS = [("cowork", "coworking space"), ("serviced office", "serviced office"),
                   ("office space", "office space for rent")]

CITIES = {"mumbai", "bombay", "bangalore", "bengaluru", "delhi", "new delhi", "delhi ncr", "ncr", "gurgaon",
          "gurugram", "noida", "hyderabad", "chennai", "pune", "ahmedabad", "kolkata", "jaipur", "kochi", "cochin",
          "coimbatore", "indore", "chandigarh", "lucknow", "dubai", "abu dhabi", "sharjah"}
STOP = r"(?:within|with|for|that|who|which|having|tier|seed|under|over|between|and\s+\d|using|focus|only|$)"


def _env(name):
    v = os.environ.get(name, "").strip()
    if v:
        return v
    try:
        with open(os.path.expanduser("~/.hermes/.env"), encoding="utf-8") as f:
            for line in f:
                m = re.match(r"\s*(?:export\s+)?" + re.escape(name) + r"\s*=\s*(.*)", line)
                if m:
                    val = re.split(r"[\s#]", m.group(1).strip().strip('"').strip("'"))[0]
                    if val:
                        v = val
    except OSError:
        pass
    return v


def _has(text, word):
    return re.search(r"\b" + re.escape(word), text) is not None


def _terms(text, table):
    out = []
    for word, phrase in table:
        if _has(text, word) and phrase not in out:
            if phrase == "bank" and any(p in out for p in ("small finance bank",)):
                continue
            out.append(phrase)
    return out[:6]


def rule_parse(text):
    raw = (text or "").strip()
    t = raw.lower()

    # mode
    if re.search(r"\b(investor|investors|vc|vcs|venture|angel|angels|fund|funds)\b", t):
        mode = "investors"
    elif re.search(r"\b(cowork\w*|workspace\w*|office space|desk|desks)\b", t):
        mode = "workspaces"
    elif re.search(r"\b(partner\w*|integrator\w*|reseller\w*|distributor\w*|channel)\b", t):
        mode = "partners"
    else:
        mode = "clients"

    # radius (before count, so "5 km" is not read as a count)
    radius = None
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:km|kms|kilomet\w*)", t)
    if m:
        radius = float(m.group(1))
    t_no_radius = re.sub(r"(\d+(?:\.\d+)?)\s*(?:km|kms|kilomet\w*)", " ", t)
    t_no_radius = re.sub(r"\d[\d,]*\s*(?:-|to)\s*\d[\d,]*\s*(?:employees|people|staff)", " ", t_no_radius)
    team = re.search(r"(?:team|group)\s+of\s+(\d+)|for\s+(\d+)\s+(?:people|persons|members|seats)", t_no_radius)
    if team:
        t_no_radius = t_no_radius.replace(team.group(0), " ")

    # count
    count = None
    m = re.search(r"\b(\d{1,2})\b", t_no_radius)
    if m:
        count = int(m.group(1))
    else:
        for w, n in NUM_WORDS.items():
            if re.search(r"\b" + w + r"\b", t_no_radius):
                count = n
                break

    # area: words after in / near / around / at, up to a stop word or punctuation
    area = ""
    m = re.search(r"\b(?:in|near|around|at)\s+(.+?)\s*(?=\b" + STOP + r"|[.;!?]|$)", raw, re.IGNORECASE)
    if m:
        area = m.group(1).strip(" ,")
        area = re.sub(r"\b(?:\d+(?:\.\d+)?\s*(?:km|kms)|radius)\b.*$", "", area, flags=re.IGNORECASE).strip(" ,")
    if area.lower() in ("the", "a", "an", "", "india"):
        area = ""

    # keywords for the map
    table = {"clients": CLIENT_TERMS, "investors": INVESTOR_TERMS, "partners": PARTNER_TERMS,
             "workspaces": WORKSPACE_TERMS}[mode]
    keywords = _terms(t, table)
    if mode == "investors" and keywords == ["seed stage venture capital"]:
        keywords = []          # "seed" alone is a rule, not a reason to narrow the map search

    # rules every result must follow
    rules = []
    m = re.search(r"\btier\s*([abc])\b", t)
    if m:
        rules.append(f"Tier {m.group(1).upper()} only")
    m = re.search(r"(\d[\d,]*)\s*(?:-|to)\s*(\d[\d,]*)\s*(?:employees|people|staff)", t)
    if m:
        rules.append(f"between {m.group(1)} and {m.group(2)} employees")
    if mode == "investors" and re.search(r"\bseed\b", t):
        rules.append("invests at seed stage")
    if team:
        rules.append(f"has space for a team of {team.group(1) or team.group(2)}")
    clause = re.search(r"\b(?:that|who|which)\s+(.+)$", raw, re.IGNORECASE)
    clause = clause.group(1).strip(" .") if clause and len(clause.group(1)) > 3 else ""
    tech = [x.upper() if x != "tally" else "Tally" for x in ("sap", "oracle", "tally")
            if _has(t, x) and not _has(clause.lower(), x)]
    if tech:
        rules.append("uses " + " or ".join(tech))
    if clause:
        rules.append(clause)
    return {"mode": mode, "area": area, "radius_km": radius, "count": count,
            "keywords": keywords, "rules": "; ".join(dict.fromkeys(rules))}


SYSTEM = (
    "You turn a request for business leads into search settings for PromptIQ's lead finder. "
    "Reply with ONLY a JSON object, no other text, with these keys: "
    '"mode": one of "investors", "clients", "workspaces", "partners" (customers or buyers are "clients"); '
    '"area": the place to search, as written, e.g. "Andheri, Mumbai" ("" if none given); '
    '"radius_km": number or null; "count": integer or null; '
    '"keywords": up to 6 short Google Maps search phrases for the kind of place, e.g. ["NBFC", "hospital"] '
    '([] to use the defaults); '
    '"rules": one short sentence of conditions every result must meet, e.g. "Tier A only; uses SAP" ("" if none).'
)


def _first_json(text):
    dec = json.JSONDecoder()
    for i, ch in enumerate(text or ""):
        if ch == "{":
            try:
                obj, _ = dec.raw_decode(text[i:])
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                continue
    return None


def llm_parse(text, timeout=25):
    key = _env("CHAT_LLM_KEY") or _env("OLLAMA_API_KEY")
    if MOCK_MODE or not key:
        return None
    base = (_env("CHAT_LLM_BASE") or "https://ollama.com/v1").rstrip("/")
    model = _env("CHAT_LLM_MODEL") or "gpt-oss:120b"
    body = {"model": model, "temperature": 0, "max_tokens": 1200,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}]}
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        content = data["choices"][0]["message"].get("content") or ""
    except (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError, OSError):
        return None
    return _first_json(content)


def _clean(p):
    out = {}
    mode = str(p.get("mode") or "").lower().strip()
    out["mode"] = mode if mode in MODES else None
    out["area"] = str(p.get("area") or "").strip()[:120]
    try:
        out["radius_km"] = max(0.5, min(50.0, float(p["radius_km"]))) if p.get("radius_km") not in (None, "") else None
    except (TypeError, ValueError):
        out["radius_km"] = None
    try:
        out["count"] = max(1, min(15, int(p["count"]))) if p.get("count") not in (None, "") else None
    except (TypeError, ValueError):
        out["count"] = None
    kw = p.get("keywords") or []
    out["keywords"] = [str(k).strip()[:60] for k in kw if str(k).strip()][:6] if isinstance(kw, list) else []
    out["rules"] = str(p.get("rules") or "").strip()[:300]
    return out


def parse(text, pending=None):
    """Returns {search, missing, parser}. `pending` is an earlier search that was missing its area."""
    rules = _clean(rule_parse(text))
    ai = llm_parse(text)
    s = _clean(ai) if ai else {}
    parser = "ai" if ai else "rules"
    search = {k: (s.get(k) if s.get(k) not in (None, "", []) else rules.get(k)) for k in rules}
    for k in ("count", "radius_km"):          # numbers you typed are read exactly by the rules, so they win
        if rules.get(k):
            search[k] = rules[k]
    if pending:
        # a reply to "Which area?" fills the gap; everything else stays from the original request
        area = search["area"] or text.strip(" .")
        search = dict(pending, area=area[:120])
    search["mode"] = search["mode"] or "clients"
    search["count"] = search["count"] or 5
    if not search["radius_km"] and search["area"]:
        city = re.sub(r",?\s*(india|uae|karnataka|maharashtra)$", "", search["area"].strip().lower()).strip()
        search["radius_km"] = 25.0 if city in CITIES else 5.0
    search["keywords"] = search["keywords"] or []
    search["rules"] = search["rules"] or ""
    missing = None if search["area"] else "area"
    return {"search": search, "missing": missing, "parser": parser}


def describe(search):
    kinds = {"investors": "investors", "clients": "clients", "workspaces": "workspaces", "partners": "partners"}
    r = search["radius_km"]
    radius = f"{int(r)}" if float(r).is_integer() else f"{r}"
    out = f"Finding {search['count']} {kinds[search['mode']]} within {radius} km of {search['area']}"
    if search["keywords"]:
        out += ", searching the map for " + ", ".join(search["keywords"])
    if search["rules"]:
        out += ". Rules: " + search["rules"]
    return out + "."
