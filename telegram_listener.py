#!/usr/bin/env python3
"""Telegram command listener for upwork-job-watcher.

Polls the bot inbox (getUpdates) and runs one watcher session per command.
Only the authorized chat from .env is served. No timers: Upwork is touched
only when a command arrives. Runs under the project venv python.
"""

import ast
import html
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(BASE_DIR, "listener_state.json")
BUDGET_PATH = os.path.join(BASE_DIR, "call_budget.json")
TOKEN_PATH = os.path.join(BASE_DIR, ".upwork_token.json")
TOKEN_TTL_SECONDS = 24 * 3600
TOKEN_WARN_SECONDS = 20 * 3600

if load_dotenv is not None:
    load_dotenv(os.path.join(BASE_DIR, ".env"))

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
API = "https://api.telegram.org/bot" + TOKEN

POLL_TIMEOUT = 30
RETRY_SLEEP = 10
RUN_TIMEOUT = 900
MIN_GAP_SECONDS = 3600

RUNNING = {"active": False}


def api_call(method, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(API + "/" + method, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=POLL_TIMEOUT + 15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def send_text(text, parse_mode=None):
    try:
        payload = {"chat_id": CHAT_ID, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        api_call("sendMessage", payload)
    except Exception as exc:
        print("Gagal kirim Telegram: " + repr(exc), flush=True)


def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return {}


def save_state(state):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(state, handle)
    os.replace(tmp, STATE_PATH)


def tail_lines(text, count=18):
    lines = text.splitlines()
    return "\n".join(lines[-count:])


def group_reject_reasons(out):
    groups = {}
    for line in out.splitlines():
        if not line.startswith("Alasan tolak:"):
            continue
        try:
            reasons = ast.literal_eval(line.split(":", 1)[1].strip())
        except Exception:
            return {}
        for key, count in reasons.items():
            if key.startswith("umur job"):
                label = "Umur di atas 1 jam"
            elif key.startswith("budget "):
                label = "Budget di bawah minimum"
            elif key.startswith("blacklist:"):
                label = "Blacklist (" + key.split(":", 1)[1].strip() + ")"
            elif key.startswith("negara client dikecualikan:"):
                label = "Negara dikecualikan (" + key.split(":", 1)[1].strip() + ")"
            elif key.startswith("proposals_tier"):
                label = "Proposal di atas batas"
            elif key == "rating client tidak terbaca":
                label = "Rating tidak terbaca"
            else:
                label = key
            groups[label] = groups.get(label, 0) + count
        return groups
    return {}


def explain_failure(out):
    low = out.lower()
    if "token upwork gagal refresh" in low or "gagal refresh" in low:
        return "Kemungkinan penyebab: token mati. Langkah berikutnya: re-mint di laptop Ilmantara lalu salin file token ke laptop ini."
    if "akses pencarian dibatasi" in low or "search has been restricted" in low or "searchrestrictederror" in low:
        return "Kemungkinan penyebab: akses pencarian dibatasi Upwork. Langkah berikutnya: jangan jalankan sesi lagi hari ini; tunggu reset 07:00 WIB dan coba satu pencarian manual."
    if "cancellederror" in low or "cancelled via cancel scope" in low:
        return "Kemungkinan penyebab: sesi terputus di tengah jalan (kemungkinan jaringan). Langkah berikutnya: coba lagi setelah jeda; kalau berulang, periksa koneksi."
    if "urlerror" in low or "connectionerror" in low or "nameresolutionerror" in low or "temporary failure in name resolution" in low or "connecterror" in low:
        return "Kemungkinan penyebab: jaringan bermasalah. Langkah berikutnya: periksa koneksi lalu coba lagi."
    return ""


def token_warn_line():
    try:
        minted = os.path.getmtime(TOKEN_PATH)
    except Exception:
        return ""
    age = time.time() - minted
    if age >= TOKEN_TTL_SECONDS:
        return "❌ Token kemungkinan SUDAH mati. Re-mint di laptop Ilmantara sebelum sesi berikutnya."
    if age >= TOKEN_WARN_SECONDS:
        return "⚠️ Token hampir mati — siapkan re-mint di laptop Ilmantara sebelum sesi berikutnya."
    return ""


def completion_message(group, out, ok):
    totals = None
    for match in re.finditer(r"Total=(\d+)\s+lolos=(\d+)\s+baru=(\d+)(?:\s+draft=(\d+))?(?:\s+skip_centrality=(\d+))?", out):
        totals = match.groups()
    if not ok or totals is None:
        header = "Sesi grup " + str(group) + (" selesai." if ok else " gagal.")
        tail = tail_lines(out)
        why = explain_failure(out)
        if why:
            tail = tail + "\n\n" + why
        warn = token_warn_line()
        if warn:
            tail = tail + "\n\n" + warn
        return header + "\n\n" + tail, None
    total, lolos, baru, draft, skip = totals
    passed = []
    detail_rejects = []
    for line in out.splitlines():
        if line.startswith("LOLOS:"):
            parts = line[len("LOLOS:"):].strip().split(" | ")
            title = parts[0] if parts else ""
            meta = ", ".join([p for p in parts[1:3] if p])
            passed.append((title, meta))
        elif line.startswith("DETAIL TOLAK:"):
            rest = line[len("DETAIL TOLAK:"):].strip()
            title, _, reason = rest.partition(" | ")
            detail_rejects.append((title, reason))
    lines = []
    lines.append("<b>✅ Sesi grup " + str(group) + " selesai</b>")
    lines.append("")
    lines.append("<b>Ringkasan</b>")
    lines.append("• Total: <code>" + str(total) + "</code> job")
    lines.append("• Lolos: <code>" + str(lolos) + "</code>")
    lines.append("• Baru: <code>" + str(baru) + "</code>")
    lines.append("• Draft: <code>" + str(draft or 0) + "</code>")
    lines.append("• Di-skip: <code>" + str(skip or 0) + "</code>")
    if passed:
        lines.append("")
        lines.append("<b>Job lolos</b>")
        for title, meta in passed[:10]:
            lines.append("• " + html.escape(title[:120]) + (" (" + html.escape(meta) + ")" if meta else ""))
        if len(passed) > 10:
            lines.append("• ... dan " + str(len(passed) - 10) + " lagi")
    reasons = group_reject_reasons(out)
    if reasons:
        lines.append("")
        lines.append("<b>Alasan tolak</b>")
        for label, count in sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append("• " + html.escape(label) + ": " + str(count) + " job")
    if detail_rejects:
        lines.append("")
        lines.append("<b>Detail tolak</b>")
        for title, reason in detail_rejects[:10]:
            lines.append("• " + html.escape(title[:120]) + (" — " + html.escape(reason[:150]) if reason else ""))
    warn = token_warn_line()
    if warn:
        lines.append("")
        lines.append(warn)
    return "\n".join(lines), "HTML"


def run_session(group):
    started = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, "main.py", "--group", str(group)],
            cwd=BASE_DIR, capture_output=True, text=True, timeout=RUN_TIMEOUT,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode == 0:
            state = load_state()
            state["last_run"] = {"group": group, "at": started,
                                 "ok": True}
            save_state(state)
        msg, mode = completion_message(group, out, proc.returncode == 0)
        send_text(msg, parse_mode=mode)
    except subprocess.TimeoutExpired:
        send_text("Sesi grup " + str(group) + " dihentikan: melebihi batas waktu.")
    except Exception as exc:
        send_text("Sesi grup " + str(group) + " gagal: " + repr(exc))
    finally:
        RUNNING["active"] = False

def token_age_block():
    try:
        minted = os.path.getmtime(TOKEN_PATH)
    except Exception:
        return "• File token tidak ditemukan."
    now = time.time()
    age_seconds = now - minted
    if age_seconds < 0:
        age_seconds = 0
    age_hours = int(age_seconds // 3600)
    death = minted + TOKEN_TTL_SECONDS
    death_local = time.localtime(death)
    now_local = time.localtime(now)
    if death_local.tm_yday == now_local.tm_yday and death_local.tm_year == now_local.tm_year:
        when = "hari ini " + time.strftime("%H:%M", death_local)
    else:
        when = time.strftime("%d %b %H:%M", death_local)
    base = "• Umur token: " + str(age_hours) + " jam · perkiraan mati " + when
    if age_seconds >= TOKEN_TTL_SECONDS:
        return base + "\n❌ Token kemungkinan SUDAH mati. Kalau sesi gagal refresh, re-mint di laptop Ilmantara lalu salin file token ke laptop ini."
    if age_seconds >= TOKEN_WARN_SECONDS:
        return base + "\n⚠️ Token hampir mati — siapkan re-mint di laptop Ilmantara sebelum sesi berikutnya."
    return base


def status_text():
    budget = {}
    try:
        with open(BUDGET_PATH, encoding="utf-8") as handle:
            budget = json.load(handle)
    except Exception:
        budget = {}
    now_utc = time.gmtime()
    if budget.get("hour_key") == time.strftime("%Y-%m-%dT%H", now_utc):
        hour = budget.get("hour_count", "?")
    else:
        hour = 0
    if budget.get("day_key") == time.strftime("%Y-%m-%d", now_utc):
        day = budget.get("day_count", "?")
    else:
        day = 0
    state = load_state()
    last = state.get("last_run") or {}
    last_at = last.get("at") or 0
    if last:
        when = time.strftime("%H:%M", time.localtime(last_at))
        hasil = "✅ berhasil" if last.get("ok") else "❌ gagal"
        last_line = "• Grup " + str(last.get("group")) + " · " + when + " · " + hasil
    else:
        last_line = "• Belum ada sesi lewat Telegram."
    if RUNNING["active"]:
        busy = "• Sesi sedang berjalan."
    else:
        wait = MIN_GAP_SECONDS - (time.time() - last_at)
        if wait > 0:
            busy = "• Perintah /jalan berikutnya bisa dalam " + str(int(wait // 60) + 1) + " menit."
        else:
            busy = "• Tidak ada sesi berjalan."
    return ("<b>📊 Status Listener</b>\n\n<b>Anggaran</b>\n• Per jam: <code>" + str(hour) + "/140</code>\n• Per hari: <code>" + str(day) + "/700</code>\n\n<b>Token Upwork</b>\n" + token_age_block() + "\n\n<b>Sesi terakhir lewat Telegram</b>\n" + last_line + "\n\n<b>Kondisi</b>\n" + busy)


def handle_command(text):
    cmd = text.strip().split()[0].lower() if text.strip() else ""
    if cmd in ("/start", "/help", "/bantuan"):
        send_text("Perintah:\n/jalan1 - sesi grup 1 (utama)\n/jalan2 - sesi grup 2\n/jalan3 - sesi grup 3\n/status - anggaran dan sesi terakhir\n/cek <id/tautan> - cek satu job manual")
        return
    if cmd == "/status":
        send_text(status_text(), parse_mode="HTML")
        return
    if cmd == "/cek":
        rest = text.strip()[len(cmd):].strip()
        found = re.search(r"\d{10,}", rest)
        if not found:
            send_text("Pakai: /cek <id job atau tautan Upwork>\nContoh: /cek 2118527230669974661")
            return
        if RUNNING["active"]:
            send_text("Masih ada sesi berjalan. Tunggu sampai selesai.")
            return
        job_id = found.group(0)
        send_text("Mengecek job " + job_id + " ...")
        try:
            proc = subprocess.run(
                [sys.executable, "main.py", "--check-job", job_id],
                cwd=BASE_DIR, capture_output=True, text=True, timeout=180,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            send_text(tail_lines(out, 30))
        except subprocess.TimeoutExpired:
            send_text("Pengecekan job melebihi batas waktu.")
        except Exception as exc:
            send_text("Cek job gagal: " + repr(exc))
        return
    group = None
    if cmd in ("/jalan1", "/jalan2", "/jalan3"):
        group = int(cmd[-1])
    if group is None:
        send_text("Perintah tidak dikenal. Ketik /bantuan.")
        return
    if RUNNING["active"]:
        send_text("Masih ada sesi berjalan. Tunggu sampai selesai.")
        return
    state = load_state()
    last = state.get("last_run") or {}
    last_at = last.get("at") or 0
    wait = MIN_GAP_SECONDS - (time.time() - last_at)
    if wait > 0:
        mins = int(wait // 60) + 1
        send_text("Sesi terakhir jalan kurang dari 60 menit lalu. Coba lagi dalam " + str(mins) + " menit.")
        return
    RUNNING["active"] = True
    send_text("Sesi grup " + str(group) + " dimulai.")
    threading.Thread(target=run_session, args=(group,), daemon=True).start()


def poll_loop():
    offset = None
    try:
        first = api_call("getUpdates", {"timeout": 0, "allowed_updates": ["message"]})
        for upd in first.get("result", []):
            upd_id = upd.get("update_id", 0) + 1
            if offset is None or upd_id > offset:
                offset = upd_id
    except Exception as exc:
        print("Gagal buang antrean awal: " + repr(exc), flush=True)
    print("Listener jalan. Menunggu perintah Telegram.", flush=True)
    while True:
        try:
            payload = {"timeout": POLL_TIMEOUT, "allowed_updates": ["message"]}
            if offset is not None:
                payload["offset"] = offset
            data = api_call("getUpdates", payload)
            for upd in data.get("result", []):
                offset = upd.get("update_id", 0) + 1
                msg = upd.get("message") or {}
                chat = msg.get("chat") or {}
                if str(chat.get("id")) != str(CHAT_ID):
                    continue
                text = msg.get("text") or ""
                if text:
                    handle_command(text)
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                print("Konflik 409: listener lain sedang jalan. Berhenti.", flush=True)
                return
            print("HTTPError: " + repr(exc) + ", coba lagi.", flush=True)
            time.sleep(RETRY_SLEEP)
        except Exception as exc:
            print("Poll error: " + repr(exc) + ", coba lagi.", flush=True)
            time.sleep(RETRY_SLEEP)


def main():
    if not TOKEN or not CHAT_ID:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID belum diset. Berhenti.", flush=True)
        return
    try:
        poll_loop()
    except KeyboardInterrupt:
        print("Listener berhenti.", flush=True)


if __name__ == "__main__":
    main()

