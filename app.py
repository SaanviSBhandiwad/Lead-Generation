"""
PromptIQ Growth Agent — v3 (Maps first).

How a run works
---------------
1. LOCATE   Google Places (your own GOOGLE_MAPS_API_KEY) finds real places in the
            area you typed. Name, address, coordinates and business status come
            straight from Google, so they are never guessed and never empty.
2. FILTER   Plain code drops places that are closed, have no address, or are
            outside your radius. "Near Indiranagar" is enforced here, not left
            to the AI.
3. RESEARCH Hermes gets small batches of those verified places. It may only web
            search and score them; it never writes addresses or coordinates.
            Batches repeat until you have `count` accepted results or the pool
            runs out.
4. RETURN   Top `count` by score. If fewer than `count` genuinely qualify, you get
            fewer, plus the reason. Nothing is padded with unverified results.

Run:   python3 app.py          (real)
       MOCK=1 python3 app.py   (fake data, no Google key / Hermes needed)
Open:  http://127.0.0.1:5050
"""

import json
import math
import os
import random
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid

from flask import Flask, jsonify, request, send_from_directory

import enrich as treg_enrich
import chatparse
import mailer

app = Flask(__name__, template_folder="templates")

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

HERMES_TIMEOUT_SECONDS = int(os.environ.get("HERMES_TIMEOUT_SECONDS", "420"))  # per batch
MOCK_MODE = os.environ.get("MOCK", "0") == "1"
PLACES_LITE = os.environ.get("PLACES_LITE", "0") == "1"  # skip website/phone/rating fields

BATCH_SIZE = 3      # places researched per Hermes call
MAX_ROUNDS = 4      # max Hermes calls per run
MAX_POOL = 60       # max verified places kept per run
MAX_COUNT = 15

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"

JSON_START = "===PROMPTIQ_JSON_START==="
JSON_END = "===PROMPTIQ_JSON_END==="

# ---------------------------------------------------------------------------
# What to look for on the map, per mode (used unless you type keywords)
# ---------------------------------------------------------------------------

MODE_QUERIES = {
    "investors": ["venture capital firm", "seed stage venture capital", "angel investor network",
                  "startup investment fund"],
    "clients": ["NBFC", "housing finance company", "small finance bank", "insurance company", "hospital",
                "diagnostic centre", "pharmaceutical company", "manufacturing company", "logistics company"],
    "workspaces": ["coworking space", "serviced office"],
    "partners": ["NVIDIA Jetson reseller", "system integrator IT solutions",
                 "edge computing AI hardware company", "embedded systems company"],
}

MODE_DEFINITIONS = {
    "investors": (
        "An institutional venture capital fund, seed fund, angel network or fund-backed accelerator that "
        "invests in early-stage startups and genuinely operates from this address. NOT relevant: law, "
        "accounting or consulting firms, banks, brokers, coworking spaces, or unrelated businesses with a "
        "similar name."
    ),
    "clients": (
        "A privately held, mid-market company (roughly 100-1500 employees) in a regulated or "
        "data-sensitive industry that could realistically buy on-premise / edge AI. NOT relevant: PSUs, "
        "government bodies, defence PSUs, India offices of global giants, very small shops, competitors."
    ),
    "workspaces": (
        "A coworking or serviced-office space that could host a seed-stage AI startup team and receive "
        "enterprise visitors."
    ),
    "partners": (
        "A technology or channel partner: NVIDIA Jetson reseller or integrator, systems integrator for "
        "regulated industries, edge-hardware vendor, or a complementary non-competing AI infrastructure firm."
    ),
}

