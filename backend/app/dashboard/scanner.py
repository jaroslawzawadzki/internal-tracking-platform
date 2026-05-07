from playwright.sync_api import sync_playwright
import base64
import time
from dashboard.models import TrackerLibrary
import os 
os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"

# Słownik wiedzy (bez zmian)
REPORT_KNOWLEDGE = {
    "https": {
        "pl": "Witryna używa HTTPS jako standardu. Szyfruje to niemal wszystkie informacje przesyłane między klientem a serwisem.",
        "de": "Die Website verwendet HTTPS als Voreinstellung. Dies verschlüsselt nahezu alle Informationen, die zwischen Client und Webservice."
    },
    "hsts": {
        "pl": "HSTS wymusza na przeglądarce bezpieczne połączenie. Zapobiega to próbom obniżenia protokołu do zwykłego HTTP.",
        "de": "HSTS stellt sicher, dass der Browser die Verbindung zu einem Webauftritt immer über HTTPS herstellt."
    },
    "csp": {
        "pl": "CSP to dodatkowa warstwa bezpieczeństwa chroniąca przed atakami XSS (wstrzykiwanie złośliwych skryptów).",
        "de": "Content Security Policy (CSP) hilft, bestimmte Arten von Angriffen wie Cross-Site-Scripting (XSS) zu verhindern."
    },
    "referrer": {
        "pl": "Polityka Referrer określa, ile informacji o Twojej stronie jest wysyłanych do innych witryn podczas kliknięcia w link.",
        "de": "Durch die Festlegung einer Referrer-Richtlinie können Websites den Browsern mitteilen, wie viele Informationen weitergegeben werden."
    }
}

def get_tracker_info(identifier):
    tracker = TrackerLibrary.objects.filter(domain=identifier).first()
    if tracker: return tracker
    if "_" in identifier:
        temp_id = identifier
        while "_" in temp_id:
            temp_id = "_".join(temp_id.split("_")[:-1])
            if not temp_id: break
            tracker = TrackerLibrary.objects.filter(domain=temp_id).first()
            if tracker: return tracker
    parts = identifier.split('.')
    if len(parts) > 1:
        while len(parts) > 1:
            parts.pop(0)
            root_domain = ".".join(parts)
            tracker = TrackerLibrary.objects.filter(domain=root_domain).first()
            if tracker: return tracker
    return None

def get_refined_category(db_info, cookie_name):
    name_lower = db_info.name.lower() if db_info.name else ""
    c_name_lower = cookie_name.lower()
    cat_raw = db_info.category if db_info.category else "Cookie"
    category_map = {
        'Advertising': {'pl': 'Marketingowe', 'de': 'Werbung / Marketing'},
        'Analytics': {'pl': 'Statystyczne', 'de': 'Analyse / Statistik'},
        'Fingerprinting': {'pl': 'Śledzące', 'de': 'Fingerprinting / Tracking'},
        'Social': {'pl': 'Społecznościowe', 'de': 'Social Media'},
        'Cookie': {'pl': 'Funkcjonalne', 'de': 'Funktionell'},
        'Content': {'pl': 'Treści zewnętrzne', 'de': 'Externe Inhalte'}
    }
    marketing_keywords = ['gcl', 'adsense', 'ad-delivery', 'doubleclick', 'facebook', 'meta', 'bing', 'advertising', 'remarketing']
    if any(key in name_lower or key in c_name_lower for key in marketing_keywords): return category_map['Advertising']
    stat_keywords = ['analytics', 'hotjar', 'matomo', 'piwik', 'measure']
    if any(key in name_lower for key in stat_keywords) or c_name_lower.startswith('_ga'): return category_map['Analytics']
    return category_map.get(cat_raw, category_map['Cookie'])

