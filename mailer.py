"""
Write and send outreach emails for PromptIQ Growth Finder.

Drafts are filled from the lead (company, hook, pilot use case) and the contact found by Treg.
Sending goes through your own mailbox over SMTP (Gmail / Google Workspace app password, or any
SMTP server), set in ~/.hermes/.env:

    SMTP_USER=you@promptiq.in
    SMTP_PASSWORD=<app password>
    SMTP_HOST=smtp.gmail.com        (optional, default)
    SMTP_PORT=587                   (optional, default; 465 uses SSL)
    SMTP_FROM_NAME=Your Name        (optional)
    MAIL_DAILY_LIMIT=20             (optional)

Without SMTP settings the app opens the draft in your mail app instead (mailto), with the same checks.

Safeguards: invalid addresses are never sent; risky / unverified ones need an explicit confirmation;
one email per address per 30 days; a daily limit; an opt-out line is always included; every send
is logged to logs/sent-emails.csv.
"""

import csv
import os
import re
import smtplib
import time
import urllib.parse
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

MOCK_MODE = os.environ.get("MAIL_SAMPLE", os.environ.get("MOCK", "0")) == "1"
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
LOG_FILE = os.path.join(LOG_DIR, "sent-emails.csv")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
OPT_OUT = 'If you would prefer not to hear from me, just reply "no thanks" and I will not email again.'
REPEAT_DAYS = 30


def env_value(name, default=""):
    v = os.environ.get(name, "").strip()
    if v:
        return v
    try:
        with open(os.path.expanduser("~/.hermes/.env"), encoding="utf-8") as f:
            for line in f:
                m = re.match(r"\s*(?:export\s+)?" + re.escape(name) + r"\s*=\s*(.*)", line)
                if m:
                    val = m.group(1).strip().strip('"').strip("'")
                    if name != "SMTP_FROM_NAME":
                        val = re.split(r"[\s#]", val)[0] if val else ""
                    if val:
                        v = val
    except OSError:
        pass
    return v or default


def config():
    user, pw = env_value("SMTP_USER"), env_value("SMTP_PASSWORD")
    return {"host": env_value("SMTP_HOST", "smtp.gmail.com"), "port": int(env_value("SMTP_PORT", "587") or 587),
            "user": user, "password": pw, "from_name": env_value("SMTP_FROM_NAME", ""),
            "daily_limit": int(env_value("MAIL_DAILY_LIMIT", "20") or 20), "smtp": bool(user and pw),
            "method": (env_value("MAIL_METHOD", "gmail") or "gmail").lower(),
            "gmail_account": env_value("MAIL_GMAIL_ACCOUNT")}


def public_config():
    c = config()
    return {"smtp": c["smtp"], "from": c["user"], "daily_limit": c["daily_limit"],
            "method": c["method"], "gmail_account": c["gmail_account"],
            "sent_today": sent_today(), "mock": MOCK_MODE}


# ---------------------------------------------------------------------------
# Drafts
# ---------------------------------------------------------------------------

def clean_company(name):
    name = (name or "").strip()
    for sep in (" - ", " | ", " (", " – "):
        if sep in name:
            name = name.split(sep)[0]
    return name.strip() or "your team"


def first_name(full):
    parts = (full or "").replace(".", " ").split()
    honorifics = {"mr", "mrs", "ms", "dr", "prof", "shri", "smt"}
    parts = [p for p in parts if p.lower() not in honorifics]
    return parts[0].capitalize() if parts else "there"


def _sign(from_name):
    who = from_name or "The PromptIQ team"
    return f"Best regards,\n{who}\nPromptIQ AI | https://www.promptiq.in"


def draft(mode, lead, contact, from_name=""):
    company = clean_company(lead.get("name"))
    hi = f"Hi {first_name(contact.get('contact_name'))},"
    hook = (lead.get("hook") or "").strip()
    hook_par = f"\n\n{hook}" if hook else ""
    use_case = (lead.get("use_case") or "").strip().rstrip(".")
    sign = _sign(from_name)
    if mode == "clients":
        subject = f"A private AI pilot for {company}"
        if use_case and not (len(use_case) > 1 and use_case[1].isupper()):
            use_case = use_case[0].lower() + use_case[1:]   # keep acronyms like "MIS" intact
        uc = f", for example: {use_case}" if use_case else ""
        body = (f"{hi}\n\nI'm reaching out from PromptIQ in Bengaluru. We help teams like {company} ask questions "
                f"of their core data (SAP, Oracle, databases, Excel) in plain English, fully on-premise, so "
                f"nothing leaves your servers.{hook_par}\n\nThis quarter we are running a few free 14-day pilots: "
                f"one data source, your own hardware, and one success metric agreed up front{uc}.\n\n"
                f"Would a 20-minute call be worth it?\n\n{sign}")
    elif mode == "investors":
        subject = "PromptIQ: on-premise enterprise AI, built in Bengaluru"
        body = (f"{hi}\n\nI'm reaching out from PromptIQ in Bengaluru. We build an on-premise, run-anywhere AI "
                f"platform that lets regulated enterprises such as banks, hospitals and manufacturers use AI on "
                f"their own servers, so their data never leaves.{hook_par}\n\nI would value 20 minutes to share "
                f"what we are building and to hear how {company} thinks about enterprise AI infrastructure.\n\n{sign}")
    elif mode == "partners":
        subject = f"Partnership idea: PromptIQ and {company}"
        body = (f"{hi}\n\nI'm reaching out from PromptIQ in Bengaluru. We build an on-premise AI platform that "
                f"runs on bare metal, private cloud and edge devices such as NVIDIA Jetson.{hook_par}\n\nI think "
                f"there may be a fit between {company}'s customers and what we do. Would you be open to a "
                f"20-minute call to explore it?\n\n{sign}")
    else:
        subject = "Office space enquiry from PromptIQ"
        body = (f"{hi}\n\nWe are PromptIQ, a seed-stage AI startup in Bengaluru, looking for space for a small "
                f"team that sometimes hosts enterprise visitors.{hook_par}\n\nCould you share availability and "
                f"pricing for {company}?\n\n{sign}")
    return {"subject": subject, "body": body + "\n\n" + OPT_OUT}