SCORING_RUBRIC = {
    "investors": ("Sector match to enterprise AI / infra / deep tech (+30), realistic seed-stage cheque "
                  "writer (+25), Bangalore or India focus (+15), a named relevant portfolio company (+20), "
                  "recent activity with a dated source (+10)."),
    "clients": ("Follows the ICP: Tier A industry (+30; Tier B +15; Tier C +5), 100-2,000 employees (+20), "
                "tech signal such as SAP, Oracle, Tally or on-prem data (+20), a trigger such as DPDP, RBI or "
                "IRDAI pressure or a CIO/CTO new in role (+15), an ICP buyer such as a CIO, CTO, Head of IT or "
                "Chief Data Officer is identifiable (+10), India or UAE (+5)."),
    "workspaces": ("Location and commute convenience (+30), price fit for a seed-stage budget (+25), "
                   "amenities for hosting enterprise clients (+25), ratings and reviews (+20)."),
    "partners": ("Strategic fit and complementarity (+35), overlap with enterprise client base (+30), "
                 "geographic reach (+15), evidence of similar partnerships (+20)."),
}

# v4 ICP: the CLIENTS definition comes from the ICP section of the promptiq-growth SKILL.md
SKILL_PATH = os.path.expanduser(os.environ.get("PROMPTIQ_SKILL", "~/.hermes/skills/promptiq-growth/SKILL.md"))
DEFAULT_ICP = r"""## PromptIQ ICP (the web app reads this section for CLIENTS searches)

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

**Anti-ICP, not relevant:** defence and PSU tenders, companies under 50 people, pure cloud-native SaaS companies."""


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
                "anything on the anti-ICP list is not relevant.\n" + load_icp())
    return MODE_DEFINITIONS[mode]


def extra_fields(mode):
    if mode == "clients":
        return (' Add these fields too: "tier" ("A", "B" or "C" per the ICP, or "" if not relevant), '
                '"hook" (one sentence on why PromptIQ fits them now, based on your evidence) and '
                '"use_case" (one concrete pilot use case, for example "MIS report from SAP in under a minute").')
    return ' Add this field too: "hook" (one sentence on why they would care about PromptIQ, based on your evidence).'


MODE_NOTES = {
    "investors": ("Only investors with a Google Maps listing can be address-verified. Individual angels "
                  "have no listing, so they are not included."),
}

# ---------------------------------------------------------------------------
# Google Places
# ---------------------------------------------------------------------------


class PlacesError(Exception):
    pass


class PipelineError(Exception):
    def __init__(self, message, detail=None):
        super().__init__(message)
        self.detail = detail


def load_maps_key():
    """Key from the environment, else from ~/.hermes/.env (last matching line wins)."""
    key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if key:
        return key
    path = os.path.expanduser("~/.hermes/.env")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                m = re.match(r"\s*(?:export\s+)?GOOGLE_MAPS_API_KEY\s*=\s*(.*)", line)
                if m:
                    val = m.group(1).strip().strip('"').strip("'")
                    val = re.split(r"[\s#]", val)[0]  # drop anything glued on after the key
                    if val:
                        key = val
    except OSError:
        pass
    return key


def field_mask():
    fields = ["places.id", "places.displayName", "places.formattedAddress", "places.location",
              "places.businessStatus", "places.googleMapsUri"]
    if not PLACES_LITE:
        fields += ["places.websiteUri", "places.nationalPhoneNumber", "places.rating",
                   "places.userRatingCount"]
    fields.append("nextPageToken")
    return ",".join(fields)


def places_error_message(code, body):
    try:
        msg = json.loads(body)["error"]["message"]
    except Exception:
        msg = (body or "")[:300]
    low = msg.lower()
    hint = ""
    if "billing" in low:
        hint = " Fix: enable billing on your Google Cloud project."
    elif "has not been used" in low or "is disabled" in low or "not enabled" in low:
        hint = " Fix: enable 'Places API (New)' for this project in the Google Cloud API Library."
    elif "referer" in low or "ip address" in low or "application restriction" in low:
        hint = " Fix: this app calls Google from your own computer, so remove the HTTP-referrer / IP restriction on the key (keep only the API restriction)."
    elif "api key not valid" in low or "api_key_invalid" in low:
        hint = " Fix: the key in ~/.hermes/.env is wrong or was deleted. Create a new key."
    elif "api_key_service_blocked" in low or "blocked" in low:
        hint = " Fix: add 'Places API (New)' to the key's allowed APIs."
    return f"Google Places returned {code}: {msg}.{hint}"


