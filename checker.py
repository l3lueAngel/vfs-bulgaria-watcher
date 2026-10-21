"""VFS Bulgaria watcher v3.2: independent VisaTour + cautious official VFS checks."""
from __future__ import annotations
import os, re, json, html, hashlib, time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode
import requests
from bs4 import BeautifulSoup

TZ_TR = timezone(timedelta(hours=3))
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "").strip()
STATE_FILE = Path("state.json")
OUTAGE_FILE = Path(".cache/outage.json")
VFS_URL = "https://visa.vfsglobal.com/tur/en/bgr"
VFS_LOGIN_URL = "https://visa.vfsglobal.com/tur/en/bgr/login"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/128.0.0.0 Safari/537.36")
CATEGORIES = [
    ("Application with Biometric (Individual - Bireysel)",
     "Application with Biometric ( Individual - Bireysel)"),
    ("Application with Biometric", "Application with Biometric"),
]
EXCLUDED = ("0-12", "0–12", "child", "without biometric", "without-biometric")


def now_tr():
    return datetime.now(TZ_TR)


def send_telegram(message: str, silent: bool = False) -> bool:
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("[Telegram] TG_BOT_TOKEN/TG_CHAT_ID secrets eksik; mesaj gönderilmedi.")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": message,
                  "parse_mode": "HTML", "disable_notification": silent,
                  "disable_web_page_preview": True}, timeout=15)
        print(f"[Telegram] HTTP {r.status_code} silent={silent}")
        return r.ok
    except requests.RequestException as exc:
        print(f"[Telegram] error: {exc}")
        return False


def load_state():
    if not STATE_FILE.exists():
        return {"initialized": False, "seen": [], "last_success": None, "vfs_seen": []}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        # Preserve existing V3.1 keys/values; never reset the existing seen list.
        data.setdefault("initialized", True)
        data.setdefault("seen", [])
        data.setdefault("last_success", None)
        data.setdefault("vfs_seen", [])
        return data
    except Exception as exc:
        raise RuntimeError(f"state.json okunamadı; güvenlik için state sıfırlanmadı: {exc}")


