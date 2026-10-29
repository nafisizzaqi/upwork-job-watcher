"""main.py v3 - Upwork job scanner, dua tahap.

Tahap 1 (search): filter ketat dari hasil find_jobs search.
  umur <= max_job_age_hours, rating client == min_client_rating,
  verification_status VERIFIED, negara dikecualikan, budget,
  proposals_tier, blacklist, keyword (dinormalisasi).
Tahap 2 (get detail per kandidat): client_record + activityStat.
  rumus hire: tidak_jalan = jobs_posted * (1 - hire_rate_percent/100);
  skip jika tidak_jalan >= max_unhired_jobs; skip jika invitesSent>0,
  totalHired>0, atau (skip_if_offered) totalOffered>0.
  Budget hourly yang kosong di search dicek ulang dari detail.
Aturan history freelancer per negara TIDAK bisa dicek dari data MCP
(tidak ada identitas/negara freelancer di review/work history) -
selalu jadi pengingat cek manual di notif Telegram.
Tidak ada auto-apply. Draft hanya disimpan lokal.
"""

import argparse
import asyncio
import collections
import json
import math
import os
import re
import sqlite3
from datetime import datetime, timezone

import requests
import yaml

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

BANNED_WORDS = [
    "experienced",
    "expert",
    "specialist",
    "professional",
    "proven",
    "skilled",
    "extensive",
    "years of experience",
    "track record",
]
GEMINI_MODELS = ["gemini-3.5-flash-lite", "gemini-3.8-flash"]

COUNTRY_ALIASES = {
    "PAK": "Pakistan",
    "PK": "Pakistan",
    "PAKISTAN": "Pakistan",
    "IND": "India",
    "IN": "India",
    "INDIA": "India",
    "BGD": "Bangladesh",
    "BD": "Bangladesh",
    "BANGLADESH": "Bangladesh",
    "LKA": "Sri Lanka",
    "LK": "Sri Lanka",
    "SRI LANKA": "Sri Lanka",
    "USA": "United States",
    "US": "United States",
    "UNITED STATES": "United States",
    "UK": "United Kingdom",
    "GB": "United Kingdom",
    "GBR": "United Kingdom",
    "UNITED KINGDOM": "United Kingdom",
    "ARE": "United Arab Emirates",
    "AE": "United Arab Emirates",
    "SGP": "Singapore",
    "SG": "Singapore",
    "AUS": "Australia",
    "AU": "Australia",
    "FRA": "France",
    "FR": "France",
    "SAU": "Saudi Arabia",
    "SA": "Saudi Arabia",
}

TIER_MAP = {
    "fewer than 5": "less_than_5",
    "5 to 10": "5_to_10",
    "10 to 15": "10_to_15",
    "15 to 20": "15_to_20",
    "20 to 50": "20_to_50",
}


# ---------- helpers umum ----------

def _words(s):
    return re.findall(r"[a-z0-9]+", str(s or "").lower())