# ---------------------------------------------------------------------------
# Log and limits
# ---------------------------------------------------------------------------

def _rows():
    try:
        with open(LOG_FILE, encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def sent_today():
    today = time.strftime("%Y-%m-%d")
    return sum(1 for r in _rows() if r.get("time", "").startswith(today) and r.get("status") in ("sent", "opened in mail app", "opened in gmail", "sample"))


def recently_emailed(address):
    cutoff = time.time() - REPEAT_DAYS * 86400
    for r in _rows():
        if r.get("to", "").lower() == address.lower() and r.get("status") in ("sent", "opened in mail app", "opened in gmail"):
            try:
                if time.mktime(time.strptime(r["time"], "%Y-%m-%d %H:%M:%S")) >= cutoff:
                    return r["time"]
            except (KeyError, ValueError):
                continue
    return ""


def _log(to, company, subject, status, detail=""):
    os.makedirs(LOG_DIR, exist_ok=True)
    new = not os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "to", "company", "subject", "status", "detail"])
        w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), to, company, subject, status, detail])


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

def send(to, subject, body, verdict, company="", allow_unverified=False):
    """Returns a dict: {ok, status, message, mailto?}."""
    to = (to or "").strip()
    subject = (subject or "").strip()
    body = (body or "").strip()
    if not EMAIL_RE.fullmatch(to):
        return {"ok": False, "message": "That is not a valid email address."}
    if not subject or not body:
        return {"ok": False, "message": "The subject and message cannot be empty."}
    verdict = (verdict or "unknown").lower()
    if verdict == "invalid":
        return {"ok": False, "message": "This address failed verification, so it will not be sent. It would bounce."}
    if verdict != "valid" and not allow_unverified:
        return {"ok": False, "message": "This address is not fully verified. Tick the confirmation box to send anyway."}
    cfg = config()
    if sent_today() >= cfg["daily_limit"]:
        return {"ok": False, "message": f"Today's limit of {cfg['daily_limit']} emails has been reached."}
    when = recently_emailed(to)
    if when:
        return {"ok": False, "message": f"This address was already emailed on {when[:10]}. "
                                        f"Wait {REPEAT_DAYS} days between emails to the same person."}
    if '"no thanks"' not in body:
        body = body + "\n\n" + OPT_OUT

    if MOCK_MODE:
        _log(to, company, subject, "sample", "sample mode, nothing sent")
        return {"ok": True, "status": "sample", "message": "Sample mode: nothing was actually sent."}

    if not cfg["smtp"] and cfg["method"] == "gmail":   # gmail-compose
        params = {"view": "cm", "fs": "1", "to": to, "su": subject, "body": body}
        if cfg["gmail_account"]:
            params = {"authuser": cfg["gmail_account"], **params}
        url = "https://mail.google.com/mail/?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        _log(to, company, subject, "opened in gmail")
        return {"ok": True, "status": "opened in gmail", "url": url,
                "message": "Opened in Gmail. Check the draft and press Send there."}

    if not cfg["smtp"]:
        link = "mailto:" + urllib.parse.quote(to) + "?" + urllib.parse.urlencode(
            {"subject": subject, "body": body}, quote_via=urllib.parse.quote)
        _log(to, company, subject, "opened in mail app")
        return {"ok": True, "status": "opened in mail app", "mailto": link,
                "message": "Opened in your mail app. Press Send there."}

    msg = EmailMessage()
    msg["From"] = formataddr((cfg["from_name"], cfg["user"])) if cfg["from_name"] else cfg["user"]
    msg["To"] = to
    msg["Subject"] = subject
    msg["Reply-To"] = cfg["user"]
    msg["Message-ID"] = make_msgid(domain=cfg["user"].split("@")[-1])
    msg.set_content(body)
    try:
        if cfg["port"] == 465:
            with smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=30) as s:
                s.login(cfg["user"], cfg["password"])
                s.send_message(msg)
        else:
            with smtplib.SMTP(cfg["host"], cfg["port"], timeout=30) as s:
                s.starttls()
                s.login(cfg["user"], cfg["password"])
                s.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        _log(to, company, subject, "failed", "login rejected")
        return {"ok": False, "message": "Your mail server rejected the login. For Gmail or Google Workspace, "
                                        "SMTP_PASSWORD must be an app password, not your normal password."}
    except (smtplib.SMTPException, OSError) as e:
        _log(to, company, subject, "failed", str(e)[:200])
        return {"ok": False, "message": f"Sending failed: {e}"}
    _log(to, company, subject, "sent")
    return {"ok": True, "status": "sent", "message": f"Sent to {to}."}
