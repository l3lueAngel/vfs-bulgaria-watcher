import os
import time
import re
import requests
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright

TG_BOT_TOKEN = "8918314722:AAEbbb81iUqvP2QU24vnrwDrt4m9TmQ4BIA"
TG_CHAT_ID = "6769707789"

def send_telegram(message, silent=False):
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_notification": silent  # True ise sessiz iletilir, ses veya titreşim yapmaz
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        print(f"[Telegram] Status: {r.status_code} (Silent: {silent})")
    except Exception as e:
        print(f"[Telegram] Error: {e}")

def run_vfs_check():
    print("[*] VFS Bulgaria 12-Centres Bulut Taraması Başlıyor...")
    
    # Türkiye saatini al (UTC+3)
    tz_tr = timezone(timedelta(hours=3))
    now_tr = datetime.now(tz_tr)
    time_str = now_tr.strftime("%H:%M")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()

        try:
            print("[*] VFS sayfasına bağlanılıyor...")
            page.goto("https://visa.vfsglobal.com/tur/en/bgr", timeout=60000, wait_until="domcontentloaded")
            time.sleep(5)

            # 12 Merkezin olduğu açılır kutuyu bul
            page.wait_for_selector('select', timeout=20000)
            selects = page.query_selector_all('select')
            target_select = None
            for s in selects:
                txt = s.inner_text()
                if "Ankara" in txt or "Edirne" in txt or "Istanbul" in txt:
                    target_select = s
                    break

            if not target_select:
                print("[-] 12 merkez kutusu bulunamadı.")
                return

            options = target_select.query_selector_all('option')
            valid_options = []
            for opt in options:
                val = opt.get_attribute("value")
                text = opt.inner_text().strip()
                if val and "choose" not in text.lower():
                    valid_options.append((val, text))

            print(f"[+] {len(valid_options)} Merkez bulundu!")

            slot_found = False

            # 12 Şehri tek tek sorgula
            for idx, (val, text) in enumerate(valid_options, 1):
                city_name = text.replace("Bulgaria Visa Application Center", "").replace(",", "").strip()
                print(f"[{idx}/{len(valid_options)}] Kontrol ediliyor: {city_name}...")

                target_select.select_option(value=val)
                target_select.dispatch_event("input")
                target_select.dispatch_event("change")

                time.sleep(4.5)

                body_text = page.inner_text("body")
                no_slot_words = [
                    "no appointment slots", "no slots available", 
                    "currently no slots", "uygun randevu bulunmamaktadır", "randevu bulunamadı"
                ]
                has_no_slot = any(w in body_text.lower() for w in no_slot_words)
                date_match = re.search(r'\b\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{4}\b', body_text)

                if not has_no_slot and date_match:
                    found_date = date_match.group(0)
                    print(f"[🎉] RANDEVU BULUNDU: {city_name} -> {found_date}")
                    # GERÇEK RANDEVU: SESLİ VE ACİL BİLDİRİM (silent=False)
                    send_telegram(
                        f"🇧🇬 🚨 <b>BULGARIA VISA C AÇILDI!</b> 🚨\n\n"
                        f"📍 <b>Şehir:</b> {city_name}\n"
                        f"📅 <b>Tarih:</b> {found_date}\n"
                        f"🎯 <b>Vize:</b> Short Stay Type C\n\n"
                        f"👉 <b>Hemen girip randevunuzu alın:</b>\n"
                        f"https://visa.vfsglobal.com/tur/en/bgr",
                        silent=False
                    )
                    slot_found = True
                    break
                else:
                    print(f"[-] {city_name}: Randevu yok.")

            if not slot_found:
                print("[*] Tarama bitti: 12 merkez şu an dolu.")

                # HER 1 SAATTE BİR SESSİZ DURUM RAPORU (Sadece saat başlarında: 00..14 arası çalışmalarda)
                is_hourly_slot = (now_tr.minute >= 0 and now_tr.minute < 15)
                is_manual = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"

                if is_hourly_slot or is_manual:
                    send_telegram(
                        f"🔄 <b>VFS Bulgaria Saatlik Durum Raporu ({time_str})</b>\n\n"
                        f"⏱️ <b>Durum:</b> Bot bulutta 7/24 aktif çalışıyor.\n"
                        f"📊 <b>Kontrol:</b> 12 merkez tarandı.\n"
                        f"❌ <b>Sonuç:</b> Henüz açık randevu yok.\n"
                        f"🟢 <i>Aralıksız tarama devam ediyor. Bu mesaj 1 saatlik sessiz rapordur.</i>",
                        silent=True
                    )

        except Exception as e:
            print(f"[!] Hata: {e}")
        finally:
            browser.close()

if __name__ == "__main__":
    run_vfs_check()