def _compact(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _pick(d, *names):
    if not isinstance(d, dict):
        return None
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    return None


def _num_from(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for k in ("amount", "value", "min", "rate", "budget", "minimum", "hourlyBudgetMin"):
            if k in value:
                v = _num_from(value[k])
                if v is not None:
                    return v
        return None
    if isinstance(value, str):
        s = value.replace("$", "").replace(",", "").strip()
        m = re.search(r"-?\d+(?:\.\d+)?", s)
        return float(m.group(0)) if m else None
    return None


def _parse_money(value):
    return _num_from(value)


def _parse_date(value):
    if not value:
        return None
    try:
        s = str(value).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _canonical_country(raw):
    s = str(raw or "").strip()
    if not s:
        return ""
    key = s.upper()
    if key in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[key]
    return s


# ---------- config ----------

def load_config(path="config.yaml"):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def flat_keywords(cfg):
    kw = cfg.get("keywords") or {}
    out = []
    if isinstance(kw, dict):
        groups = list(kw.values())
    elif isinstance(kw, list):
        groups = [kw]
    else:
        groups = []
    for g in groups:
        for k in g or []:
            if k not in out:
                out.append(str(k))
    return out


def filter_settings(cfg):
    f = dict(cfg.get("filters") or {})
    f.setdefault("max_job_age_hours", 1)
    f.setdefault("min_client_rating", 5.0)
    f.setdefault("max_unhired_jobs", 50)
    f.setdefault("max_detail_checks_per_run", 10)
    f.setdefault("skip_if_offered", True)
    f.setdefault("min_fixed_budget_usd", 100)
    f.setdefault("min_hourly_rate_usd", 20)
    f.setdefault("excluded_client_countries", [])
    f.setdefault("proposal_ranges", ["less_than_5", "5_to_10", "10_to_15", "15_to_20"])
    f["blacklist"] = cfg.get("blacklist", [])
    return f


# ---------- normalisasi job (search) ----------

def to_internal_job(raw):
    if not isinstance(raw, dict):
        return None
    client = raw.get("client") or {}
    if not isinstance(client, dict):
        client = {}
    job_id = raw.get("id") or raw.get("job_id") or ""
    if not job_id and raw.get("url"):
        job_id = str(raw.get("url"))
    title = str(raw.get("title") or "")
    desc = str(raw.get("description") or "")
    snippet = str(raw.get("description_snippet") or "")
    skills = raw.get("skills") or []
    if isinstance(skills, str):
        skills = [skills]
    skills_text = " ".join(str(s) for s in skills)
    job_type = str(
        _pick(raw, "job_type", "budget_type", "type") or ""
    ).lower()
    budget_val, budget_kind = parse_search_budget(raw, job_type)
    country = _canonical_country(
        _pick(client, "country")
        or _pick(raw, "country", "client_country", "clientCountry", "location")
    )
    return {
        "id": str(job_id),
        "title": title,
        "description": desc,
        "description_snippet": snippet,
        "skills": skills,
        "skills_text": skills_text,
        "text": " ".join([title, desc, snippet, skills_text]),
        "job_type": job_type,
        "budget": budget_val,
        "budget_kind": budget_kind,
        "country": country,
        "rating": _pick(client, "rating"),
        "total_posted_jobs": _pick(client, "total_posted_jobs"),
        "total_reviews": _pick(client, "total_reviews"),
        "total_spent": _pick(client, "total_spent"),
        "verification_status": _pick(client, "verification_status"),
        "proposals_tier": _pick(raw, "proposals_tier"),
        "published_date": _pick(raw, "published_date", "created_date"),
        "created_date": _pick(raw, "created_date"),
        "url": str(raw.get("url") or ""),
        "raw": raw,
    }


def parse_search_budget(raw, job_type):
    for key in ("budget", "amount", "budget_amount", "hourly_rate", "rate", "rate_min", "budget_min"):
        if key in raw:
            v = _num_from(raw.get(key))
            if v is not None:
                kind = "hourly" if "hour" in job_type else ("fixed" if "fix" in job_type else "fixed")
                return v, kind
    return None, ("hourly" if "hour" in job_type else "fixed")


def keyword_in_text(keyword, text):
    kw_words = _words(keyword)
    if not kw_words:
        return False
    if _compact(keyword) and _compact(keyword) in _compact(text):
        return True
    tw = _words(text)
    n = len(kw_words)
    for i in range(len(tw) - n + 1):
        if tw[i : i + n] == kw_words:
            return True
    # Varian titik: "node.js" juga cocok dengan kata "node" sendirian.
    if "." in str(keyword) and kw_words[0] in tw:
        return True
    return False


def matched_keywords(job, keywords):
    return [k for k in keywords if keyword_in_text(k, job["text"])]


# ---------- filter tahap 1 ----------

def stage1_filter(job, keywords, filters):
    cfg = filters
    text = job["text"]
    low = text.lower()
    for bad in cfg.get("blacklist", []) or []:
        if str(bad).lower() in low:
            return False, f"blacklist: {bad}"
    excluded = {_canonical_country(c) for c in (cfg.get("excluded_client_countries") or [])}
    if job["country"] in excluded or job["country"].lower() in {e.lower() for e in excluded}:
        return False, f"negara client dikecualikan: {job['country'].lower()}"
    tier_raw = str(job["proposals_tier"] or "")
    tier = TIER_MAP.get(tier_raw.strip().lower())
    if tier is None:
        return False, f"proposals_tier tidak lolos: {tier_raw or 'tidak terbaca'}"
    if tier not in (cfg.get("proposal_ranges") or []):
        return False, f"proposals_tier tidak lolos: {tier_raw}"
    # Budget: kosong di search -> lolos ke tahap 2 (dicek ulang dari detail).
    if job["budget"] is not None:
        if job["budget_kind"] == "hourly":
            if job["budget"] < float(cfg["min_hourly_rate_usd"]):
                return False, f"budget hourly {job['budget']} di bawah minimum {cfg['min_hourly_rate_usd']}"
        else:
            if job["budget"] < float(cfg["min_fixed_budget_usd"]):
                return False, f"budget fixed {job['budget']} di bawah minimum {cfg['min_fixed_budget_usd']}"
    matched = matched_keywords(job, keywords)
    if not matched:
        return False, "tidak ada keyword config"
    job["matched_keywords"] = matched
    # Umur job (UTC, dari published_date, fallback created_date).
    pub = _parse_date(job["published_date"]) or _parse_date(job["created_date"])
    if pub is None:
        return False, "tanggal posting tidak terbaca"
    age_h = (datetime.now(timezone.utc) - pub).total_seconds() / 3600.0
    job["age_hours"] = age_h
    if age_h > float(cfg["max_job_age_hours"]):
        return False, f"umur job {age_h:.1f} jam di atas maksimum {cfg['max_job_age_hours']} jam"
    # Rating ketat: harus ada dan >= minimum (5.0).
    rating = _num_from(job["rating"])
    job["rating_num"] = rating
    if rating is None:
        return False, "rating client tidak terbaca"
    if rating < float(cfg["min_client_rating"]):
        return False, f"rating client {rating} di bawah {cfg['min_client_rating']}"
    vs = str(job["verification_status"] or "").strip().upper()
    if vs and vs != "VERIFIED":
        return False, f"status verifikasi client bukan VERIFIED: {vs}"
    return True, ""


# ---------- detail (get) + filter tahap 2 ----------

def parse_detail(raw):
    d = raw if isinstance(raw, dict) else {}
    # Buka bungkus umum: {"result": {...}} / {"data": {...}}.
    if "client_record" not in d and isinstance(d.get("result"), dict):
        d = d["result"]
    cr = _pick(d, "client_record", "clientRecord") or {}
    if not isinstance(cr, dict):
        cr = {}
    data = d.get("data") or {}
    mp = {}
    if isinstance(data, dict):
        mp = data.get("marketplaceJobPosting") or {}
    if not mp:
        mp = _pick(d, "marketplaceJobPosting") or {}
    if not isinstance(mp, dict):
        mp = {}
    act = ((mp.get("activityStat") or {}).get("jobActivity") or {})
    if not isinstance(act, dict):
        act = {}
    ct = mp.get("contractTerms") or {}
    hourly = ct.get("hourlyContractTerms") or {}
    pref = None
    def _find_pref(o):
        nonlocal pref
        if pref is not None:
            return
        if isinstance(o, dict):
            for k in ("preferred_locations", "preferredLocations"):
                if isinstance(o.get(k), dict):
                    pref = o[k]
                    return
            for v in o.values():
                _find_pref(v)
        elif isinstance(o, list):
            for v in o:
                _find_pref(v)
    _find_pref(d)
    pref_countries = []
    loc_required = None
    if isinstance(pref, dict):
        loc_required = pref.get("location_required", pref.get("locationRequired"))
        countries = pref.get("countries") or []
        if isinstance(countries, list):
            for c in countries:
                if isinstance(c, dict):
                    pref_countries.append(str(c.get("name") or c.get("code") or c.get("label") or ""))
                else:
                    pref_countries.append(str(c))
        pref_countries = [c for c in pref_countries if c]
    return {
        "hire_rate_percent": _num_from(_pick(cr, "hire_rate_percent", "hireRatePercent")),
        "jobs_posted": _num_from(_pick(cr, "jobs_posted", "jobsPosted")),
        "jobs_with_hires": _num_from(_pick(cr, "jobs_with_hires", "jobsWithHires")),
        "feedback_score": _num_from(_pick(cr, "feedback_score", "feedbackScore")),
        "spend_total": _pick(cr, "spend_total", "spendTotal"),
        "invites_sent": _num_from(_pick(act, "invitesSent", "invites_sent")),
        "total_hired": _num_from(_pick(act, "totalHired", "total_hired")),
        "total_interviewed": _num_from(_pick(act, "totalInvitedToInterview", "total_invited_to_interview")),
        "total_offered": _num_from(_pick(act, "totalOffered", "total_offered")),
        "hourly_min": _num_from(hourly.get("hourlyBudgetMin")),
        "hourly_max": _num_from(hourly.get("hourlyBudgetMax")),
        "preferred_countries": pref_countries,
        "location_required": loc_required,
        "raw": d,
    }


def stage2_filter(job, detail, filters):
    # Lokasi preferred (tanda seru merah): skip bila daftar preferred
    # location ada isinya dan negara freelancer tidak termasuk di dalamnya.
    _det = detail or {}
    _pref = [str(c) for c in (_det.get("preferred_countries") or [])]
    if _pref:
        _ref = str((filters or {}).get("freelancer_country") or "Indonesia").strip().lower()
        _want = {"indonesia", "id", "idn"} if _ref == "indonesia" else {_ref}
        _norm = {c.strip().lower() for c in _pref}
        if not (_norm & _want):
            return False, "lokasi preferred tidak cocok: " + ", ".join(_pref), _det
    if detail is None:
        return False, "detail get gagal / tidak terbaca", {}
    hire = detail["hire_rate_percent"]
    posted = detail["jobs_posted"]
    # Budget fallback dari detail untuk job hourly yang kosong di search.
    if job["budget"] is None and job["budget_kind"] == "hourly":
        fb = detail["hourly_min"] if detail["hourly_min"] is not None else detail["hourly_max"]
        if fb is not None:
            job["budget"] = fb
            job["budget_from_detail"] = True
            if fb < float(filters["min_hourly_rate_usd"]):
                return False, f"budget hourly {fb} di bawah minimum {filters['min_hourly_rate_usd']} (dari detail)", detail
        else:
            job["budget_unknown"] = True
    if job["budget"] is None:
        job["budget_unknown"] = True
    if hire is None or posted is None:
        return False, "data hire tidak terbaca di detail", detail
    unhired = posted * (1.0 - hire / 100.0)
    detail["unhired"] = unhired
    if unhired >= float(filters["max_unhired_jobs"]):
        return False, f"hire: {posted:g} posting, {hire:g}% hire -> {unhired:g} tidak jalan (>= {filters['max_unhired_jobs']})", detail
    invites = detail["invites_sent"] or 0
    hired = detail["total_hired"] or 0
    offered = detail["total_offered"] or 0
    if hired > 0:
        return False, f"activity: sudah ada hires ({hired:g})", detail
    if invites > 0:
        return False, f"activity: invite terkirim ({invites:g})", detail
    if filters.get("skip_if_offered") and offered > 0:
        return False, f"activity: sudah ada offer ({offered:g})", detail
    # Lolos: activity 0 semua, atau ada interviewing.
    return True, "", detail


# ---------- dedup SQLite (kompatibel dengan DB v1/v2) ----------

def _seen_table_info(conn):
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    except Exception:
        return None, []
    names = [r[0] for r in rows]
    for cand in ("seen_jobs", "seen", "jobs_seen", "processed_jobs", "processed"):
        if cand in names:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({cand})").fetchall()]
            return cand, cols
    for n in names:
        if "seen" in n or "processed" in n:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({n})").fetchall()]
            return n, cols
    return None, []


def load_seen(db_path="seen_jobs.db"):
    if not os.path.exists(db_path):
        return set(), None
    conn = sqlite3.connect(db_path)
    try:
        table, cols = _seen_table_info(conn)
        if table is None:
            return set(), None
        id_col = "job_id" if "job_id" in cols else ("id" if "id" in cols else cols[0])
        rows = conn.execute(f"SELECT {id_col} FROM {table}").fetchall()
        return {str(r[0]) for r in rows if r and r[0] is not None}, table
    except Exception:
        return set(), None
    finally:
        conn.close()


def mark_seen(db_path, job_id, status, title=""):
    conn = sqlite3.connect(db_path)
    try:
        table, cols = _seen_table_info(conn)
        if table is None:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS seen_jobs "
                "(job_id TEXT PRIMARY KEY, status TEXT, title TEXT, seen_at TEXT)"
            )
            table, cols = "seen_jobs", ["job_id", "status", "title", "seen_at"]
        if "status" in cols and "title" in cols:
            conn.execute(
                f"INSERT OR REPLACE INTO {table} (job_id, status, title) VALUES (?, ?, ?)",
                (str(job_id), str(status), str(title)),
            )
        elif "status" in cols:
            conn.execute(
                f"INSERT OR REPLACE INTO {table} (job_id, status) VALUES (?, ?)",
                (str(job_id), str(status)),
            )
        else:
            conn.execute(
                f"INSERT OR IGNORE INTO {table} (job_id) VALUES (?)", (str(job_id),)
            )
        conn.commit()
    except Exception as e:
        print(f"Peringatan: gagal menandai seen {job_id}: {e}")
    finally:
        conn.close()


# ---------- Telegram + Gemini ----------

def telegram_send(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("Telegram: token/chat_id belum diset, notif dilewati.")
        return None
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=30,
        )
        print(f"Telegram: {r.status_code}")
        return r.status_code
    except Exception as e:
        print(f"Telegram gagal: {e}")
        return None


def _clean_url(url):
    return str(url or "").split("?", 1)[0]


def budget_display(job):
    if job.get("budget") is None:
        return "tidak dinyatakan"
    kind = str(job.get("budget_kind") or "").lower()
    try:
        amount = float(job["budget"])
    except (TypeError, ValueError):
        return f"{job['budget']} {kind}".strip()
    if kind == "hourly":
        return f"${amount:,.0f}/jam"
    return f"${amount:,.0f} (fixed)"


def telegram_send_file(path, caption):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("Telegram: token/chat_id belum diset, kirim file dilewati.")
        return None
    try:
        with open(path, "rb") as fh:
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendDocument",
                data={"chat_id": chat_id, "caption": caption[:1024]},
                files={"document": (os.path.basename(path), fh)},
                timeout=60,
            )
        print(f"Telegram file: {r.status_code}")
        return r.status_code
    except Exception as e:
        print(f"Telegram kirim file gagal: {e}")
        return None


