"""
Enrich and verify, using Treg (https://treg.to).

For each lead (a real business found on Google Maps) this module:
  1. takes the company domain from its Google Maps website,
  2. finds a decision-maker there        (treg.people.search),
  3. finds that person's work email      (treg.people.email.find, with verification), or,
     if the search already returned an email, verifies it (treg.people.email.verify).

Money safety:
  - every call carries X-Treg-Route-Max-Cost, so Treg refuses (and charges nothing) above that price;
  - each run has a budget (TREG_RUN_BUDGET, default $0.25) and stops before it would go over;
  - an empty balance, a bad token or a rate limit stops the run instead of retrying.

Every Treg response is appended to logs/treg-YYYYMMDD.jsonl so unexpected answers can be inspected.
"""

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlparse

TREG_BASE = os.environ.get("TREG_BASE", "https://treg.to").rstrip("/")
RUN_BUDGET_USD = float(os.environ.get("TREG_RUN_BUDGET", "0.25"))
MOCK_MODE = os.environ.get("MOCK", "0") == "1"
CAP = {"search": 0.02, "find": 0.03, "verify": 0.01}   # USD ceiling per call
MAX_LEADS = 15
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")

# Job titles to search for, and how to rank the people that come back, per mode.
TITLES = {
    "investors": ["Partner", "Founder"],
    "clients": ["CIO", "CTO", "Head of IT"],
    "workspaces": ["Community Manager", "Founder"],
    "partners": ["Founder", "Business Development"],
}
RANK = {
    "investors": ["managing partner", "general partner", "founding partner", "partner", "founder",
                  "principal", "investment director", "vice president", "director"],
    "clients": ["chief information officer", "cio", "chief technology officer", "cto", "head of it", "it head",
                "vp it", "chief data officer", "chief digital officer", "cdo", "chief financial officer", "cfo",
                "chief operating officer", "coo", "it director", "director"],
    "workspaces": ["community manager", "centre manager", "center manager", "sales", "business development",
                   "founder", "ceo", "manager"],
    "partners": ["founder", "ceo", "managing director", "business development", "partnership", "alliances",
                 "sales", "director"],
}
# Websites that are not the company's own domain (looking these up would find the wrong people).
SKIP_DOMAINS = {"facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com", "google.com",
                "goo.gl", "wa.me", "linktr.ee", "youtube.com", "business.site", "justdial.com", "wix.com",
                "wixsite.com", "blogspot.com", "wordpress.com"}
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


class TregStop(Exception):
    """Stop the whole run (bad token, empty balance, rate limit, Treg unreachable)."""
    def __init__(self, message, cost=0.0):
        super().__init__(message)
        self.cost = cost


class TregSkip(Exception):
    """Skip this one call (price above the cap, or Treg refused it)."""
    def __init__(self, message, cost=0.0):
        super().__init__(message)
        self.cost = cost


def load_token():
    """TREG_TOKEN from the environment, else from ~/.hermes/.env (last clean line wins)."""
    tok = os.environ.get("TREG_TOKEN", "").strip()
    if tok:
        return tok
    try:
        with open(os.path.expanduser("~/.hermes/.env"), encoding="utf-8") as f:
            for line in f:
                m = re.match(r"\s*(?:export\s+)?TREG_TOKEN\s*=\s*(.*)", line)
                if m:
                    val = re.split(r"[\s#]", m.group(1).strip().strip('"').strip("'"))[0]
                    if val:
                        tok = val
    except OSError:
        pass
    return tok


