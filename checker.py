import os
import re
import html
import json
import hashlib
import requests
from pathlib import Path
from datetime import datetime, timezone, timedelta


# =========================================================
# CONFIG
# =========================================================

TZ_TR = timezone(timedelta(hours=3))

URL_INDIVIDUAL = (
    "https://alerts.visatour.org/"
    "?origin=TUR&destination=Bulgaria"
    "&category=Application+with+Biometric+%28+Individual+-+Bireysel%29"
)

URL_BIOMETRIC = (
    "https://alerts.visatour.org/"
    "?origin=TUR&destination=Bulgaria"
    "&category=Application+with+Biometric"
)

VFS_LOGIN_URL = "https://visa.vfsglobal.com/tur/en/bgr/login"

STATE_FILE = Path("state.json")
OUTAGE_CACHE_FILE = Path(".cache/outage.json")

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN") or "8918314722:AAEbbb81iUqvP2QU24vnrwDrt4m9TmQ4BIA"
TG_CHAT_ID = os.getenv("TG_CHAT_ID") or "6769707789"

MAX_SEEN_RECORDS = 1000
REQUEST_TIMEOUT = 20


# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(message: str, silent: bool = False) -> bool:
    """
    Telegram'a mesaj gönderir.
    silent=False -> gerçek slot alarmı (SESLİ)
    silent=True  -> durum/saatlik/outage mesajları (SESSİZ)
    """
    if (
        not TG_BOT_TOKEN
        or TG_BOT_TOKEN == "BURAYA_BOT_TOKEN"
        or not TG_CHAT_ID
        or TG_CHAT_ID == "BURAYA_CHAT_ID"
    ):
        print("[!] Telegram token/chat ID ayarlanmamış.")
        return False

    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": TG_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_notification": silent,
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        print(f"[Telegram] HTTP {response.status_code} (silent={silent})")

        if not response.ok:
            print("[Telegram ERROR]", response.text[:500])

        return response.ok

    except Exception as exc:
        print("[Telegram Exception]", repr(exc))
        return False


# =========================================================
# STATE (seen hashes in git repo)
# =========================================================

def default_state():
    return {
        "version": "3.1",
        "initialized": False,
        "v3_1_baseline": False,
        "seen": []
    }


def load_state():
    if not STATE_FILE.exists():
        print("[State] state.json yok; ilk kurulum.")
        return default_state()

    try:
        with STATE_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            raise ValueError("state.json root object değil.")

        data.setdefault("version", "3.1")
        data.setdefault("initialized", False)
        data.setdefault("v3_1_baseline", False)
        data.setdefault("seen", [])

        if not isinstance(data["seen"], list):
            raise ValueError("state.json seen list değil.")

        return data

    except Exception as exc:
        print("[State ERROR]", repr(exc))
        raise


def save_state(state):
    """
    Sadece gerçekten yeni hash eklendiğinde çağrılır.
    """
    state["version"] = "3.1"
    state["seen"] = state["seen"][-MAX_SEEN_RECORDS:]

    temp_file = STATE_FILE.with_suffix(".tmp")

    with temp_file.open("w", encoding="utf-8") as f:
        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2,
            sort_keys=True
        )
        f.write("\n")

    temp_file.replace(STATE_FILE)

    print(f"[State] Kaydedildi. Seen={len(state['seen'])}")


# =========================================================
# OUTAGE TRACKER (isolated in .cache/outage.json - NO GIT COMMITS)
# =========================================================

def load_outage_state():
    if OUTAGE_CACHE_FILE.exists():
        try:
            with OUTAGE_CACHE_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    data.setdefault("consecutive_errors", 0)
                    data.setdefault("outage_notified", False)
                    data.setdefault("last_hourly_hour", -1)
                    return data
        except Exception as exc:
            print(f"[Outage Cache Read Warning] {repr(exc)}")

    return {"consecutive_errors": 0, "outage_notified": False, "last_hourly_hour": -1}


def save_outage_state(outage_state):
    try:
        OUTAGE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp_file = OUTAGE_CACHE_FILE.with_suffix(".tmp")
        with temp_file.open("w", encoding="utf-8") as f:
            json.dump(outage_state, f, indent=2)
            f.write("\n")
        temp_file.replace(OUTAGE_CACHE_FILE)
    except Exception as exc:
        print(f"[Outage Cache Write ERROR] {repr(exc)}")


