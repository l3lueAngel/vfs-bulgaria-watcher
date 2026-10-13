import os
import time
import re
import html
import requests
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright

TG_BOT_TOKEN = "8918314722:AAEbbb81iUqvP2QU24vnrwDrt4m9TmQ4BIA"
TG_CHAT_ID = "6769707789"

VISATOUR_URL = (
    "https://alerts.visatour.org/"
    "?origin=TUR&destination=Bulgaria"
    "&category=Application+with+Biometric+%28+Individual+-+Bireysel%29"
)

def send_telegram(message, silent=False):
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_notification": silent
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        print(f"[Telegram] Status: {r.status_code} (Silent: {silent})")
    except Exception as e:
        print(f"[Telegram] Error: {e}")

def parse_visatour_cards(page_html):
    card_pattern = re.compile(
        r'<div class="card-surface"[^>]*>.*?'
        r'title="([^"]+)"[^>]*>\s*([^<\r\n]+)\s*</span>.*?'
        r'Earliest Date Opened:</span>\s*<strong[^>]*>([^<]+)</strong>.*?'
        r'Center:</span>\s*<strong[^>]*>([^<]+)</strong>.*?'
        r'<span>(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})</span>',
        re.DOTALL
    )
    cards = []
    for m in card_pattern.finditer(page_html):
        cat = html.unescape(m.group(1).strip())
        date_opened = html.unescape(m.group(3).strip())
        center = html.unescape(m.group(4).strip())
        reg_time = m.group(5).strip()
        if "individual" in cat.lower() or "bireysel" in cat.lower():
            cards.append({
                "category": cat,
                "date_opened": date_opened,
                "center": center,
                "reg_time": reg_time
            })
    return cards