def save_state(state):
    state["seen"] = list(dict.fromkeys(state.get("seen", [])))[-1000:]
    state["vfs_seen"] = list(dict.fromkeys(state.get("vfs_seen", [])))[-500:]
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def record_id(record):
    raw = "|".join(str(record.get(k, "")) for k in ("category", "center", "date_opened", "reg_time"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_visatour_records():
    records, errors = [], []
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9", "Cache-Control": "no-cache"}
    for label, category in CATEGORIES:
        params = {"origin": "TUR", "destination": "Bulgaria", "category": category}
        url = "https://alerts.visatour.org/?" + urlencode(params)
        try:
            response = requests.get(url, headers=headers, timeout=20)
            print(f"[VisaTour] {label}: HTTP {response.status_code}, {len(response.text)} bytes")
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            cards = soup.select("div.card-surface")
            if not cards:
                # Don't confuse a changed layout/blocked page with zero records.
                if "cloudflare" in response.text.lower() or "access denied" in response.text.lower():
                    raise RuntimeError("engel/Cloudflare sayfası döndü")
                raise RuntimeError("HTTP 200 fakat card-surface bulunamadı; parser/site yapısı değişmiş olabilir")
            for card in cards:
                title = card.select_one("[title]")
                category_text = (title.get("title", "") if title else "")
                text = " ".join(card.stripped_strings)
                lower = (category_text + " " + text).lower()
                if any(term in lower for term in EXCLUDED):
                    continue
                # Require the card to match the queried category; avoid accidental cross-category parse.
                if label.startswith("Application with Biometric (Individual"):
                    if not ("individual" in lower or "bireysel" in lower):
                        continue
                else:
                    if "individual" in lower or "bireysel" in lower:
                        continue
                    if "biometric" not in lower and "biyometrik" not in lower:
                        continue
                date_opened = _field_after_label(card, ("Earliest Date Opened", "En Erken Açılış Tarihi", "Açılış Tarihi"))
                center = _field_after_label(card, ("Center", "Merkez"))
                reg_time = _find_timestamp(text)
                if not center or not date_opened or not reg_time:
                    print(f"[VisaTour] Kart atlandı: gerekli alan eksik: {text[:180]}")
                    continue
                records.append({"source": "VisaTour", "category": category_text or label,
                                "center": center, "date_opened": date_opened,
                                "reg_time": reg_time, "url": url})
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            print(f"[VisaTour ERROR] {label}: {exc}")
    return records, errors


def _field_after_label(card, labels):
    # Label/strong markup is used by current VisaTour cards; also tolerate translated labels.
    for node in card.find_all(string=True):
        value = node.strip().rstrip(":").lower()
        if any(value == label.lower() for label in labels):
            parent = node.parent
            strong = parent.find_next("strong") if parent else None
            if strong and strong.get_text(strip=True):
                return html.unescape(strong.get_text(" ", strip=True))
            nxt = node.find_parent().find_next("strong") if node.find_parent() else None
            if nxt and nxt.get_text(strip=True):
                return html.unescape(nxt.get_text(" ", strip=True))
    # fallback for cards whose labels and values are represented as adjacent text
    text = " ".join(card.stripped_strings)
    for label in labels:
        m = re.search(re.escape(label) + r"\s*:?\s*([^|]{2,80})", text, re.I)
        if m:
            return m.group(1).strip()
    return ""


def _find_timestamp(text):
    m = re.search(r"\b(20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\b", text)
    return m.group(1) if m else ""


def check_official_vfs():
    """Check VFS public page and dynamically inspect its center selector; never treat a static date as a slot."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return [], f"Playwright import edilemedi: {exc}"

    findings = []
    negative_patterns = [
        r"no appointment slots", r"no slots available", r"currently no slots",
        r"no appointment is available", r"no appointments available",
        r"uygun randevu bulunmamaktadır", r"randevu bulunamadı",
        r"there are no appointments", r"no availability"
    ]
    date_pattern = re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b")
    center_pattern = re.compile(r"ankara|edirne|istanbul|izmir|antalya|gaziantep|bursa|trabzon|adana|bodrum|antalya|kayseri", re.I)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=USER_AGENT, viewport={"width": 1365, "height": 900})
            try:
                response = page.goto(VFS_URL, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)
                status = response.status if response else "unknown"
                if status != 200:
                    return [], f"VFS public page HTTP {status}"

                baseline = page.locator("body").inner_text(timeout=10000)
                print(f"[VFS] Public page HTTP {status}; {len(baseline)} chars")

                # Retain explicit page-level signals, but ignore dates alone.
                low = baseline.lower()
                explicit_positive = any(re.search(pattern, low, re.I) for pattern in [
                    r"appointments?\s+available", r"available\s+appointment\s+slots?",
                    r"slots?\s+available\s+for\s+booking", r"randevu\s+slotları\s+mevcut",
                    r"uygun\s+randevu\s+bulundu", r"randevu\s+alınabilir"
                ])
                explicit_negative = any(re.search(pattern, low, re.I) for pattern in negative_patterns)
                if explicit_positive and not explicit_negative:
                    findings.append({
                        "source": "Official VFS",
                        "category": "VFS public-page availability signal",
                        "center": "VFS Global Bulgaria (merkez teyidi gerekli)",
                        "date_opened": "Açık müsaitlik ifadesi görüldü; takvimde teyit edin",
                        "reg_time": now_tr().strftime("%Y-%m-%d %H:%M:%S"),
                        "url": VFS_LOGIN_URL
                    })

                selects = page.locator("select")
                print(f"[VFS] Public page select sayısı: {selects.count()}")
                target_select = None
                options = []
                for i in range(selects.count()):
                    sel = selects.nth(i)
                    option_nodes = sel.locator("option")
                    candidate_options = []
                    for j in range(option_nodes.count()):
                        opt = option_nodes.nth(j)
                        value = (opt.get_attribute("value") or "").strip()
                        label = (opt.inner_text() or "").strip()
                        if value and label and not any(word in label.lower() for word in ("choose", "select", "please select")):
                            candidate_options.append((value, label))
                    joined = " ".join(label for _, label in candidate_options)
                    if len(candidate_options) >= 2 and center_pattern.search(joined):
                        target_select = sel
                        options = candidate_options
                        break

                if target_select is None:
                    print("[VFS] Merkez seçicisi bulunamadı; gerçek randevu takvimi giriş/oturum arkasında olabilir.")
                else:
                    print(f"[VFS] Merkez seçicisinde {len(options)} seçenek bulundu; tek tek kontrol ediliyor.")
                    checked = 0
                    for value, label in options:
                        city = label.replace("Bulgaria Visa Application Center", "").replace(",", "").strip()
                        try:
                            target_select.select_option(value=value, timeout=5000)
                            target_select.dispatch_event("input")
                            target_select.dispatch_event("change")
                            page.wait_for_timeout(3500)
                            body = page.locator("body").inner_text(timeout=10000)
                            if body == baseline:
                                print(f"[VFS] {city}: seçim sonrası sayfa değişmedi; slot sinyali yok.")
                                continue
                            checked += 1
                            lower = body.lower()
                            no_slots = any(re.search(pattern, lower, re.I) for pattern in negative_patterns)
                            dates = date_pattern.findall(body)
                            # A date is only a candidate when changing the center altered the page
                            # and no explicit no-slot message is present. Do not alert on the landing page's static dates.
                            if dates and not no_slots:
                                date_value = dates[-1]
                                print(f"[VFS] Olası takvim sinyali: {city} -> {date_value}; kullanıcı girişinde teyit gerekli.")
                                findings.append({
                                    "source": "Official VFS",
                                    "category": "Application with Biometric (Individual - Bireysel)",
                                    "center": city,
                                    "date_opened": f"Merkez seçimi sonrası görülen tarih: {date_value} (takvimde teyit gerekli)",
                                    "reg_time": now_tr().strftime("%Y-%m-%d %H:%M:%S"),
                                    "url": VFS_LOGIN_URL
                                })
                                # Send one alert per run; subsequent checks can continue next run.
                                break
                            print(f"[VFS] {city}: açık slot sinyali yok.")
                        except Exception as exc:
                            print(f"[VFS] {city}: merkez kontrol hatası: {type(exc).__name__}")
                    print(f"[VFS] Merkez kontrolü tamamlandı; dinamik değişiklik görülen merkez: {checked}")
            finally:
                browser.close()
    except Exception as exc:
        return [], f"VFS kontrol hatası: {type(exc).__name__}: {exc}"
    return findings, None

def load_outage():
    try:
        return json.loads(OUTAGE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"errors": {}, "outage_notified": {}}


def save_outage(data):
    OUTAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTAGE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def update_source_health(outage, source, error):
    errors = outage.setdefault("errors", {})
    notified = outage.setdefault("outage_notified", {})
    if error:
        errors[source] = int(errors.get(source, 0)) + 1
        print(f"[{source}] Ardışık hata: {errors[source]}")
        if errors[source] >= 5 and not notified.get(source):
            send_telegram(f"⚠️ <b>{source} yaklaşık 10 dakikadır erişilemiyor.</b>\nDiğer kaynak bağımsız kontrol edilmeye devam ediyor.", silent=True)
            notified[source] = True
    else:
        was_down = bool(notified.get(source))
        errors[source] = 0
        notified[source] = False
        if was_down:
            send_telegram(f"🟢 <b>{source} tekrar erişilebilir.</b> Normal tarama devam ediyor.", silent=True)


def send_new_record(record, key, seen):
    if key in seen:
        return False
    message = (
        "🇧🇬 🚨 <b>BULGARİSTAN RANDEVU HAREKETİ</b> 🚨\n\n"
        f"📡 <b>Kaynak:</b> {html.escape(record['source'])}\n"
        f"📍 <b>Merkez:</b> {html.escape(record['center'])}\n"
        f"🎯 <b>Kategori:</b> {html.escape(record['category'])}\n"
        f"📅 <b>Görünen tarih/durum:</b> {html.escape(record['date_opened'])}\n"
        f"🕒 <b>Kayıt zamanı:</b> {html.escape(record['reg_time'])}\n\n"
        f"🔗 <a href=\"{html.escape(record['url'], quote=True)}\">Kaynağı aç</a>\n"
        "<i>Bu bir açılış sinyalidir; randevunun hâlâ boş olduğunu başvuru ekranında doğrulayın.</i>"
    )
    if send_telegram(message, silent=False):
        seen.add(key)
        return True
    return False


def main():
    print(f"VFS BULGARIA WATCHER v3.2 — {now_tr().isoformat()}")
    state = load_state()
    seen = set(state.get("seen", []))
    vfs_seen = set(state.get("vfs_seen", []))
    outage = load_outage()

    vt_records, vt_errors = get_visatour_records()
    update_source_health(outage, "VisaTour", "; ".join(vt_errors) if vt_errors else None)
    # Process records from any category that succeeded, even if the other URL failed.
    # This prevents one broken VisaTour category from suppressing valid records from the other.
    for record in vt_records:
        key = record_id(record)
        send_new_record(record, key, seen)
    if not vt_errors:
        state["last_success"] = now_tr().isoformat()

    vfs_records, vfs_error = check_official_vfs()
    update_source_health(outage, "Official VFS", vfs_error)
    for record in vfs_records:
        # Separate VFS identity namespace; do not collide with VisaTour hashes.
        key = hashlib.sha256(("official-vfs|" + "|".join(str(record.get(k, "")) for k in ("center", "category", "date_opened"))).encode()).hexdigest()
        if key not in vfs_seen:
            if send_new_record(record, key, vfs_seen):
                pass

    state["seen"] = list(seen)
    state["vfs_seen"] = list(vfs_seen)
    save_state(state)
    save_outage(outage)

    # A healthy source with a valid parsed page and no new records is not the same as both sources failing.
    if not vt_errors and not vfs_error and now_tr().minute < 3:
        send_telegram(
            "🔄 <b>VFS Bulgaria Saatlik Durum</b>\n\n"
            "🟢 Bot: Aktif\n"
            f"📡 VisaTour kayıtları: {len(vt_records)}\n"
            "📡 Resmî VFS: Sayfa kontrol edildi; herkese açık sayfada doğrulanabilir slot verisi bulunmayabilir.\n"
            "🔕 Bu saatlik rapor sessiz gönderildi.", silent=True)
    print(f"[DONE] VisaTour records={len(vt_records)} errors={len(vt_errors)}; VFS errors={bool(vfs_error)}")


if __name__ == "__main__":
    main()