def _discord_webhook():
    url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not url:
        print("Discord: DISCORD_WEBHOOK_URL belum diset, notif dilewati.")
        return ""
    try:
        cfg = load_config()
        if (cfg.get("notifications") or {}).get("discord_enabled") is False:
            return ""
    except Exception:
        pass
    return url


def discord_send(text):
    url = _discord_webhook()
    if not url:
        return None
    try:
        r = requests.post(url, json={"content": text[:2000]}, timeout=30)
        print(f"Discord: {r.status_code}")
        return r.status_code
    except Exception as e:
        print(f"Discord gagal: {e}")
        return None


def discord_send_embed(title, description, color=0xE74C3C):
    url = _discord_webhook()
    if not url:
        return None
    try:
        import json
        r = requests.post(url, json={"embeds": [{"title": title[:256], "description": description[:4096], "color": color}]}, timeout=30)
        print(f"Discord embed: {r.status_code}")
        return r.status_code
    except Exception as e:
        print(f"Discord gagal: {e}")
        return None


def discord_send_file(path, title, description, color=0x2ECC71):
    url = _discord_webhook()
    if not url:
        return None
    try:
        import json
        payload = {"embeds": [{"title": title[:256], "description": description[:4096], "color": color}]}
        with open(path, "rb") as fh:
            r = requests.post(url, data={"payload_json": json.dumps(payload)}, files={"file": (os.path.basename(path), fh)}, timeout=60)
        print(f"Discord file: {r.status_code}")
        return r.status_code
    except Exception as e:
        print(f"Discord kirim file gagal: {e}")
        return None