def handle_error(exc: Exception, now_tr: datetime):
    outage_state = load_outage_state()
    outage_state["consecutive_errors"] = outage_state.get("consecutive_errors", 0) + 1
    count = outage_state["consecutive_errors"]
    notified = outage_state.get("outage_notified", False)

    print(f"[Outage Tracker] Ardışık hata: {count}/5 (Önceki bildirim: {notified})")

    # 1-4 hata: Telegram YOK
    # 5. ardışık hata: bir kez SESSİZ Telegram
    if count == 5 and not notified:
        print("[Outage Tracker] 5. hata ulaşıldı -> Sessiz Telegram uyarısı gönderiliyor.")
        send_telegram(
            "⚠️ <b>VisaTour yaklaşık 10 dakikadır erişilemiyor. Bot çalışıyor ve tekrar deniyor.</b>",
            silent=True
        )
        outage_state["outage_notified"] = True

    # Saatlik rapor penceresi (:00 - :02) kontrolü - Hata olsa dahi saatlik sessiz rapor kesilmez!
    last_hour = outage_state.get("last_hourly_hour", -1)
    if now_tr.minute < 3 and last_hour != now_tr.hour:
        print(f"[Hourly] Saat başı ({now_tr.hour:02d}:00) durum raporu gönderiliyor (VisaTour erişim hatası bildirimi).")
        err_short = "HTTP 502 Bad Gateway" if "502" in str(exc) else str(exc)[:60]
        send_telegram(
            hourly_error_message(now_tr, err_short),
            silent=True
        )
        outage_state["last_hourly_hour"] = now_tr.hour

    save_outage_state(outage_state)


def handle_success(now_tr: datetime, cards: list):
    outage_state = load_outage_state()
    notified = outage_state.get("outage_notified", False)

    if notified:
        print("[Outage Recovery] VisaTour tekrar erişilebilir -> Sessiz bildirim gönderiliyor.")
        send_telegram(
            "🟢 <b>VisaTour tekrar erişilebilir. Normal tarama devam ediyor.</b>",
            silent=True
        )

    outage_state["consecutive_errors"] = 0
    outage_state["outage_notified"] = False
    save_outage_state(outage_state)


# =========================================================
# RECORD ID
# =========================================================

def make_record_id(card: dict) -> str:
    """
    Her kaydın category + center + date_opened + reg_time
    değerlerini birleştirip benzersiz SHA-256 hash üretir.
    Böylece iki kategorideki benzer merkezler karışmaz.
    """
    raw = "|".join([
        card["category"].strip(),
        card["center"].strip(),
        card["date_opened"].strip(),
        card["reg_time"].strip(),
    ])

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# =========================================================
# VISATOUR FETCHER
# =========================================================

def fetch_visatour(url: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }

    response = requests.get(
        url,
        headers=headers,
        timeout=REQUEST_TIMEOUT
    )

    cat_label = url.split("category=")[-1][:35]
    print(f"[VisaTour] HTTP {response.status_code} / {len(response.text)} bytes ({cat_label})")

    response.raise_for_status()

    if not response.text.strip():
        raise RuntimeError(f"VisaTour boş HTML döndürdü ({cat_label}).")

    return response.text


# =========================================================
# PARSER
# =========================================================

def parse_visatour_cards(page_html: str) -> list:
    """
    Hem İngilizce hem Türkçe HTML etiketlerini eşleyen çift dilli regex.
    0-12, child, without biometric kategorilerini kesinlikle filtreler.
    """
    card_pattern = re.compile(
        r'<div class="card-surface"[^>]*>.*?'
        r'title="([^"]+)"[^>]*>\s*'
        r'([^<\r\n]+)\s*</span>.*?'
        r'(?:Earliest Date Opened|A[^:<]*Tarih):</span>\s*'
        r'<strong[^>]*>([^<]+)</strong>.*?'
        r'(?:Center|Merkez):</span>\s*'
        r'<strong[^>]*>([^<]+)</strong>.*?'
        r'<span>'
        r'(\d{4}-\d{2}-\d{2}\s+'
        r'\d{2}:\d{2}:\d{2})'
        r'</span>',
        re.DOTALL | re.IGNORECASE
    )

    cards = []

    for match in card_pattern.finditer(page_html):
        category = html.unescape(match.group(1).strip())
        date_opened = html.unescape(match.group(3).strip())
        center = html.unescape(match.group(4).strip())
        reg_time = match.group(5).strip()

        cat_lower = category.lower()

        # Kesinlikle elenecek kategoriler
        if any(bad in cat_lower for bad in ["without", "0-12", "child"]):
            continue

        # Sadece Biyometrik veya Bireysel olanlar
        if not ("biometric" in cat_lower or "biyometrik" in cat_lower or "bireysel" in cat_lower):
            continue

        cards.append({
            "category": category,
            "date_opened": date_opened,
            "center": center,
            "reg_time": reg_time,
        })

    cards.sort(key=lambda card: card["reg_time"])
    return cards