def places_text_search(query, key, center=None, radius_m=None, max_pages=2, page_size=20):
    places, token = [], None
    for page in range(max_pages):
        body = {"textQuery": query, "pageSize": page_size}
        if center and radius_m:
            body["locationBias"] = {"circle": {
                "center": {"latitude": center[0], "longitude": center[1]},
                "radius": float(min(radius_m, 50000))}}
        if token:
            body["pageToken"] = token
        req = urllib.request.Request(
            PLACES_URL, data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "X-Goog-Api-Key": key,
                     "X-Goog-FieldMask": field_mask()})
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if page > 0:
                break  # losing page 2 is fine
            raise PlacesError(places_error_message(e.code, e.read().decode("utf-8", "replace")))
        except (urllib.error.URLError, TimeoutError) as e:
            if page > 0:
                break
            raise PlacesError(f"Could not reach Google Places: {e}")
        places.extend(data.get("places", []))
        token = data.get("nextPageToken")
        if not token or page_size == 1:
            break
        time.sleep(1.0)  # next-page tokens need a moment before they work
    return places


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def filter_places(places, center, radius_km):
    """Keep only open, addressed, de-duplicated places inside the radius."""
    kept, dropped, seen = [], {"closed": 0, "outside_radius": 0, "no_address": 0, "duplicate": 0}, set()
    for p in places:
        pid = p.get("id")
        if not pid or pid in seen:
            dropped["duplicate"] += 1
            continue
        seen.add(pid)
        status = p.get("businessStatus")
        if status and status != "OPERATIONAL":
            dropped["closed"] += 1
            continue
        loc = p.get("location") or {}
        lat, lng, addr = loc.get("latitude"), loc.get("longitude"), p.get("formattedAddress")
        if not addr or lat is None or lng is None:
            dropped["no_address"] += 1
            continue
        dist = haversine_km(center[0], center[1], lat, lng)
        if dist > radius_km:
            dropped["outside_radius"] += 1
            continue
        kept.append({
            "place_id": pid,
            "name": (p.get("displayName") or {}).get("text", "Unnamed place"),
            "address": addr, "latitude": lat, "longitude": lng,
            "business_status": status or "OPERATIONAL",
            "website": p.get("websiteUri", ""), "phone": p.get("nationalPhoneNumber", ""),
            "rating": p.get("rating"), "rating_count": p.get("userRatingCount"),
            "maps_url": p.get("googleMapsUri", ""), "distance_km": round(dist, 2),
        })
    return kept, dropped


def merge_round_robin(lists):
    out, i = [], 0
    while any(i < len(l) for l in lists):
        for l in lists:
            if i < len(l):
                out.append(l[i])
        i += 1
    return out


# ---------------------------------------------------------------------------
# Hermes research (no addresses, no coordinates, no Maps)
# ---------------------------------------------------------------------------


class ResearchError(Exception):
    def __init__(self, message, raw=""):
        super().__init__(message)
        self.raw = raw


def build_research_prompt(mode, batch, extra):
    items = [{"place_id": c["place_id"], "name": c["name"], "address": c["address"],
              "website": c.get("website") or ""} for c in batch]
    return (
        f"You are qualifying {mode} leads for PromptIQ AI, a Bangalore seed-stage startup selling "
        "on-premise / edge enterprise AI. Each candidate below was found on Google Maps, so its name and "
        "address are already verified. Do NOT use any Maps or Sheets tool and do NOT output addresses or "
        "coordinates. Use only web search / page-read tools, at most 2 lookups per candidate (read the "
        "website first if one is listed). Do not load or follow any skill: everything you need is in this "
        "message. Your final message must BE the JSON block itself, never a note about writing it.\n\n"
        f"Candidates (JSON):\n{json.dumps(items, indent=2)}\n\n"
        f"What counts as relevant for {mode}: {mode_definition(mode)}\n"
        f"Rules every accepted result must also satisfy (meets_instructions): {extra or 'none'}\n"
        f"Scoring rubric: {SCORING_RUBRIC[mode]}\n\n"
        "For EACH candidate output one object with exactly these fields: "
        '"place_id" (copy unchanged), "relevant" (true or false), "meets_instructions" (true or false), '
        '"score" (integer 0-100, 0 if not relevant), "reasoning" (max 2 sentences with a brief score '
        'breakdown, or the reason it was rejected), "source" (a URL you actually used, or "" if none). '
        f"{extra_fields(mode)} "
        "If you cannot find real evidence, set relevant to false. Never guess. Work silently, no commentary. "
        f"Output ONLY a JSON array wrapped between the literal markers {JSON_START} and {JSON_END} on "
        "their own lines."
    )