def _error_signature(exc):
    text = " ".join(str(exc).split())[:120]
    return f"{type(exc).__name__}: {text}"


def _should_send_error(signature, cooldown_seconds=3600):
    import json
    import time

    state_path = os.path.join("logs", "error_state.json")
    try:
        os.makedirs("logs", exist_ok=True)
        now = time.time()
        if os.path.exists(state_path):
            with open(state_path, encoding="utf-8") as fh:
                state = json.load(fh)
            if state.get("signature") == signature and now - float(state.get("ts", 0)) < cooldown_seconds:
                return False
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump({"signature": signature, "ts": now}, fh)
        return True
    except Exception as e:
        print(f"State error Telegram gagal dibaca/ditulis: {e}")
        return True


def notify_error(exc):
    from datetime import datetime

    signature = _error_signature(exc)
    try:
        cfg = load_config()
        notify_cfg = cfg.get("notifications") or {}
        if notify_cfg.get("notify_on_error") is False:
            print("Notif error dimatikan di config, dilewati.")
            return
        telegram_off = notify_cfg.get("telegram_enabled") is False
    except Exception as e:
        print(f"Baca config untuk notif error gagal ({e}), tetap coba kirim.")
        telegram_off = False
    if not _should_send_error(signature):
        print("Notif error sama sudah dikirim <60 menit, dilewati (anti-spam).")
        return
    when = datetime.now().strftime("%H:%M")
    if not telegram_off:
        telegram_send(
            f"Scanner error {when} WIB: {signature}\n"
            "Jalan berikutnya otomatis coba lagi 10 menit lagi."
        )
    discord_send_embed(
        "Scanner error",
        f"{signature}\nJalan berikutnya otomatis coba lagi 10 menit lagi.",
        color=0xE74C3C,
    )


def _find_questions(raw):
    found = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if "question" in str(k).lower():
                    if isinstance(v, str) and v.strip():
                        found.append(v.strip())
                    elif isinstance(v, list):
                        for item in v:
                            if isinstance(item, str) and item.strip():
                                found.append(item.strip())
                            elif isinstance(item, dict):
                                for qk in ("text", "question", "title", "label"):
                                    val = item.get(qk)
                                    if isinstance(val, str) and val.strip():
                                        found.append(val.strip())
                    elif isinstance(v, dict):
                        walk(v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    try:
        walk(raw or {})
    except Exception:
        return []
    out = []
    for q in found:
        if q not in out:
            out.append(q)
    return out[:10]


def build_prompt(job, keywords, detail):
    budget_txt = "tidak dinyatakan"
    if job["budget"] is not None:
        budget_txt = f"{job['budget']:g} {job['budget_kind']}"
    _qs = _find_questions((detail or {}).get("raw"))
    if _qs:
        questions_txt = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(_qs))
    else:
        questions_txt = "None found in structured job data."
    full_desc = job["description"] or job["description_snippet"] or ""
    if len(full_desc) <= 1800:
        desc = full_desc
    else:
        desc = full_desc[:900] + "\n...[middle omitted]...\n" + full_desc[-900:]
    prompt = (
        "You write Upwork proposals. Read the job description BACKWARDS first: start from the END, "
        "where the requirements, 'please include', and application instructions usually are, then read the beginning. "
        "Instructions at the end win over anything generic at the top. Extract three things: "
        "(1) a required opener, such as 'start your proposal with POD' or 'include the word POD', "
        "(2) anything the client asks to include, such as experience or stores worked on, "
        "(3) screening questions the client asks applicants to answer. "
        "If the matched keyword is only incidental to this job (a passing mention, a nice-to-have, "
        "or the job is migrating away from it), reply with exactly: SKIP and nothing else. "
        "Otherwise reply with exactly three sections, each label on its own line, in this order: "
        "PROPOSAL, QUESTIONS, EDITOR NOTE - DO NOT SUBMIT. No other headings. "
        "PROPOSAL section rules: plain paragraphs separated by blank lines, simple clear English, "
        "no bold, no italics, no markdown except bullet lines for work examples only. "
        "If an opener is required, it goes on the very first line by itself, then a blank line, then 'Hi,'. "
        "If no opener is required, start with 'Hi,'. "
        "The opening paragraph must, within its first 250 characters, state the SOLUTION directly, "
        "never restate the client's problem. Then a paragraph about the relevant work, followed by "
        "the line 'Here are two examples of my work:' and exactly two bullet placeholder lines, "
        "'- [Work example link to be added during manual edit]', with no URLs, no passwords, no invented links. "
        "Then a paragraph of concrete steps tailored to this job's requirements, starting with checking the current setup. "
        "Then an honest paragraph: name the strongest relevant work area plainly, then transparently disclose "
        "every named tool, platform, or role in the requirements that goes beyond it as not confirmed hands-on experience, "
        "and say you are willing to learn the client's workflow and follow their processes. "
        "Do not narrate specific past projects, clients, or tasks performed; describe only what you will do for this job, and answer experience questions only with the work-area statement and the disclosure, never with a story about a past store or client. "
        "Use present or future tense only when describing capability; never use past tense such as have worked, have handled, helped, or managed to imply completed past projects. "
        "Never use the phrases 'I have experience', 'I have recent experience', or 'hands-on experience', or any sentence saying you performed specific work for a past store or client; the only experience statement allowed is 'My strongest relevant work area is ...' followed by the disclosure of what is not confirmed. "
        "Never claim years of work, past clients, results, numbers, portfolio, or certifications. "
        "Never use these words: experienced, expert, specialist, professional, proven, skilled, extensive, "
        "years of experience, track record. Never promise a result, ranking, or sales outcome. "
        "Budget rule: the stated budget is shown below as 'Budget: ...'. If it says 'tidak dinyatakan' (not stated), never invent a price or estimate. "
        "If the description asks for a price estimate, include the line '[Price estimate to be added during manual edit]' in the PROPOSAL and state in the EDITOR NOTE that the budget was not stated and an estimate placeholder was included. "
        "If the description does not ask for one, do not discuss price at all; pricing can be discussed later. "
        "Close with an invitation to discuss, then 'Best regards,' on its own line, then 'Dana'. "
        "QUESTIONS section rules: answer every screening question found, numbered, one answer each, "
        "in the same honest tone as the proposal. A question about certifications is answered: "
        "'I don't hold certifications related to this project.' If no screening questions were found, write: "
        "'No screening questions were included in this job posting.' "
        "EDITOR NOTE - DO NOT SUBMIT section rules: bullet lines listing what portfolio or work examples this job needs "
        "and what would be relevant to add, which opener or include-instructions were obeyed, and which gaps were disclosed. "
        "This section is only for the human editor and must never be submitted.\n\n"
        f"Job title: {job['title']}\n"
        f"Budget: {budget_txt} | Client country: {job['country']}\n"
        f"Skills: {job['skills_text']}\n"
        f"Matched keywords: {', '.join(keywords)}\n"
        f"Screening questions from job data:\n{questions_txt}\n"
        f"Description: {desc}"
    )
    return prompt