def is_individual(category: str) -> bool:
    cat_lower = category.lower()
    return "individual" in cat_lower or "bireysel" in cat_lower


# =========================================================
# MESSAGE BUILDERS
# =========================================================

def slot_message(card: dict) -> str:
    center = html.escape(card["center"])
    opened = html.escape(card["date_opened"])
    category = html.escape(card["category"])
    reg_time = html.escape(card["reg_time"])

    # Individual/Bireysel için özel başlık, Genel biyometrik için ikincil başlık
    if is_individual(card["category"]):
        title = "🇧🇬 🚨 <b>BİREYSEL BİYOMETRİK RANDEVU HAREKETİ!</b> 🚨"
    else:
        title = "🇧🇬 ⚠️ <b>BİYOMETRİK RANDEVU HAREKETİ!</b>"

    return (
        f"{title}\n\n"
        f"📍 <b>Merkez:</b> {center}\n"
        f"📅 <b>Açılan Tarih / Durum:</b> {opened}\n"
        f"🎯 <b>Kategori:</b> {category}\n"
        f"🕒 <b>VisaTour Tespit:</b> {reg_time}\n\n"
        f"🔥 <b>HEMEN VFS'YE GİR:</b>\n"
        f"{VFS_LOGIN_URL}"
    )


def hourly_message(now_tr: datetime, cards: list) -> str:
    if cards:
        latest = max(cards, key=lambda card: card["reg_time"])
        latest_text = (
            f"{html.escape(latest['center'])} — "
            f"{html.escape(latest['date_opened'])} "
            f"({html.escape(latest['reg_time'])})"
        )
    else:
        latest_text = "Kayıt yok"

    return (
        "🔄 <b>VFS Bulgaria Saatlik Durum</b>\n\n"
        "🟢 <b>Bot:</b> Aktif (2 dakikada bir taranıyor)\n"
        "🎯 <b>Filtre:</b> Biyometrik (Bireysel & Genel)\n"
        "❌ <b>Son tarama:</b> Yeni kayıt yok\n"
        f"📌 <b>En son görünen kayıt:</b> {latest_text}\n"
        f"🕒 <b>Kontrol:</b> {now_tr.strftime('%d.%m.%Y %H:%M')}\n\n"
        "🔕 <i>Bu saatlik mesaj sessiz gönderildi.</i>"
    )


def hourly_error_message(now_tr: datetime, err_msg: str = "") -> str:
    err_display = html.escape(err_msg) if err_msg else "HTTP 502 / Bağlantı hatası"
    return (
        "🔄 <b>VFS Bulgaria Saatlik Durum</b>\n\n"
        "🟢 <b>Bot:</b> Aktif (2 dakikada bir taranıyor)\n"
        "🎯 <b>Filtre:</b> Biyometrik (Bireysel & Genel)\n"
        f"⚠️ <b>Son kontrol:</b> VisaTour geçici olarak erişilemiyor ({err_display})\n"
        f"🕒 <b>Kontrol:</b> {now_tr.strftime('%d.%m.%Y %H:%M')}\n\n"
        "🔕 <i>Bu saatlik mesaj sessiz gönderildi.</i>"
    )


# =========================================================
# MAIN
# =========================================================