def domain_of(url):
    if not url:
        return ""
    host = (urlparse(url if "://" in url else "http://" + url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if "." not in host:
        return ""
    if any(host == d or host.endswith("." + d) for d in SKIP_DOMAINS):
        return ""
    return host


class Budget:
    def __init__(self, limit):
        self.limit, self.spent = limit, 0.0
        self._lock = threading.Lock()

    def can_spend(self, amount):
        return self.spent + amount <= self.limit + 1e-9

    def spend(self, amount):
        with self._lock:
            self.spent += max(0.0, amount or 0.0)


def _log(endpoint, status, headers, data):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        row = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "endpoint": endpoint, "status": status,
               "cost_micro": headers.get("X-Treg-Cost-Micro"), "call_id": headers.get("X-Treg-Call-Id"),
               "served_by": headers.get("X-Treg-Served-By"), "body": data}
        with open(os.path.join(LOG_DIR, f"treg-{time.strftime('%Y%m%d')}.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def treg_call(endpoint, body, cap_usd, token, extra_headers=None):
    """POST to Treg. Returns (status, data, cost_usd, served_by). Raises TregStop / TregSkip."""
    headers = {"Content-Type": "application/json", "X-Treg-Token": token,
               "X-Treg-Meta": "feature=lead-enrichment", "X-Treg-Route-Max-Cost": f"{cap_usd:.4f}"}
    headers.update(extra_headers or {})
    req = urllib.request.Request(f"{TREG_BASE}/call/{endpoint}", data=json.dumps(body).encode("utf-8"),
                                 method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            status, rh, raw = r.status, r.headers, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, rh, raw = e.code, e.headers, e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError) as e:
        raise TregStop(f"Could not reach Treg: {e}")
    try:
        cost = int(rh.get("X-Treg-Cost-Micro") or 0) / 1e6
    except ValueError:
        cost = 0.0
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {"_text": raw[:500]}
    _log(endpoint, status, rh, data)
    low = raw.lower()
    if status in (401, 403):
        raise TregStop("Treg rejected the token. Create an app token with 'treg org agent-new promptiq-app' "
                       "and save it as TREG_TOKEN in ~/.hermes/.env.", cost)
    if status == 400 and "choose an org" in low:
        raise TregStop("This Treg token is not tied to a team. Create one with 'treg org agent-new promptiq-app' "
                       "and save it as TREG_TOKEN.", cost)
    if status == 402:
        if "route_max_cost" in low:
            raise TregSkip("the price was above the per-call cap", cost)
        raise TregStop("Your Treg balance has run out. Add credit with 'treg topup'.", cost)
    if status == 429:
        raise TregStop("Treg's rate limit was reached. Wait a few minutes and try again.", cost)
    if rh.get("X-Treg-Error") == "1":
        raise TregSkip(f"Treg refused the call ({status})", cost)
    return status, data, cost, rh.get("X-Treg-Served-By", "") or ""


# ---------------------------------------------------------------------------
# Reading provider answers (field names differ between providers)
# ---------------------------------------------------------------------------

def _first(d, keys):
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _flatten(row):
    flat = dict(row)
    for v in row.values():
        if isinstance(v, dict):
            for k2, v2 in v.items():
                flat.setdefault(k2, v2)
    return flat


def person_from(row):
    r = _flatten(row)
    name = _first(r, ["full_name", "fullName", "name", "person_name"])
    if not name:
        name = (_first(r, ["first_name", "firstName", "first"]) + " " +
                _first(r, ["last_name", "lastName", "last"])).strip()
    title = _first(r, ["title", "job_title", "jobTitle", "position", "headline", "role", "current_title"])
    linkedin = _first(r, ["linkedin_url", "linkedinUrl", "linkedin", "linkedin_profile", "profile_url", "li_url"])
    if linkedin and "linkedin.com" in linkedin and not linkedin.startswith("http"):
        linkedin = "https://" + linkedin.lstrip("/")
    if "linkedin.com" not in linkedin:
        linkedin = ""
    email = _first(r, ["email", "work_email", "workEmail", "business_email", "professional_email"])
    if not email and isinstance(r.get("emails"), list) and r["emails"]:
        e0 = r["emails"][0]
        email = e0 if isinstance(e0, str) else (_first(e0, ["email", "value", "address"]) if isinstance(e0, dict) else "")
    if email and not EMAIL_RE.fullmatch(email):
        email = ""
    return {"name": name, "title": title, "linkedin": linkedin, "email": email}


def people_rows(data):
    out = data.get("output", data) if isinstance(data, dict) else data
    rows = []
    if isinstance(out, dict):
        for k in ("people", "results", "data", "items", "persons", "profiles", "employees"):
            if isinstance(out.get(k), list):
                rows = out[k]
                break
    elif isinstance(out, list):
        rows = out
    return [r for r in rows if isinstance(r, dict)]


def best_person(people, mode):
    kws = RANK.get(mode, RANK["investors"])

    def rank(p):
        t = p["title"].lower()
        pos = next((i for i, k in enumerate(kws) if re.search(r"\b" + re.escape(k) + r"\b", t)), len(kws))
        return (pos, 0 if p["email"] else 1)

    named = [p for p in people if p["name"]]
    return min(named, key=rank) if named else None


def email_from(data):
    out = data.get("output") if isinstance(data, dict) else None
    if isinstance(out, dict):
        e = _first(_flatten(out), ["email", "work_email", "workEmail", "address"])
        if e and EMAIL_RE.fullmatch(e):
            return e
    if out is not None:
        m = EMAIL_RE.search(json.dumps(out))
        if m:
            return m.group(0)
    return ""


def _norm_verdict(s):
    s = str(s).strip().lower()
    if any(w in s for w in ("invalid", "undeliverable", "bounce", "bad", "fail", "reject")):
        return "invalid"
    if "unknown" in s or "unverif" in s:
        return "unknown"
    if any(w in s for w in ("risky", "accept_all", "accept-all", "catch", "role", "disposable")):
        return "risky"
    if any(w in s for w in ("valid", "deliverable", "ok", "safe", "good", "verified")):
        return "valid"
    return "unknown"


def verdict_from(data):
    t = (data or {}).get("_treg") if isinstance(data, dict) else None
    v = ((t or {}).get("verification") or {}).get("verdict") if isinstance(t, dict) else None
    if v:
        return _norm_verdict(v)
    out = data.get("output") if isinstance(data, dict) else None
    if isinstance(out, dict):
        f = _flatten(out)
        for k in ("verdict", "status", "result", "state", "deliverability"):
            if isinstance(f.get(k), str):
                return _norm_verdict(f[k])
        for k in ("valid", "is_valid", "deliverable", "is_deliverable"):
            if isinstance(f.get(k), bool):
                return "valid" if f[k] else "invalid"
    return "unknown"


# ---------------------------------------------------------------------------
# One lead
# ---------------------------------------------------------------------------

def enrich_one(lead, mode, token, budget):
    res = {"place_id": lead["place_id"], "status": "not_found", "domain": "", "contact_name": "",
           "contact_title": "", "contact_email": "", "contact_linkedin": "", "email_verdict": "",
           "served_by": "", "cost_usd": 0.0, "note": ""}

    def pay(amount):
        budget.spend(amount)
        res["cost_usd"] = round(res["cost_usd"] + (amount or 0.0), 6)

    dom = domain_of(lead.get("website", ""))
    if not dom:
        res["note"] = "No company website on Google Maps, so there is nothing to look up."
        return res
    res["domain"] = dom

    # 1. a decision-maker at the company
    person = None
    for title in TITLES.get(mode, TITLES["investors"]) + [None]:
        if not budget.can_spend(CAP["search"]):
            res.update(status="budget", note="Skipped: this run's budget was reached.")
            return res
        body = {"company_domain": dom, "limit": 3}
        if title:
            body["title"] = title
        try:
            status, data, cost, _ = treg_call("treg.people.search", body, CAP["search"], token)
        except TregSkip as e:
            pay(e.cost)
            continue
        except TregStop as e:
            pay(e.cost)
            raise
        pay(cost)
        if status < 400:
            person = best_person([person_from(r) for r in people_rows(data)], mode)
            if person:
                break
    if not person:
        res["note"] = "No people found for this company."
        return res
    res.update(contact_name=person["name"], contact_title=person["title"], contact_linkedin=person["linkedin"])

    # 2. their email, verified
    email = person["email"]
    try:
        if email:
            if budget.can_spend(CAP["verify"]):
                status, data, cost, served = treg_call("treg.people.email.verify", {"email": email},
                                                       CAP["verify"], token)
                pay(cost)
                res["email_verdict"] = verdict_from(data) if status < 400 else "unknown"
                res["served_by"] = served
        else:
            if not budget.can_spend(CAP["find"] + CAP["verify"]):
                res.update(status="person_only", note="Found a person; the email lookup was skipped to stay within budget.")
                return res
            body = {"domain": dom, "full_name": person["name"]}
            if person["linkedin"]:
                body["linkedin_url"] = person["linkedin"]
            status, data, cost, served = treg_call("treg.people.email.find", body, CAP["find"], token,
                                                   {"X-Treg-Route-Verify": "true"})
            pay(cost)
            if status < 400:
                email = email_from(data)
                if email:
                    res["email_verdict"] = verdict_from(data)
                    res["served_by"] = served
    except TregSkip as e:
        pay(e.cost)
        res["note"] = f"Email lookup skipped: {e}."
    except TregStop as e:
        pay(e.cost)
        raise

    if not email:
        res["status"] = "person_only"
        res["note"] = res["note"] or "Found a person, but no work email."
        return res
    res.update(status="found", contact_email=email, email_verdict=res["email_verdict"] or "unknown")
    return res


def mock_enrich(lead, mode):
    time.sleep(0.4)
    n = sum(map(ord, lead["place_id"])) % 5
    if n == 4:
        return {"place_id": lead["place_id"], "status": "not_found", "note": "No people found for this company.",
                "cost_usd": 0.0}
    first, last = ["Asha", "Rohan", "Meera", "Karan"][n], ["Rao", "Iyer", "Shah", "Menon"][n]
    dom = domain_of(lead.get("website", "")) or "example.com"
    return {"place_id": lead["place_id"], "status": "found", "domain": dom,
            "contact_name": f"{first} {last}", "contact_title": TITLES.get(mode, ["Founder"])[0],
            "contact_email": f"{first.lower()}.{last.lower()}@{dom}",
            "contact_linkedin": f"https://www.linkedin.com/in/sample-{first.lower()}",
            "email_verdict": ["valid", "risky", "valid", "unknown"][n], "served_by": "sample",
            "cost_usd": 0.0, "note": "Sample data, not a real person."}


# ---------------------------------------------------------------------------
# Background jobs
# ---------------------------------------------------------------------------

JOBS = {}
_LOCK = threading.Lock()


def start(leads, mode):
    job = {"id": uuid.uuid4().hex[:10], "status": "running", "message": "Starting...", "results": {},
           "spent": 0.0, "budget": RUN_BUDGET_USD, "stopped": "", "error": "", "started": time.time()}
    with _LOCK:
        JOBS[job["id"]] = job
        for old in sorted(JOBS, key=lambda k: JOBS[k]["started"])[:-20]:
            JOBS.pop(old, None)
    threading.Thread(target=_run, args=(job, leads[:MAX_LEADS], mode), daemon=True).start()
    return job["id"]


def get(job_id):
    return JOBS.get(job_id)


def _run(job, leads, mode):
    try:
        token = load_token()
        if not MOCK_MODE and not token:
            job.update(status="error", error="No TREG_TOKEN found. Create one with 'treg org agent-new promptiq-app' "
                                             "and save it in ~/.hermes/.env.")
            return
        budget = Budget(RUN_BUDGET_USD)
        done = 0
        for i, lead in enumerate(leads):
            job["message"] = f"Enriching {i + 1} of {len(leads)}: {lead.get('name', '')}"
            try:
                r = mock_enrich(lead, mode) if MOCK_MODE else enrich_one(lead, mode, token, budget)
            except TregStop as e:
                job["stopped"] = str(e)
                break
            job["results"][lead["place_id"]] = r
            job["spent"] = round(budget.spent, 6)
            done += 1
            if r.get("status") == "budget":
                job["stopped"] = f"This run's ${RUN_BUDGET_USD:.2f} budget was reached."
                break
            time.sleep(0.3)
        found = sum(1 for r in job["results"].values() if r.get("status") == "found")
        job.update(status="done", spent=round(budget.spent, 6),
                   message=f"Done: {found} of {len(leads)} leads have a contact email")
    except Exception as e:  # never leave the page waiting
        job.update(status="error", error=f"Unexpected error: {e}")