def _draft_complete(text):
    need = ["PROPOSAL", "QUESTIONS", "EDITOR NOTE", "Best regards", "Dana"]
    return all(s in text for s in need)


def gemini_draft(job, keywords, detail):
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print("Gemini: GEMINI_API_KEY belum diset.")
        return None, None
    prompt = build_prompt(job, keywords, detail)
    last_err = None
    for model in GEMINI_MODELS:
        for attempt in range(2):
            try:
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"maxOutputTokens": 4000, "temperature": 0.7},
                    },
                    timeout=120,
                )
                if r.status_code in (429, 503):
                    last_err = f"{model} {r.status_code}"
                    import time

                    time.sleep(20)
                    continue
                r.raise_for_status()
                data = r.json()
                cand = data["candidates"][0]
                fr = cand.get("finishReason") or ""
                if fr:
                    print(f"Gemini {model} finishReason: {fr}")
                parts = cand.get("content", {}).get("parts") or []
                text = "".join(p.get("text", "") for p in parts).strip()
                if not text:
                    last_err = f"{model} empty response"
                    print(f"Gemini {model} mengembalikan teks kosong, coba lagi.")
                    continue
                if text == "SKIP":
                    return "SKIP", model
                if not text.startswith("PROPOSAL"):
                    text = "PROPOSAL\n" + text
                if not _draft_complete(text):
                    last_err = f"{model} incomplete draft (finishReason={fr})"
                    print(f"Gemini {model} draft tidak lengkap, coba lagi.")
                    continue
                low = text.lower()
                hits = [w for w in BANNED_WORDS if w in low]
                if hits:
                    print(f"Peringatan kata terlarang di draft: {hits} (tetap disimpan, cek manual).")
                return text, model
            except Exception as e:
                last_err = str(e)
                print(f"Gemini {model} gagal (percobaan {attempt + 1}): {e}")
                continue
    print(f"Gemini gagal semua model: {last_err}")
    return None, None


GROQ_MODELS = ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b"]


def groq_draft(job, keywords, detail):
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        print("Groq: GROQ_API_KEY belum diset.")
        return None, None
    prompt = build_prompt(job, keywords, detail)
    last_err = None
    for model in GROQ_MODELS:
        for attempt in range(2):
            try:
                r = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 2000,
                        "temperature": 0.7,
                    },
                    timeout=120,
                )
                if r.status_code == 429:
                    last_err = f"{model} 429"
                    import time

                    time.sleep(20)
                    continue
                if r.status_code in (400, 404):
                    last_err = f"{model} {r.status_code}"
                    print(f"Groq {model} ditolak ({r.status_code}), coba model berikut...")
                    break
                r.raise_for_status()
                data = r.json()
                ch = data["choices"][0]
                fr = ch.get("finish_reason") or ""
                if fr:
                    print(f"Groq {model} finish_reason: {fr}")
                if fr == "length":
                    last_err = f"{model} length cut"
                    print(f"Groq {model} kepotong token, coba lagi.")
                    continue
                text = (ch.get("message", {}).get("content") or "").strip()
                if not text:
                    last_err = f"{model} empty response"
                    print(f"Groq {model} mengembalikan teks kosong, coba lagi.")
                    continue
                if text == "SKIP":
                    return "SKIP", model
                if not text.startswith("PROPOSAL"):
                    text = "PROPOSAL\n" + text
                if not _draft_complete(text):
                    last_err = f"{model} incomplete draft (finish_reason={fr})"
                    print(f"Groq {model} draft tidak lengkap, coba lagi.")
                    continue
                low = text.lower()
                hits = [w for w in BANNED_WORDS if w in low]
                if hits:
                    print(f"Peringatan kata terlarang di draft: {hits} (tetap disimpan, cek manual).")
                return text, model
            except Exception as e:
                last_err = str(e)
                print(f"Groq {model} gagal (percobaan {attempt + 1}): {e}")
                continue
    print(f"Groq gagal semua model: {last_err}")
    return None, None


def budget_label(job):
    if job["budget"] is None:
        return "tidak dinyatakan"
    return f"{job['budget']:g} {job['budget_kind']}"


def client_line(job, detail):
    parts = []
    if job.get("rating_num") is not None:
        parts.append(f"rating {job['rating_num']:g}")
    if detail:
        if detail.get("hire_rate_percent") is not None:
            parts.append(f"hire {detail['hire_rate_percent']:g}%")
        if detail.get("jobs_posted") is not None:
            parts.append(f"posting {detail['jobs_posted']:g}")
        if detail.get("unhired") is not None:
            parts.append(f"tidak jalan {detail['unhired']:g}")
        parts.append(
            "activity invites "
            f"{detail.get('invites_sent') or 0:g}/hired {detail.get('total_hired') or 0:g}"
            f"/interview {detail.get('total_interviewed') or 0:g}"
        )
    spent = _parse_money(job.get("total_spent"))
    if spent is not None:
        parts.append(f"spend ${spent:,.2f}")
    return " | ".join(parts)


# ---------- MCP (Upwork) ----------

def _extract_dict(result):
    if isinstance(result, dict):
        return result
    sc = getattr(result, "structured_content", None)
    if isinstance(sc, dict):
        return sc
    if isinstance(sc, list) and sc and isinstance(sc[0], dict):
        return sc[0]
    content = getattr(result, "content", None) or []
    for item in content:
        txt = getattr(item, "text", None)
        if isinstance(txt, str) and txt.strip():
            try:
                parsed = json.loads(txt)
            except Exception:
                continue
            if isinstance(parsed, dict):
                return parsed
            if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                return parsed[0]
        sc2 = getattr(item, "structured_content", None)
        if isinstance(sc2, dict):
            return sc2
    return {}


def _extract_jobs(d):
    if isinstance(d, list):
        return [x for x in d if isinstance(x, dict)]
    if isinstance(d, dict):
        for key in ("jobs", "results", "items"):
            v = d.get(key)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
        data = d.get("data")
        if isinstance(data, dict):
            return _extract_jobs(data)
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
    return []