def run():
    now_tr = datetime.now(TZ_TR)

    print("=" * 65)
    print("VFS BULGARIA WATCHER v3.1", now_tr.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 65)

    try:
        state = load_state()
    except Exception as exc:
        print("[Fatal State Error]", repr(exc))
        raise

    # 1. Her iki URL'yi ayrı ayrı çek ve parse et
    try:
        html_ind = fetch_visatour(URL_INDIVIDUAL)
        cards_ind = parse_visatour_cards(html_ind)

        html_bio = fetch_visatour(URL_BIOMETRIC)
        cards_bio = parse_visatour_cards(html_bio)

        # Kartları birleştir ve duplicate ID'leri ayıkla
        all_cards = []
        seen_in_batch = set()
        for card in cards_ind + cards_bio:
            rid = make_record_id(card)
            if rid not in seen_in_batch:
                seen_in_batch.add(rid)
                all_cards.append(card)

        all_cards.sort(key=lambda card: card["reg_time"])

        print(
            f"[Parser] Bireysel: {len(cards_ind)}, "
            f"İkincil Biyometrik: {len(cards_bio)}, "
            f"Toplam: {len(all_cards)}"
        )

        if not all_cards:
            raise RuntimeError("VisaTour açıldı fakat 0 biyometrik kayıt ayrıştırıldı.")

    except Exception as exc:
        print(f"[Fetch/Parse ERROR] {repr(exc)}")
        handle_error(exc, now_tr)
        return

    # Başarılı döngü -> Hata sayacını sıfırla / recovery gönder
    handle_success(now_tr, all_cards)

    # İlk çalıştırma (Sıfır kurulum) kontrolü
    if not state.get("initialized", False):
        print("[INIT] İlk çalıştırma. Mevcut kayıtlar baseline yapılıyor.")
        for card in all_cards:
            rid = make_record_id(card)
            if rid not in state["seen"]:
                state["seen"].append(rid)

        state["initialized"] = True
        state["v3_1_baseline"] = True
        state["version"] = "3.1"
        save_state(state)

        send_telegram(
            f"✅ <b>VFS Bulgaria Watcher v3.1 kuruldu</b>\n\n"
            f"📚 {len(state['seen'])} mevcut kayıt baseline olarak kaydedildi.\n"
            f"🎯 <b>Filtreler:</b>\n"
            f"1️⃣ Application with Biometric (Individual - Bireysel)\n"
            f"2️⃣ Application with Biometric\n\n"
            f"Bundan sonra yalnızca YENİ kayıtlar sesli alarm oluşturacak.",
            silent=True
        )
        return

    # V3.1 Baseline Güncellemesi (İkinci kategorinin mevcut eski kayıtlarını sessizce ekle)
    if not state.get("v3_1_baseline", False):
        print("[INIT V3.1] İkincil biyometrik kayıtlar baseline yapılıyor.")
        new_baseline_count = 0
        for card in all_cards:
            rid = make_record_id(card)
            if rid not in state["seen"]:
                state["seen"].append(rid)
                new_baseline_count += 1

        state["v3_1_baseline"] = True
        state["version"] = "3.1"
        save_state(state)

        print(f"[INIT V3.1] {new_baseline_count} yeni kayıt baseline'a eklendi. Toplam: {len(state['seen'])}")

        send_telegram(
            f"✅ <b>VFS Bulgaria Watcher v3.1 aktif edildi</b>\n\n"
            f"📚 {new_baseline_count} ikincil biyometrik kayıt baseline'a eklendi (toplam: {len(state['seen'])}).\n"
            f"🎯 İki biyometrik kategori de devrede.\n\n"
            f"Bundan sonra yalnızca YENİ kayıtlar sesli alarm oluşturacak.",
            silent=True
        )
        return

    # Normal Tarama: Yeni kayıtları tespit et
    new_records = []
    for card in all_cards:
        rid = make_record_id(card)
        if rid not in state["seen"]:
            new_records.append(card)

    print(f"[Result] Yeni kayıt: {len(new_records)}")

    if new_records:
        for card in new_records:
            rid = make_record_id(card)
            msg = slot_message(card)
            # İKİ KATEGORİ DE SESLİ (silent=False)
            sent = send_telegram(msg, silent=False)
            if sent:
                state["seen"].append(rid)

        save_state(state)
    else:
        print("[State] Değişiklik yok. state.json yazılmadı.")

        # Saat başı sessiz rapor (now_tr.minute < 3)
        outage_state = load_outage_state()
        last_hour = outage_state.get("last_hourly_hour", -1)
        if now_tr.minute < 3 and last_hour != now_tr.hour:
            print(f"[Hourly] Saat başı ({now_tr.hour:02d}:00) sessiz rapor gönderiliyor.")
            send_telegram(
                hourly_message(now_tr, all_cards),
                silent=True
            )
            outage_state["last_hourly_hour"] = now_tr.hour
            save_outage_state(outage_state)

    print("[DONE]")


if __name__ == "__main__":
    run()
