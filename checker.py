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

VISATOUR_URL = (
    "https://alerts.visatour.org/"
    "?origin=TUR&destination=Bulgaria"
    "&category=Application+with+Biometric+%28+Individual+-+Bireysel%29"
)

VFS_LOGIN_URL = "https://visa.vfsglobal.com/tur/en/bgr/login"

STATE_FILE = Path("state.json")

# GitHub Secrets tanımlı değilse boş string ("") döneceği için `or` ile fallback kullanılır.
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

    silent=False -> gerçek slot alarmı
    silent=True  -> durum/hata mesajları
    """

    if (
        not TG_BOT_TOKEN
        or TG_BOT_TOKEN == "BURAYA_BOT_TOKEN"
        or not TG_CHAT_ID
        or TG_CHAT_ID == "BURAYA_CHAT_ID"
    ):
        print("[!] Telegram token/chat ID ayarlanmamış.")
        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{TG_BOT_TOKEN}/sendMessage"
    )

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

        print(
            f"[Telegram] HTTP {response.status_code} "
            f"(silent={silent})"
        )

        if not response.ok:
            print(
                "[Telegram ERROR]",
                response.text[:500]
            )

        return response.ok

    except Exception as exc:
        print(
            "[Telegram Exception]",
            repr(exc)
        )
        return False


# =========================================================
# STATE
# =========================================================

def default_state():
    return {
        "version": 3,
        "initialized": False,
        "seen": []
    }


def load_state():
    if not STATE_FILE.exists():
        print("[State] state.json yok; ilk kurulum.")
        return default_state()

    try:
        with STATE_FILE.open(
            "r",
            encoding="utf-8"
        ) as f:
            data = json.load(f)

        if not isinstance(data, dict):
            raise ValueError(
                "state.json root object değil."
            )

        data.setdefault("version", 3)
        data.setdefault("initialized", False)
        data.setdefault("seen", [])

        if not isinstance(data["seen"], list):
            raise ValueError(
                "state.json seen list değil."
            )

        return data

    except Exception as exc:
        print(
            "[State ERROR]",
            repr(exc)
        )
        raise


def save_state(state):
    """
    Sadece gerçekten state değiştiğinde çağrılır.
    """

    state["version"] = 3
    state["seen"] = state["seen"][-MAX_SEEN_RECORDS:]

    temp_file = STATE_FILE.with_suffix(".tmp")

    with temp_file.open(
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2,
            sort_keys=True
        )
        f.write("\n")

    temp_file.replace(STATE_FILE)

    print(
        f"[State] Kaydedildi. "
        f"Seen={len(state['seen'])}"
    )


# =========================================================
# RECORD ID
# =========================================================

def make_record_id(card):
    """
    Aynı VisaTour kaydının her çalışmada aynı ID'yi
    üretmesini sağlar.
    """

    raw = "|".join([
        card["category"].strip(),
        card["center"].strip(),
        card["date_opened"].strip(),
        card["reg_time"].strip(),
    ])

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


# =========================================================
# VISATOUR
# =========================================================

def fetch_visatour():
    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": (
            "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7"
        ),
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }

    response = requests.get(
        VISATOUR_URL,
        headers=headers,
        timeout=REQUEST_TIMEOUT
    )

    print(
        f"[VisaTour] HTTP {response.status_code} "
        f"/ {len(response.text)} bytes"
    )

    response.raise_for_status()

    if not response.text.strip():
        raise RuntimeError(
            "VisaTour boş HTML döndürdü."
        )

    return response.text


# =========================================================
# PARSER
# =========================================================

def parse_visatour_cards(page_html):
    card_pattern = re.compile(
        r'<div class="card-surface"[^>]*>.*?'
        r'title="([^"]+)"[^>]*>\s*'
        r'([^<\r\n]+)\s*</span>.*?'
        r'Earliest Date Opened:</span>\s*'
        r'<strong[^>]*>([^<]+)</strong>.*?'
        r'Center:</span>\s*'
        r'<strong[^>]*>([^<]+)</strong>.*?'
        r'<span>'
        r'(\d{4}-\d{2}-\d{2}\s+'
        r'\d{2}:\d{2}:\d{2})'
        r'</span>',
        re.DOTALL | re.IGNORECASE
    )

    cards = []

    for match in card_pattern.finditer(page_html):
        category = html.unescape(
            match.group(1).strip()
        )

        date_opened = html.unescape(
            match.group(3).strip()
        )

        center = html.unescape(
            match.group(4).strip()
        )

        reg_time = match.group(5).strip()

        category_lower = category.lower()

        if (
            "individual" not in category_lower
            and "bireysel" not in category_lower
        ):
            continue

        cards.append({
            "category": category,
            "date_opened": date_opened,
            "center": center,
            "reg_time": reg_time,
        })

    cards.sort(
        key=lambda card: card["reg_time"]
    )

    return cards


# =========================================================
# MESSAGE BUILDERS
# =========================================================

def slot_message(card):
    center = html.escape(card["center"])
    opened = html.escape(card["date_opened"])
    category = html.escape(card["category"])
    reg_time = html.escape(card["reg_time"])

    return (
        "🇧🇬 🚨 <b>BULGARİSTAN BİREYSEL "
        "RANDEVU HAREKETİ!</b> 🚨\n\n"

        f"📍 <b>Merkez:</b> {center}\n"
        f"📅 <b>Açılan Tarih / Durum:</b> {opened}\n"
        f"🎯 <b>Kategori:</b> {category}\n"
        f"🕒 <b>VisaTour Tespit:</b> {reg_time}\n\n"

        "🔥 <b>HEMEN VFS'YE GİR:</b>\n"
        f"{VFS_LOGIN_URL}"
    )


def hourly_message(now_tr, cards):
    if cards:
        latest = max(
            cards,
            key=lambda card: card["reg_time"]
        )

        latest_text = (
            f"{html.escape(latest['center'])} — "
            f"{html.escape(latest['date_opened'])} "
            f"({html.escape(latest['reg_time'])})"
        )
    else:
        latest_text = "Kayıt okunamadı"

    return (
        "🔄 <b>VFS Bulgaria Saatlik Durum</b>\n\n"

        "🟢 <b>Bot:</b> Aktif\n"
        "🎯 <b>Filtre:</b> "
        "Application with Biometric "
        "(Individual - Bireysel)\n"

        "❌ <b>Son tarama:</b> "
        "Yeni kayıt yok\n"

        f"📌 <b>En son görünen kayıt:</b> "
        f"{latest_text}\n"

        f"🕒 <b>Kontrol:</b> "
        f"{now_tr.strftime('%d.%m.%Y %H:%M')}\n\n"

        "🔕 <i>Bu saatlik mesaj sessiz gönderildi.</i>"
    )


# =========================================================
# MAIN
# =========================================================

def run():
    now_tr = datetime.now(TZ_TR)

    print("=" * 65)
    print(
        "VFS BULGARIA WATCHER v3",
        now_tr.strftime("%Y-%m-%d %H:%M:%S")
    )
    print("=" * 65)

    try:
        state = load_state()

    except Exception as exc:
        send_telegram(
            "⚠️ <b>VFS Watcher teknik uyarı</b>\n\n"
            "state.json okunamadı. "
            "Yanlış randevu alarmı üretmemek için "
            "bu tarama durduruldu.\n\n"
            f"<code>{html.escape(str(exc))}</code>",
            silent=True
        )
        raise

    try:
        page_html = fetch_visatour()

    except Exception as exc:
        print(
            "[VisaTour ERROR]",
            repr(exc)
        )

        send_telegram(
            "⚠️ <b>VFS Watcher teknik uyarı</b>\n\n"
            "VisaTour bu taramada okunamadı.\n"
            "Bir sonraki çalışmada tekrar denenecek.\n\n"
            f"<code>{html.escape(str(exc))}</code>",
            silent=True
        )
        raise

    cards = parse_visatour_cards(
        page_html
    )

    print(
        f"[Parser] {len(cards)} "
        "Bireysel kayıt bulundu."
    )

    if not cards:
        send_telegram(
            "⚠️ <b>VFS Watcher parser uyarısı</b>\n\n"
            "VisaTour açıldı fakat "
            "<b>0 Bireysel kayıt</b> ayrıştırıldı.\n"
            "HTML yapısı değişmiş olabilir.\n\n"
            "🔕 Bu teknik uyarıdır; "
            "randevu alarmı değildir.",
            silent=True
        )
        return

    # -----------------------------------------------------
    # FIRST RUN / BASELINE
    # -----------------------------------------------------

    if not state["initialized"]:
        print(
            "[INIT] İlk çalıştırma. "
            "Mevcut kayıtlar baseline yapılıyor."
        )

        baseline = []

        for card in cards:
            baseline.append(
                make_record_id(card)
            )

        state["initialized"] = True
        state["seen"] = baseline

        save_state(state)

        send_telegram(
            "✅ <b>VFS Bulgaria Watcher v3 hazır</b>\n\n"
            f"📚 {len(cards)} mevcut kayıt "
            "baseline olarak kaydedildi.\n"

            "🎯 Application with Biometric "
            "(Individual - Bireysel)\n\n"

            "Bundan sonra yalnızca "
            "<b>yeni kayıtlar</b> "
            "sesli alarm oluşturacak.",
            silent=True
        )

        return

    # -----------------------------------------------------
    # FIND NEW RECORDS
    # -----------------------------------------------------

    seen = set(
        state["seen"]
    )

    new_cards = []

    for card in cards:
        record_id = make_record_id(card)

        if record_id not in seen:
            new_cards.append(
                (record_id, card)
            )

    print(
        f"[Result] Yeni kayıt: "
        f"{len(new_cards)}"
    )

    # -----------------------------------------------------
    # NEW SLOT
    # -----------------------------------------------------

    state_changed = False

    for record_id, card in new_cards:
        print(
            "[NEW]",
            card["center"],
            card["date_opened"],
            card["reg_time"]
        )

        delivered = send_telegram(
            slot_message(card),
            silent=False
        )

        if delivered:
            seen.add(record_id)
            state_changed = True
        else:
            print(
                "[!] Telegram teslim edemedi. "
                "Kayıt seen yapılmadı."
            )

    # -----------------------------------------------------
    # SAVE ONLY IF CHANGED
    # -----------------------------------------------------

    if state_changed:
        state["seen"] = list(seen)
        save_state(state)
    else:
        print(
            "[State] Değişiklik yok. "
            "state.json yazılmadı."
        )

    # -----------------------------------------------------
    # HOURLY SILENT REPORT
    # -----------------------------------------------------

    # Saat başındaki çalışmada (:00 - :09 arası ilk tetikleme) sessiz rapor gönder.
    # Not: cron-job.org 10 dk veya 2 dk ayarlı olsa da saat başında çalışır.
    if (
        not new_cards
        and now_tr.minute < 2
    ):
        print(
            "[Hourly] Sessiz durum raporu."
        )

        send_telegram(
            hourly_message(
                now_tr,
                cards
            ),
            silent=True
        )

    print("[DONE]")


if __name__ == "__main__":
    run()