async def _raw_call(client, name, arguments):
    try:
        return await client.call_tool(name, arguments=arguments)
    except TypeError:
        return await client.call_tool(name, arguments)


async def _call_tool(client, name, arguments):
    try:
        return await _raw_call(client, name, arguments)
    except Exception as e:
        if not name.startswith("upwork__") and getattr(e, "code", None) == -32603:
            return await _raw_call(client, "upwork__" + name, arguments)
        raise


def _find_org_uid(d):
    if isinstance(d, dict):
        for k in ("org_uid", "orgUid", "org_id"):
            if k in d and isinstance(d[k], str):
                return d[k]
        for v in d.values():
            r = _find_org_uid(v)
            if r:
                return r
    elif isinstance(d, list):
        for v in d:
            r = _find_org_uid(v)
            if r:
                return r
    return None


class McpSession:
    """Sesi MCP Upwork (OAuth via auth_setup.py, pola SDK v2)."""

    def __init__(self):
        self.client = None
        self._http_client = None
        self._client_cm = None
        self.org_uid = None

    async def __aenter__(self):
        import auth_setup as auth_mod

        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client

        try:
            from mcp.shared.auth import OAuthClientMetadata
        except Exception:
            from mcp.client.auth import OAuthClientMetadata  # type: ignore

        from mcp.client.auth import OAuthClientProvider
        import httpx2

        try:
            auth_mod._callback_result = None
        except Exception:
            pass
        server_url = getattr(auth_mod, "SERVER_URL", None) or ""
        mcp_url = server_url if "upwork.com" in str(server_url) else "https://mcp.upwork.com/mcp"
        wait_for_callback = getattr(auth_mod, "wait_for_callback", None)

        async def redirect_handler(url):
            print("Buka URL login Upwork di browser:")
            print(url)
            try:
                import webbrowser

                webbrowser.open(url)
            except Exception:
                pass

        async def callback_handler():
            if wait_for_callback is not None:
                try:
                    res = await wait_for_callback()
                except TypeError:
                    res = await wait_for_callback(300)
                if isinstance(res, (list, tuple)) and res:
                    return (res[0], res[1] if len(res) > 1 else None)
                code = getattr(res, "code", None)
                state = getattr(res, "state", None)
                if code:
                    return (code, state)
                raise RuntimeError("callback OAuth tidak mengembalikan authorization code")
            for _ in range(300):
                res = getattr(auth_mod, "_callback_result", None)
                if res is not None:
                    code = getattr(res, "code", None)
                    state = getattr(res, "state", None)
                    if code:
                        return (code, state)
                await asyncio.sleep(1.0)
            raise RuntimeError("timeout menunggu callback OAuth (300 detik)")

        storage = None
        for maker in (
            lambda: auth_mod.FileTokenStorage(".upwork_token.json"),
            lambda: auth_mod.FileTokenStorage(path=".upwork_token.json"),
            lambda: auth_mod.FileTokenStorage(),
        ):
            try:
                storage = maker()
                break
            except Exception:
                continue
        if storage is None:
            raise RuntimeError("FileTokenStorage tidak bisa dibuat; cek auth_setup.py")

        metadata = OAuthClientMetadata(
            client_name="upwork-job-watcher",
            redirect_uris=["http://127.0.0.1:3000/callback"],
        )
        try:
            provider = OAuthClientProvider(
                server_url=mcp_url,
                client_metadata=metadata,
                storage=storage,
                redirect_handler=redirect_handler,
                callback_handler=callback_handler,
            )
        except TypeError:
            provider = OAuthClientProvider(
                mcp_url, metadata, storage, redirect_handler, callback_handler
            )
        self._http_client = httpx2.AsyncClient(
            auth=provider, timeout=httpx2.Timeout(30.0, read=300.0)
        )
        await self._http_client.__aenter__()
        transport = streamable_http_client(mcp_url, http_client=self._http_client)
        self.client = Client(transport)
        enter = getattr(self.client, "__aenter__", None)
        if enter is not None:
            await enter()
        # org_uid dari list_accounts (nilainya tidak dicatat/diprint).
        res = None
        last_err = None
        for _args in ({}, None):
            try:
                res = await _call_tool(self.client, "list_accounts", _args)
                break
            except Exception as e:
                last_err = e
                print(
                    "list_accounts gagal args="
                    f"{_args!r} err={type(e).__name__} "
                    f"code={getattr(e, 'code', None)} "
                    f"message={getattr(e, 'message', None)} "
                    f"data={getattr(e, 'data', None)}"
                )
        if res is None:
            try:
                await self.__aexit__(None, None, None)
            except Exception:
                pass
            raise RuntimeError(f"list_accounts gagal: {last_err!r}")
        self.org_uid = _find_org_uid(_extract_dict(res))
        if not self.org_uid:
            raise RuntimeError("org_uid tidak ditemukan dari list_accounts")
        return self

    async def __aexit__(self, *exc):
        if self.client is not None:
            ex = getattr(self.client, "__aexit__", None)
            if ex is not None:
                try:
                    await ex(*exc)
                except Exception:
                    pass
        if self._http_client is not None:
            try:
                await self._http_client.aclose()
            except Exception:
                pass
        return False

    async def search_keyword(self, keyword, limit_per_keyword, filters):
        from datetime import datetime, timezone
        per_page = max(1, min(10, int(limit_per_keyword or 10)))
        max_pages = int((filters or {}).get("max_search_pages") or 3)
        max_age_h = (filters or {}).get("max_job_age_hours")
        if max_age_h is None:
            _days = (filters or {}).get("max_job_age_days")
            max_age_h = float(_days) * 24.0 if _days is not None else 1.0
        max_age_h = float(max_age_h)
        all_jobs = []
        seen_ids = set()
        cursor = None
        for _page in range(max(1, max_pages)):
            params = {
                "title": keyword,
                "verified_payment_only": True,
                "sort": "recency",
                "proposals_max": 20,
                "limit": per_page,
                "include_full_details": True,
            }
            if cursor:
                params["cursor"] = cursor
            res = await _call_tool(
                self.client,
                "find_jobs",
                {"action": "search", "org_uid": self.org_uid, "params": params},
            )
            d = _extract_dict(res)
            jobs = _extract_jobs(d) or []
            if not jobs:
                break
            for j in jobs:
                jid = str(j.get("id") or j.get("job_id") or "") if isinstance(j, dict) else ""
                if jid:
                    if jid in seen_ids:
                        continue
                    seen_ids.add(jid)
                all_jobs.append(j)
            try:
                now = datetime.now(timezone.utc)
                ages = []
                for j in jobs:
                    if not isinstance(j, dict):
                        continue
                    dt = _parse_date(j.get("published_date") or j.get("created_date"))
                    if dt is not None:
                        ages.append((now - dt).total_seconds() / 3600.0)
            except Exception:
                ages = []
            if ages and min(ages) > max_age_h:
                break
            if len(jobs) < per_page:
                break
            nxt = None
            if isinstance(d, dict):
                nxt = d.get("next_cursor") or d.get("cursor")
                pi = d.get("pageInfo") or d.get("page_info") or {}
                if not nxt and isinstance(pi, dict) and pi.get("hasNextPage"):
                    nxt = pi.get("endCursor")
                if not nxt and not d.get("hasMore", True):
                    nxt = None
            cursor = nxt
            if not cursor:
                break
        return all_jobs

    async def get_detail(self, job_id):
        res = await _call_tool(
            self.client,
            "find_jobs",
            {
                "action": "get",
                "org_uid": self.org_uid,
                "params": {"id": str(job_id), "include_full_details": True},
            },
        )
        return _extract_dict(res)