# v3.1 robust parser
def _json_candidates(text):
    """Possible JSON arrays in Hermes output, newest first."""
    cands, pos, pairs = [], 0, []
    while True:
        i = text.find(JSON_START, pos)
        if i < 0:
            break
        j = text.find(JSON_END, i + len(JSON_START))
        if j < 0:
            break
        pairs.append(text[i + len(JSON_START):j])
        pos = j + len(JSON_END)
    cands += list(reversed(pairs))
    cands += list(reversed(re.findall(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)))
    cands += [text[m.start():] for m in reversed(list(re.finditer(r"(?m)^\s*\[", text)))]
    return cands


def extract_json_block(raw):
    """Find the verdict list Hermes wrote. The echoed prompt (which also contains the markers
    and the candidate list) is skipped because only arrays of verdicts are accepted."""
    dec = json.JSONDecoder()
    for cand in _json_candidates(raw):
        c = cand.strip()
        if not c.startswith("["):
            continue
        try:
            data, _ = dec.raw_decode(c)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list) and data and all(
                isinstance(x, dict) and "place_id" in x and ("relevant" in x or "score" in x) for x in data):
            return data, None
    return None, "No usable JSON in Hermes output (it may have answered in prose or been cut off)."


def _as_bool(v):
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "yes", "1")


def parse_research_output(raw, valid_ids):
    data, err = extract_json_block(raw)
    if err:
        raise ResearchError(err, raw[-3000:])
    verdicts = {}
    for item in data:
        if not isinstance(item, dict) or item.get("place_id") not in valid_ids:
            continue  # ignore invented or mangled ids
        try:
            score = int(round(float(item.get("score", 0))))
        except (TypeError, ValueError):
            score = 0
        verdicts[item["place_id"]] = {
            "relevant": _as_bool(item.get("relevant")),
            "meets_instructions": _as_bool(item.get("meets_instructions")),
            "score": max(0, min(100, score)),
            "reasoning": str(item.get("reasoning", "")).strip(),
            "source": str(item.get("source", "")).strip(),
            "tier": str(item.get("tier", "") or "").strip().upper()[:1],
            "hook": str(item.get("hook", "") or "").strip(),
            "use_case": str(item.get("use_case", "") or "").strip(),
        }
    return verdicts


LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")