def test_scan(url):
    results = {
        "title": "",
        "url": url,
        "timestamp": time.strftime('%Y-%m-%d %H:%M:%S'),
        "knowledge": REPORT_KNOWLEDGE,
        "https": url.startswith("https"),
        "hsts": {"enabled": False, "max_age": None},
        "cookies_count": 0,
        "detailed_cookies": [],
        "trackers": [],
        "network_details": [], # <--- TUTAJ BĘDĄ NOWE DANE (Payload/Response)
        "security_headers": {},
        "screenshot": "",
        "screenshot_2": ""
    }

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
            page = context.new_page()
            
            # --- KROK 1: PRZECHWYTYWANIE SZCZEGÓŁÓW SIECI ---
            found_domains = set()

            def handle_request(request):
                try:
                    # Logujemy tylko istotne typy (dane i skrypty)
                    if request.resource_type in ["fetch", "xhr", "script"]:
                        domain = request.url.split('/')[2] if len(request.url.split('/')) > 2 else "Unknown"
                        
                        req_data = {
                            "url": request.url,
                            "method": request.method,
                            "type": request.resource_type,
                            "payload": request.post_data if request.post_data else "Brak danych",
                            "status": "In progress...", # Zaktualizuje to handle_response
                        }
                        results["network_details"].append(req_data)
                        found_domains.add(domain)
                except: pass

            def handle_response(response):
                try:
                    # Aktualizujemy status kodu (np. 200, 404) dla pasującego URL
                    for item in results["network_details"]:
                        if item["url"] == response.url:
                            item["status"] = response.status
                except: pass

            page.on("request", handle_request)
            page.on("response", handle_response)
            # --- KONIEC KROKU 1 ---

            response = page.goto(url, timeout=30000, wait_until="networkidle")
            results["title"] = page.title()
            page.wait_for_timeout(5000) 

            # 2. ANALIZA NAGŁÓWKÓW
            if response:
                headers = response.headers
                hsts = headers.get('strict-transport-security', '')
                if hsts:
                    results["hsts"]["enabled"] = True
                    results["hsts"]["max_age"] = hsts
                interesting = {'csp': 'content-security-policy', 'xfo': 'x-frame-options', 'referrer': 'referrer-policy'}
                results["security_headers"] = {k: headers.get(v, "Brak") for k, v in interesting.items()}

            # 3. ANALIZA TRACKERÓW (Domeny zewnętrzne)
            main_domain = url.split('/')[2].replace('www.', '')
            formatted_trackers = []
            for d in found_domains:
                if main_domain not in d and len(d.split('.')) > 1:
                    db_info = get_tracker_info(d)
                    if db_info:
                        formatted_trackers.append(f"{d} ({db_info.name})")
                    else:
                        formatted_trackers.append(f"{d} (Nieznany serwis)")
            results["trackers"] = sorted(list(set(formatted_trackers)))

            # 4. ANALIZA CIASTECZEK
            all_cookies = context.cookies()
            results["cookies_count"] = len(all_cookies)
            for c in all_cookies:
                db_info = get_tracker_info(c['name']) or get_tracker_info(c['domain'].strip('.'))
                cookie_data = {
                    'name': c['name'], 'domain': c['domain'],
                    'secure': c.get('secure', False), 'httpOnly': c.get('httpOnly', False),
                    'expires': time.strftime('%Y-%m-%d', time.localtime(c['expires'])) if c.get('expires', 0) > 0 else 'Sesja'
                }
                if db_info:
                    cat_obj = get_refined_category(db_info, c['name'])
                    cookie_data.update({'friendly_name': db_info.name, 'category_pl': cat_obj['pl'], 'category_de': cat_obj['de'], 'description': db_info.description_pl})
                else:
                    cookie_data.update({'friendly_name': 'Własne witryny / Eigene', 'category_pl': 'Niezbędne', 'category_de': 'Notwendig', 'description': 'Ciasteczko techniczne'})
                results["detailed_cookies"].append(cookie_data)

            # 5. SCREENSHOTY (Oryginał i Rentgen)
            try:
                screenshot_bytes = page.screenshot(full_page=False)
                results["screenshot"] = base64.b64encode(screenshot_bytes).decode('utf-8')
                cookie_preview = "\\n".join([f"🍪 {c['name']} | {c['domain']}" for c in all_cookies[:15]])
                overlay_script = f"""
                    (function() {{
                        const container = document.createElement('div');
                        container.style.cssText = "all: initial; position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; z-index: 2147483647; font-family: monospace; pointer-events: none;";
                        const backdrop = document.createElement('div');
                        backdrop.style.cssText = "position: absolute; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.85);";
                        container.appendChild(backdrop);
                        const panel = document.createElement('div');
                        panel.style.cssText = "position: absolute; top: 10%; left: 10%; width: 80%; background: #000; color: #00ff00; padding: 30px; border: 3px solid #00ff00; border-radius: 15px; box-shadow: 0 0 50px #00ff00; pointer-events: auto;";
                        panel.innerHTML = `
                            <h1 style="color:#00ff00; margin-top:0; font-size:24px;">🔍 ONDUA DEEP SCAN ACTIVE</h1>
                            <p style="color:#fff; border-bottom:1px dashed #00ff00; padding-bottom:10px;">Detected {len(all_cookies)} cookies in pre-consent phase:</p>
                            <pre style="white-space:pre-wrap; color:#00ff00; font-size:14px; margin-top:15px; background:rgba(0,255,0,0.05); padding:10px;">{cookie_preview}</pre>
                            <p style="color:#ff0000; font-weight:bold; margin-top:20px; font-size:18px;">[!] PRIVACY VIOLATION DETECTED</p>
                        `;
                        container.appendChild(panel);
                        document.documentElement.appendChild(container);
                    }})();
                """
                page.evaluate(overlay_script)
                page.wait_for_timeout(500)
                screenshot_bytes_2 = page.screenshot(full_page=False)
                results["screenshot_2"] = base64.b64encode(screenshot_bytes_2).decode('utf-8')
            except: pass

            browser.close()
            return results
        except Exception as e:
            if 'browser' in locals(): browser.close()
            return {"error": str(e)}