# ---------- main ----------

HISTORY_FILE = "dashboard_history.jsonl"


def _record_history(run_id, run_at, job, verdict, reason, detail, drafts):
    try:
        det = detail or {}
        try:
            url = _clean_url(job.get("url") or "")
        except Exception:
            url = job.get("url") or ""
        rec = {
            "run_id": run_id, "run_at": run_at,
            "job_id": str(job.get("id") or ""),
            "title": job.get("title") or "",
            "country": job.get("country") or "",
            "budget": job.get("budget"),
            "budget_kind": job.get("budget_kind") or "",
            "budget_unknown": bool(job.get("budget_unknown")),
            "proposals_tier": job.get("proposals_tier") or "",
            "age_hours": job.get("age_hours"),
            "rating": job.get("rating_num") if job.get("rating_num") is not None else job.get("rating"),
            "verification_status": job.get("verification_status") or "",
            "keywords": list(job.get("matched_keywords") or []),
            "url": url,
            "verdict": verdict, "reason": reason or "",
            "hire_rate": det.get("hire_rate_percent"),
            "jobs_posted": det.get("jobs_posted"),
            "unhired": det.get("unhired"),
            "invites": det.get("invites_sent"),
            "hired": det.get("total_hired"),
            "offered": det.get("total_offered"),
            "preferred": list(det.get("preferred_countries") or []),
            "spend": det.get("spend_total"),
            "drafts": list(drafts or []),
        }
        with open(HISTORY_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + chr(10))
        if os.path.getsize(HISTORY_FILE) > 2000000:
            with open(HISTORY_FILE, encoding="utf-8") as fh:
                lines = fh.readlines()
            with open(HISTORY_FILE, "w", encoding="utf-8") as fh:
                fh.writelines(lines[-3000:])
    except Exception as e:
        print(f"History gagal dicatat: {e}")