def save_raw(text):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        path = os.path.join(LOG_DIR, f"research-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path
    except OSError:
        return None


def run_hermes(prompt):
    r = subprocess.run(["hermes", "chat", "-q", prompt], capture_output=True, text=True,
                       timeout=HERMES_TIMEOUT_SECONDS)
    return r.stdout + (("\n[stderr]\n" + r.stderr) if r.stderr else "")


def research_batch(mode, batch, extra):
    # v3.2 retry: some models end on a planning note instead of the answer, so try once more
    prompt = build_research_prompt(mode, batch, extra)
    ids = {c["place_id"] for c in batch}
    last = None
    for attempt in (1, 2):
        try:
            raw = run_hermes(prompt)
        except FileNotFoundError:
            raise ResearchError("The 'hermes' command was not found on this machine.")
        except subprocess.TimeoutExpired:
            raise ResearchError(f"Hermes did not finish a batch within {HERMES_TIMEOUT_SECONDS}s.")
        path = save_raw(raw)
        try:
            return parse_research_output(raw, ids)
        except ResearchError as e:
            last = ResearchError(f"{e} Raw output saved to {path}." if path else str(e), e.raw)
    raise ResearchError(f"{last} (tried twice)", last.raw)


# ---------------------------------------------------------------------------
# Mock mode (no Google key, no Hermes) so the screen can be tried instantly
# ---------------------------------------------------------------------------

MOCK_CENTERS = {"indiranagar": (12.9784, 77.6408), "koramangala": (12.9352, 77.6245),
                "hsr": (12.9121, 77.6446), "whitefield": (12.9698, 77.7500)}


def mock_geocode(area):
    low = area.lower()
    for k, v in MOCK_CENTERS.items():
        if k in low:
            return v
    return (12.9716, 77.5946)


def mock_pool(mode, area, center, radius_km):
    rnd = random.Random(f"{mode}|{area}|{radius_km}")
    nouns = {"investors": "Capital", "clients": "Technologies", "workspaces": "Cowork", "partners": "Systems"}
    words = ["Orion", "Vertex", "Lumen", "Nimbus", "Cobalt", "Apex", "Kestrel", "Mosaic", "Talon",
             "Zenith", "Aurora", "Pylon", "Quanta", "Ridge"]
    out = []
    for i, w in enumerate(words):
        ang, dist = rnd.uniform(0, 2 * math.pi), radius_km * math.sqrt(rnd.uniform(0.02, 0.95))
        lat = center[0] + (dist * math.cos(ang)) / 111.0
        lng = center[1] + (dist * math.sin(ang)) / (111.0 * math.cos(math.radians(center[0])))
        out.append({
            "place_id": f"mock-{i}", "name": f"{w} {nouns[mode]}",
            "address": f"{rnd.randint(10, 400)}, {rnd.choice(['12th Main', '100 Feet Rd', '5th Cross', 'CMH Rd'])}, "
                       f"{area.split(',')[0].title()}, Bengaluru, Karnataka {rnd.choice([560038, 560034, 560102])}",
            "latitude": lat, "longitude": lng, "business_status": "OPERATIONAL",
            "website": f"https://example.com/{w.lower()}", "phone": "", "rating": round(rnd.uniform(3.8, 4.9), 1),
            "rating_count": rnd.randint(8, 400), "maps_url": f"https://www.google.com/maps/search/?api=1&query={lat},{lng}",
            "distance_km": round(haversine_km(center[0], center[1], lat, lng), 2)})
    return out


def mock_research(mode, batch, extra):
    time.sleep(1.2)
    verdicts = {}
    for c in batch:
        n = int(c["place_id"].split("-")[1])
        rnd = random.Random(c["place_id"])
        relevant = n % 4 != 3
        verdicts[c["place_id"]] = {
            "relevant": relevant, "meets_instructions": True,
            "score": rnd.randint(62, 97) if relevant else 0,
            "reasoning": ("Mock result: strong fit, with a sample score breakdown." if relevant
                          else "Mock result: not a real match for this mode."),
            "source": "https://example.com/source",
            "tier": ("A" if n % 3 else "B") if relevant else "",
            "hook": "Sample hook: why PromptIQ fits them now." if relevant else "",
            "use_case": "MIS report from SAP in under a minute" if relevant and mode == "clients" else ""}
    return verdicts


# ---------------------------------------------------------------------------
# The pipeline (runs in a background thread; the page polls for progress)
# ---------------------------------------------------------------------------

JOBS = {}
JOBS_LOCK = threading.Lock()


def new_job():
    job_id = uuid.uuid4().hex[:10]
    job = {"id": job_id, "status": "running", "message": "Starting...", "log": [], "center": None,
           "radius_km": None, "candidates": [], "result": None, "error": None, "detail": None,
           "started": time.time()}
    with JOBS_LOCK:
        JOBS[job_id] = job
        for old in sorted(JOBS, key=lambda k: JOBS[k]["started"])[:-20]:
            JOBS.pop(old, None)
    return job


def build_pool(mode, area, center, radius_km, keywords, key, log):
    queries = keywords or MODE_QUERIES[mode]
    lists = []
    for q in queries:
        full = f"{q} in {area}"
        found = places_text_search(full, key, center, radius_km * 1000)
        log(f"Google Maps: '{full}' returned {len(found)} places")
        lists.append(found)
    return filter_places(merge_round_robin(lists), center, radius_km)


def run_pipeline(job, params):
    log = lambda m: (job["log"].append(m), job.update(message=m))
    try:
        mode, count, area = params["mode"], params["count"], params["area"]
        radius_km, keywords, extra = params["radius_km"], params["keywords"], params["extra"]
        key = load_maps_key()
        if not MOCK_MODE and not key:
            raise PipelineError("No GOOGLE_MAPS_API_KEY found. Add it to ~/.hermes/.env on its own line.")

        # 1. locate the area
        log(f"Locating '{area}'...")
        if MOCK_MODE:
            center = mock_geocode(area)
        else:
            hits = places_text_search(area, key, max_pages=1, page_size=1)
            loc = (hits[0].get("location") if hits else None) or {}
            if "latitude" not in loc:
                raise PipelineError(f"Google Maps could not find an area called '{area}'. Try a fuller name, e.g. 'Indiranagar, Bangalore'.")
            center = (loc["latitude"], loc["longitude"])
        job["center"], job["radius_km"] = center, radius_km

        # 2. find and verify places on the map
        if MOCK_MODE:
            time.sleep(0.8)
            pool, dropped = mock_pool(mode, area, center, radius_km), {}
            log(f"Mock mode: generated {len(pool)} sample places")
        else:
            pool, dropped = build_pool(mode, area, center, radius_km, keywords, key, log)
        pool = pool[:MAX_POOL]
        job["candidates"] = pool
        log(f"{len(pool)} open places with a verified address within {radius_km} km of {area}")

        warnings, rejected, accepted = [], [], []
        researched = unresearched = 0
        last_error = None

        # 3. research in batches until enough qualify
        queue, rounds = list(pool), 0
        while queue and len(accepted) < count and rounds < MAX_ROUNDS:
            rounds += 1
            need = count - len(accepted)
            batch, queue = queue[:max(BATCH_SIZE, need)], queue[max(BATCH_SIZE, need):]
            log(f"Researching {len(batch)} places (round {rounds} of {MAX_ROUNDS}); {len(accepted)} of {count} accepted so far")
            try:
                verdicts = mock_research(mode, batch, extra) if MOCK_MODE else research_batch(mode, batch, extra)
            except ResearchError as e:
                last_error = e
                warnings.append(f"Round {rounds} failed: {e}")
                log(f"Round {rounds} failed: {e}")
                continue
            for c in batch:
                v = verdicts.get(c["place_id"])
                if v is None:
                    unresearched += 1
                    continue
                researched += 1
                if v["relevant"] and v["meets_instructions"]:
                    accepted.append({**c, **v})
                else:
                    why = v["reasoning"] or ("Did not match your rules" if v["relevant"] else "Not a relevant match")
                    rejected.append({"name": c["name"], "address": c["address"], "reason": why})

        if researched == 0 and last_error is not None:
            raise PipelineError(f"Research failed: {last_error}", getattr(last_error, "raw", ""))

        final = sorted(accepted, key=lambda r: r["score"], reverse=True)[:count]
        if not pool:
            warnings.append(f"No open, addressed places were found within {radius_km} km of {area}. "
                            "Try a larger radius, a broader area, or different keywords.")
        elif len(final) < count:
            parts = [f"Only {len(final)} of {count} qualified."]
            parts.append(f"Google Maps gave {len(pool)} verified places in range; {researched} were researched, "
                         f"{len(rejected)} were rejected.")
            if queue:
                parts.append(f"{len(queue)} more were not researched (limit of {MAX_ROUNDS} rounds).")
            if unresearched:
                parts.append(f"{unresearched} came back without a verdict.")
            parts.append("Widen the radius, change keywords, or relax your rules to find more.")
            warnings.append(" ".join(parts))

        job["result"] = {"requested": count, "returned": len(final), "results": final,
                         "pool_size": len(pool), "researched": researched, "rejected": rejected[:20],
                         "warnings": warnings, "dropped_by_filter": dropped,
                         "elapsed_seconds": round(time.time() - job["started"], 1),
                         "note": MODE_NOTES.get(mode, ""), "mock": MOCK_MODE}
        job["status"] = "done"
        job["message"] = f"Done: {len(final)} of {count} results"
    except (PipelineError, PlacesError) as e:
        job.update(status="error", error=str(e), detail=getattr(e, "detail", None))
    except Exception as e:  # never leave the page waiting forever
        job.update(status="error", error=f"Unexpected error: {e}")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/")
def landing():
    return send_from_directory(app.template_folder, "landing.html")


@app.route("/app")
def index():
    return send_from_directory(app.template_folder, "index.html")


@app.route("/api/health")
def api_health():
    return jsonify({"mock": MOCK_MODE, "maps_key": bool(load_maps_key()),
                    "hermes": bool(shutil.which("hermes")), "max_count": MAX_COUNT})


@app.route("/api/start", methods=["POST"])
def api_start():
    b = request.get_json(force=True, silent=True) or {}
    mode = b.get("mode")
    if mode not in MODE_QUERIES:
        return jsonify({"ok": False, "error": f"Unknown mode: {mode}"}), 400
    try:
        count = max(1, min(MAX_COUNT, int(b.get("count", 3))))
        radius_km = max(0.5, min(50.0, float(b.get("radius_km", 5))))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Count and radius must be numbers."}), 400
    area = (b.get("area") or "").strip() or "Bangalore, India"
    keywords = [k.strip() for k in (b.get("keywords") or "").split(",") if k.strip()][:6]
    extra = (b.get("extra") or "").strip()[:500]
    job = new_job()
    threading.Thread(target=run_pipeline, daemon=True, args=(job, {
        "mode": mode, "count": count, "area": area, "radius_km": radius_km,
        "keywords": keywords, "extra": extra})).start()
    return jsonify({"ok": True, "job_id": job["id"]})


@app.route("/api/job/<job_id>")
def api_job(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"ok": False, "error": "Unknown job (the app may have restarted)."}), 404
    out = {k: job[k] for k in ("id", "status", "message", "center", "radius_km", "candidates",
                               "result", "error", "detail")}
    out["log"] = job["log"][-10:]
    out["elapsed_seconds"] = round(time.time() - job["started"], 1)
    out["ok"] = True
    return jsonify(out)