def run_vfs_check():
    print("[*] VFS Bulgaria (Application with Biometric - Individual) Taraması Başlıyor...")

    # Türkiye saatini al (UTC+3)
    tz_tr = timezone(timedelta(hours=3))
    now_tr = datetime.now(tz_tr)
    time_str = now_tr.strftime("%H:%M")

    slot_found = False
    latest_record_info = "Henüz kayıt bulunamadı"

    # 1. AŞAMA: VisaTour Canlı Akış Taraması (Application with Biometric - Individual)
    print("[*] Aşama 1: alerts.visatour.org canlı tablosu kontrol ediliyor...")
    visatour_html = ""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        }
        resp = requests.get(VISATOUR_URL, headers=headers, timeout=15)
        if resp.status_code == 200 and "card-surface" in resp.text:
            visatour_html = resp.text
    except Exception as e:
        print(f"[!] VisaTour HTTP bağlantı uyarısı: {e}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()

        try:
            # Eğer HTTP ile alınamadıysa Playwright ile al
            if not visatour_html:
                print("[*] VisaTour sayfası tarayıcı ile açılıyor...")
                page.goto(VISATOUR_URL, timeout=45000, wait_until="domcontentloaded")
                time.sleep(3)
                visatour_html = page.content()

            cards = parse_visatour_cards(visatour_html)
            print(f"[+] VisaTour üzerinde {len(cards)} adet Bireysel (Individual) kayıt bulundu.")

            if cards:
                top = cards[0]
                latest_record_info = f"{top['center']} — {top['date_opened']} ({top['reg_time']})"
                print(f"[*] En güncel bireysel kayıt: {latest_record_info}")

                # Son 12 dakika içinde yeni düşen kayıt var mı kontrol et
                for c in cards:
                    try:
                        reg_dt = datetime.strptime(c["reg_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz_tr)
                        age_minutes = (now_tr - reg_dt).total_seconds() / 60.0
                        if -2 <= age_minutes <= 12:
                            print(f"[🎉] YENİ BİREYSEL RANDEVU YAKALANDI: {c['center']} -> {c['date_opened']} ({c['reg_time']})")
                            send_telegram(
                                f"🇧🇬 🚨 <b>BULGARIA BİREYSEL RANDEVU AÇILDI!</b> 🚨\n\n"
                                f"📍 <b>Merkez:</b> {c['center']}\n"
                                f"📅 <b>Açılan Tarih / Durum:</b> {c['date_opened']}\n"
                                f"🎯 <b>Kategori:</b> {c['category']}\n"
                                f"🕒 <b>Tespit Zamanı:</b> {c['reg_time']}\n\n"
                                f"👉 <b>Hemen girip randevunuzu alın:</b>\n"
                                f"https://visa.vfsglobal.com/tur/en/bgr/login",
                                silent=False
                            )
                            slot_found = True
                    except Exception as parse_err:
                        print(f"[!] Tarih ayrıştırma hatası: {parse_err}")

            # 2. AŞAMA: VFS Global 12 Merkez Doğrudan Kontrol (Yedek Tarama)
            if not slot_found:
                print("[*] Aşama 2: VFS Global sayfası kontrol ediliyor...")
                page.goto("https://visa.vfsglobal.com/tur/en/bgr", timeout=60000, wait_until="domcontentloaded")
                time.sleep(5)

                selects = page.query_selector_all('select')
                target_select = None
                for s in selects:
                    txt = s.inner_text()
                    if "Ankara" in txt or "Edirne" in txt or "Istanbul" in txt:
                        target_select = s
                        break

                if target_select:
                    options = target_select.query_selector_all('option')
                    valid_options = []
                    for opt in options:
                        val = opt.get_attribute("value")
                        text = opt.inner_text().strip()
                        if val and "choose" not in text.lower():
                            valid_options.append((val, text))

                    print(f"[+] VFS sayfasında {len(valid_options)} merkez kontrol ediliyor...")
                    for idx, (val, text) in enumerate(valid_options, 1):
                        city_name = text.replace("Bulgaria Visa Application Center", "").replace(",", "").strip()
                        target_select.select_option(value=val)
                        target_select.dispatch_event("input")
                        target_select.dispatch_event("change")
                        time.sleep(3.5)

                        body_text = page.inner_text("body")
                        no_slot_words = [
                            "no appointment slots", "no slots available",
                            "currently no slots", "uygun randevu bulunmamaktadır", "randevu bulunamadı"
                        ]
                        has_no_slot = any(w in body_text.lower() for w in no_slot_words)
                        date_match = re.search(r'\b\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{4}\b', body_text)

                        if not has_no_slot and date_match:
                            found_date = date_match.group(0)
                            print(f"[🎉] VFS SAYFASINDA RANDEVU BULUNDU: {city_name} -> {found_date}")
                            send_telegram(
                                f"🇧🇬 🚨 <b>BULGARIA VISA C AÇILDI!</b> 🚨\n\n"
                                f"📍 <b>Şehir:</b> {city_name}\n"
                                f"📅 <b>Tarih:</b> {found_date}\n"
                                f"🎯 <b>Kategori:</b> Application with Biometric (Individual - Bireysel)\n\n"
                                f"👉 <b>Hemen girip randevunuzu alın:</b>\n"
                                f"https://visa.vfsglobal.com/tur/en/bgr/login",
                                silent=False
                            )
                            slot_found = True
                            break

            # 3. AŞAMA: SAATLİK SESSİZ DURUM RAPORU
            if not slot_found:
                print("[*] Tarama bitti: Şu an yeni açılan bireysel randevu yok.")

                # Sadece saat başlarında (:00 ile :09 arası) ve çift mesaj olmaması için workflow_dispatch'te gönder
                is_hourly_slot = (now_tr.minute < 10)
                is_schedule_dup = (os.environ.get("GITHUB_EVENT_NAME") == "schedule")

                if is_hourly_slot and not is_schedule_dup:
                    send_telegram(
                        f"🔄 <b>VFS Bulgaria Saatlik Durum Raporu ({time_str})</b>\n\n"
                        f"⏱️ <b>Durum:</b> Bot 7/24 aktif çalışıyor.\n"
                        f"🎯 <b>Filtre:</b> Application with Biometric (Individual - Bireysel)\n"
                        f"❌ <b>Sonuç:</b> Son 10 dakikada yeni açılan randevu yok.\n"
                        f"📌 <b>En Son Kayıt:</b> {latest_record_info}\n\n"
                        f"🟢 <i>Aralıksız tarama 10 dakikada bir devam ediyor. (Bu mesaj 1 saatlik sessiz rapordur).</i>",
                        silent=True
                    )

        except Exception as e:
            print(f"[!] Hata: {e}")
        finally:
            browser.close()

if __name__ == "__main__":
    run_vfs_check()