async def amain(args):
    cfg = load_config()
    filters = filter_settings(cfg)
    run_at = datetime.now(timezone.utc).isoformat()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    keywords_all = flat_keywords(cfg)
    if args.max_keywords:
        keywords = keywords_all[: args.max_keywords]
    else:
        keywords = keywords_all
    max_jobs = args.limit if args.limit else int(cfg.get("max_jobs_per_run", 5))
    limit_per_keyword = args.limit_per_keyword or 10
    enrich_limit = int(filters["max_detail_checks_per_run"])

    print(f"dry_run={cfg.get('dry_run', True)} | max_jobs_per_run={max_jobs} | keywords={len(keywords_all)}")
    print("Tidak ada auto-apply di kode ini. Semua draft hanya disimpan lokal.")

    seen, _ = load_seen()
    from_file = args.from_file

    if from_file:
        with open(from_file, encoding="utf-8") as f:
            data = json.load(f)
        raw_jobs = data["jobs"] if isinstance(data, dict) and "jobs" in data else data
        print(f"Mode from-file: {len(raw_jobs)} job dari {from_file} (tanpa cek detail).")
        jobs = []
        for raw in raw_jobs:
            j = to_internal_job(raw)
            if j:
                jobs.append(j)
        # Di from-file, field rating/verifikasi yang kosong dianggap lolos
        # karena data sample tidak punya client_record.
        candidates, rejects = [], collections.Counter()
        for job in jobs:
            ok, reason = stage1_filter(job, keywords_all, filters)
            if not ok and reason.startswith(("rating client tidak terbaca",)):
                ok, reason = True, ""
            if ok:
                candidates.append(job)
            else:
                rejects[reason] += 1
                print(f"TOLAK: {job['title']} | {reason}")
        for job in candidates:
            print(
                f"LOLOS: {job['title']} | {job['country']} | {budget_label(job)} "
                f"| keyword: {', '.join(job.get('matched_keywords', []))}"
            )
        stage2 = [(j, {}) for j in candidates]
        raw_for_save = jobs
    else:
        async with McpSession() as sess:
            all_raw = []
            for kw in keywords:
                print(f"Search: {kw} ...")
                try:
                    found = await sess.search_keyword(kw, limit_per_keyword, filters)
                except Exception as e:
                    print(f"Search gagal ({kw}): {e}")
                    continue
                print(f"  -> {len(found)} job")
                all_raw.extend(found)
            # Dedup per id, urutan pertama menang.
            deduped, seen_ids = [], set()
            for raw in all_raw:
                jid = str(raw.get("id") or raw.get("job_id") or raw.get("url") or "")
                if jid and jid in seen_ids:
                    continue
                if jid:
                    seen_ids.add(jid)
                deduped.append(raw)
            print(f"Hasil live tersimpan di jobs_live.json: {len(deduped)} job unik")
            with open("jobs_live.json", "w", encoding="utf-8") as f:
                json.dump(deduped, f, ensure_ascii=False, indent=2)
            jobs = []
            for raw in deduped:
                j = to_internal_job(raw)
                if j:
                    jobs.append(j)
            # Urutkan terbaru dulu (published_date desc) sebelum cek detail.
            jobs.sort(key=lambda j: _parse_date(j["published_date"]) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
            rejects = collections.Counter()
            candidates = []
            for job in jobs:
                ok, reason = stage1_filter(job, keywords_all, filters)
                if ok:
                    candidates.append(job)
                    print(
                        f"LOLOS: {job['title']} | {job['country']} | {budget_label(job)} "
                        f"| keyword: {', '.join(job.get('matched_keywords', []))}"
                    )
                else:
                    rejects[reason] += 1
                    _record_history(run_id, run_at, job, "rejected_stage1", reason, None, [])
                    print(f"TOLAK: {job['title']} | {reason}")
            print(f"Alasan tolak: {dict(rejects)}")
            # Tahap 2: cek detail, dibatasi enrich_limit kandidat terbaru.
            stage2, checked, unchecked = [], 0, 0
            unchecked_jobs = []
            for job in candidates:
                if job["id"] in seen:
                    _record_history(run_id, run_at, job, "already_seen", "sudah pernah diproses", None, [])
                    continue
                if checked >= enrich_limit:
                    unchecked += 1
                    unchecked_jobs.append(job)
                    continue
                checked += 1
                try:
                    raw_detail = await sess.get_detail(job["id"])
                    detail = parse_detail(raw_detail)
                except Exception as e:
                    print(f"DETAIL TOLAK: {job['title']} | get detail gagal: {e}")
                    mark_seen("seen_jobs.db", job["id"], "rejected_detail", job["title"])
                    _record_history(run_id, run_at, job, "rejected_stage2", f"get detail gagal: {e}", None, [])
                    continue
                ok, reason, detail = stage2_filter(job, detail, filters)
                if ok:
                    stage2.append((job, detail))
                else:
                    print(f"DETAIL TOLAK: {job['title']} | {reason}")
                    mark_seen("seen_jobs.db", job["id"], "rejected_detail", job["title"])
                    _record_history(run_id, run_at, job, "rejected_stage2", reason, detail, [])
            for _job in unchecked_jobs:
                _record_history(run_id, run_at, _job, "unchecked", "batas cek detail", None, [])
            if unchecked:
                print(f"Detail belum dicek (batas {enrich_limit}): {unchecked} kandidat, dicoba lagi di jalan berikutnya.")
            raw_for_save = jobs

    # Dedup terhadap seen_jobs.db (untuk from-file dan live).
    new_passed = []
    for job, detail in stage2:
        if job["id"] in seen:
            print(f"SUDAH PERNAH DIPROSES: {job['title']} ({job['id']})")
        else:
            new_passed.append((job, detail))
    if from_file:
        # from-file: simpan kandidat lolos tahap 1 untuk inspeksi.
        with open("jobs_filtered.json", "w", encoding="utf-8") as f:
            json.dump([j for j, _ in new_passed], f, ensure_ascii=False, indent=2)
    else:
        with open("jobs_filtered.json", "w", encoding="utf-8") as f:
            json.dump([j for j, _ in new_passed], f, ensure_ascii=False, indent=2)

    to_draft = new_passed[:max_jobs]
    print(
        f"SEARCH-ONLY. Baru lolos: {len(new_passed)} (disimpan di jobs_filtered.json). "
        "Tanpa draft, tanpa Telegram draft."
        if args.search_only
        else f"Baru lolos tahap 2: {len(new_passed)} | akan didraft: {len(to_draft)}"
    )
    if args.search_only:
        for _job, _det in new_passed:
            _record_history(run_id, run_at, _job, "passed_search_only", "lolos tahap 2 (search-only, tanpa draft)", _det, [])
        print(f"Selesai. Total={len(raw_for_save)} lolos={len(new_passed)} baru={len(new_passed)}")
        return

    if to_draft and not from_file:
        telegram_send(
            f"Upwork scanner: {len(to_draft)} job baru lolos filter dari {len(raw_for_save)} job. "
            "Menganalisis draft..."
        )
        discord_send(
            f"Upwork scanner: {len(to_draft)} job baru lolos filter dari {len(raw_for_save)} job. Menganalisis draft..."
        )
    draft_count = 0
    skip_count = 0
    os.makedirs("drafts", exist_ok=True)
    for job, detail in to_draft:
        print(f"Menganalisis: {job['title']}")
        texts = {}
        skips = []
        g_text, g_model = gemini_draft(job, job.get("matched_keywords", []), detail)
        if g_text == "SKIP":
            skips.append("Gemini")
        elif g_text is not None:
            texts["gemini"] = (g_text, g_model)
        else:
            print(f"Draft Gemini gagal: {job['title']}")
        q_text, q_model = groq_draft(job, job.get("matched_keywords", []), detail)
        if q_text == "SKIP":
            skips.append("Groq")
        elif q_text is not None:
            texts["groq"] = (q_text, q_model)
        else:
            print(f"Draft Groq gagal: {job['title']}")
        if skips:
            print(f"SKIP (keyword hanya numpang): {job['title']} [{', '.join(skips)}] - draft AI lain dibuang, job di-skip.")
            skip_count += 1
            mark_seen("seen_jobs.db", job["id"], "skipped_centrality", job["title"])
            _record_history(run_id, run_at, job, "skipped_centrality", "SKIP oleh " + ", ".join(skips), detail, [])
            continue
        if not texts:
            print(f"Draft gagal: {job['title']}")
            _record_history(run_id, run_at, job, "draft_failed", "kedua AI gagal membuat draft", detail, [])
            continue
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(job["id"]))
        saved = []
        for label, (text, model) in texts.items():
            path = f"drafts/{safe_id}.{label}.txt"
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            print(f"DRAFT TERSIMPAN: {path} | model: {model}")
            saved.append((label, path))
        if skips:
            print(f"Catatan: {', '.join(skips)} menilai keyword hanya numpang (SKIP); draft dari AI lain tetap disimpan.")
        draft_count += 1
        mark_seen("seen_jobs.db", job["id"], "drafted", job["title"])
        _record_history(run_id, run_at, job, "drafted", "", detail, [p for _, p in saved])
        if not from_file:
            for label, path in saved:
                caption = (
                    f"Draft siap [{label}]: {job['title']}\n"
                    f"Budget: {budget_display(job)} | {job['country']}\n"
                    f"Client: {client_line(job, detail)}\n"
                    f"File: {path}\n"
                    f"Link: {_clean_url(job.get('url'))}\n"
                    "Cek manual sebelum dipakai: preferred qualifications, budget, dan history freelancer."
                )
                if skips:
                    caption += f"\nCatatan: {', '.join(skips)} menilai keyword hanya numpang (SKIP)."
                if telegram_send_file(path, caption) != 200:
                    telegram_send(caption)
                discord_send_file(path, job["title"], caption)
    print(
        f"Selesai. Total={len(raw_for_save)} lolos={len(new_passed)} baru={len(to_draft)} "
        f"draft={draft_count} skip_centrality={skip_count}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-keywords", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--limit-per-keyword", type=int, default=5)
    parser.add_argument("--search-only", action="store_true")
    parser.add_argument("--from-file", default=None)
    _args = parser.parse_args()
    try:
        asyncio.run(amain(_args))
    except Exception as _exc:
        print(f"ERROR: {_exc!r}")
        try:
            notify_error(_exc)
        except Exception as _notify_exc:
            print(f"Notif error gagal: {_notify_exc}")
        raise