@app.route("/api/check")
def api_check():
    checks = []

    def add(name, ok, detail):
        checks.append({"name": name, "ok": ok, "detail": detail})

    if MOCK_MODE:
        add("Sample-data mode", True, "Results are fake. Nothing is sent to Google or Hermes.")
    else:
        key = load_maps_key()
        if key:
            add("Google Maps key", True, "Found in ~/.hermes/.env or the environment.")
            try:
                hits = places_text_search("Indiranagar, Bangalore", key, max_pages=1, page_size=1)
                if hits:
                    name = (hits[0].get("displayName") or {}).get("text", "a place")
                    add("Google Places API", True, f"Working. Test search found: {name}.")
                else:
                    add("Google Places API", False, "Google answered but returned nothing for a test search.")
            except PlacesError as e:
                add("Google Places API", False, str(e))
        else:
            add("Google Maps key", False, "Not found. Add GOOGLE_MAPS_API_KEY=... on its own line in ~/.hermes/.env.")
        if shutil.which("hermes"):
            add("Hermes", True, "Found. The model itself is not tested here; run: hermes chat -q hello")
        else:
            add("Hermes", False, "The 'hermes' command is not on this terminal's PATH.")
        add("Treg (optional)", True, "Token found. Enrich and verify is ready." if treg_enrich.load_token()
            else "No TREG_TOKEN set. Only needed for Enrich and verify.")
    return jsonify({"ok": all(c["ok"] for c in checks), "checks": checks})


@app.route("/api/enrich-config")
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


@app.route("/api/mail-config")
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


@app.route("/chat")
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    print(f"PromptIQ Growth Agent v3 at http://127.0.0.1:{port}")
    print("MOCK mode: fake data, no Google key or Hermes needed." if MOCK_MODE else
          f"Google key found: {bool(load_maps_key())} | hermes on PATH: {bool(shutil.which('hermes'))}")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
