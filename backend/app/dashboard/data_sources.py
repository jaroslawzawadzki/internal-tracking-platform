from collections import defaultdict
import re
from dashboard.models import SstMetric
from datetime import date
from django.utils import timezone
from datetime import timedelta
from django.db.models import F, Sum, Max, Case, When, IntegerField, Q ,CharField,FloatField
from django.db.models.functions import Greatest,Cast
import pandas as pd
import requests
import io
import math


NOT_AVAILABLE = "Not available"

PURCHASE_REGEX = r"(purchase|kauf|bestellung|order|transaction|checkout|ecommerce|sale|shopping app)"

# Ads/Sheet: bierzemy all_conversions jeśli jest, w przeciwnym razie conversions
ADS_VALUE_EXPR = Greatest(F("all_conversions"), F("conversions"))


# słowa-klucze dla "classic purchase"
CLASSIC_KEYWORDS = ["purchase", "kauf", "bestellung"]


def normalize(text: str) -> str:
    """
    Normalizuje nazwę konwersji:
    - lowercase
    - zamienia '_' i '-' na spacje
    - usuwa wielokrotne spacje
    """
    if not text:
        return ""
    text = text.lower()
    text = text.replace("_", " ")
    text = text.replace("-", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def safe_int(val):
    """Bezpiecznie zamienia wartość na int, obsługując None i NaN."""
    if val is None:
        return 0
    try:
        # Jeśli val to NaN (float), math.isnan zwróci True
        if isinstance(val, float) and math.isnan(val):
            return 0
        return int(val)
    except (ValueError, TypeError):
        return 0

def load_sst_rows(limit: int = 20):
    """
    Raw view (debug) – zostawiamy, bo czasem się przydaje.
    Pokazuje ostatnie rekordy z DB.
    """
    qs = (
        SstMetric.objects
        .order_by("-report_date", "property_id")[:limit]
        .values("property_id", "report_date", "conversions", "conversion_action_name")
    )

    rows = []
    for r in qs:
        rows.append({
            "Client": r["property_id"],
            "Date": r["report_date"],
            "Conversions": r["conversions"],
            "Action": r.get("conversion_action_name"),
            "Status": "OK" if (r["conversions"] or 0) > 0 else "WARN",
        })

    return rows


def load_monitoring_rows(days=30):# 1. Zmieniamy argument z monitoring_date na days
    """
    Monitoring view: 1 klient = 1 wiersz = 1 dzień

    Sloty:
      - Classic Purchase: (bez SST) + purchase/kauf/bestellung
      - SST Purchase: (SST w nazwie) + purchase/kauf/bestellung
      - GA4 Purchase: (GA4 w nazwie) + purchase

    Zwraca:
      rows: lista dictów gotowych pod template (columns + row|get_item)
      monitoring_date: data, którą monitorujemy (ostatni dzień w DB, jeśli nie podana)
    """


    # 1) Wyliczamy zakres dat na podstawie parametru days
    today = timezone.now().date()
    start_date = today - timedelta(days=int(days))
    
    # Do wyświetlenia w nagłówku jako "data monitoringu"
    display_date = f"{start_date} - {today}"

    # 2) Bierzemy rekordy z ZAKRESU dat (używamy __range)
    # TUTAJ ZMIANA: filter(report_date__range=...) zamiast (report_date=...)
    base = SstMetric.objects.filter(
        report_date__range=[start_date, today]
    ).only(
        "property_id", 
        "conversions", 
        "conversion_action_name",
        "conversion_action_category",
        "external_conversion_source"
    )

    if not base.exists():
        return [], display_date

    # 3) Mapy per klient (reszta logiki zostaje bez zmian, 
    # bo pętla 'for row in base' teraz po prostu przemieli więcej dni)
    sst_map = defaultdict(float)
    classic_map = defaultdict(float)
    ga4_map = defaultdict(float)
    fast_map = defaultdict(float)
    stape_p_map = defaultdict(float)
    stape_e_map = defaultdict(float)

    for row in base:
        pid = row.property_id
        val = float(row.conversions or 0)
        name = normalize(getattr(row, "conversion_action_name", ""))
        cat = normalize(getattr(row, "conversion_action_category", ""))
        src = (getattr(row, "external_conversion_source", "") or "").lower()

        # --- NAPRAWA BŁĘDU: Nie używamy row.fast_value, bo go nie ma! ---
        # Liczymy FAST na podstawie źródła (jeśli w źródle jest gclid/fast)
        if "fast" in src or "gclid" in src:
            fast_map[pid] += val
        
        # STAPE Events to po prostu suma wszystkich rekordów dla klienta
        stape_e_map[pid] += val

        # Jeśli to zakup, sumujemy do STAPE Purchase
        if cat == "purchase":
            stape_p_map[pid] += val

        # --- Logika klasyfikacji zakupów (Twoja, zostaje) ---
        if cat != "purchase":
            continue

        if "sst" in name:
            sst_map[pid] += val 
            continue

        if "analytics" in src or "unknown" in src or "ga4" in name:
            ga4_map[pid] += val
            continue

        if any(k in name for k in CLASSIC_KEYWORDS):
            classic_map[pid] += val
            continue

    # 4) Union klientów i budowanie wierszy
    all_clients = set(stape_e_map.keys())

    rows = []
    for pid in sorted(all_clients):
        classic_val = classic_map.get(pid, 0)
        sst_val = sst_map.get(pid, 0)
        ga4_val = ga4_map.get(pid, 0)
        fast_val = fast_map.get(pid, 0)
        stape_p_val = stape_p_map.get(pid, 0)
        stape_e_val = stape_e_map.get(pid, 0)

        classic_ok = classic_val > 0
        sst_ok = sst_val > 0
        
        # Twoja logika statusu
        if sst_ok and classic_ok:
            status = "OK"
        elif sst_ok and not classic_ok:
            status = "UNIDENTIFIED"
        else:
            status = "FAIL"
# Przed pętlą for pid... stwórz mapę nazw
    names_map = dict(SstMetric.objects.exclude(extra_json__client_name__isnull=True).values_list("property_id", "extra_json__client_name").distinct())

    # Wewnątrz pętli:
    rows.append({
        "Client": names_map.get(pid, pid),  # Tutaj naprawiamy nazwę!
        "Ads ID": pid,
        "Date": f"{start_date.strftime('%d.%m')} - {today.strftime('%d.%m')}",
        "Classic Purchase": ("✅" if classic_ok else "❌") + f" ({int(classic_val)})",
        "GA4 Purchase": ("✅" if ga4_val > 0 else "❌") + f" ({int(ga4_val)})",
        "SST Purchase": ("✅" if sst_ok else "❌") + f" ({int(sst_val)})",
        "FAST": ("✅" if fast_val > 0 else "⚠️") + f" ({int(fast_val)})",
        "STAPE Purchase": f"✅ {int(stape_p_val)}",
        "STAPE Events": f"✅ {int(stape_e_val)}",
        "Status": status,
        "Alert_Message": "Persistent SST efficiency gap" if status == "FAIL" else "Stable",
        "Health_7d_color": "green" if sst_ok else "red",
        "Health_14d_color": "green" if sst_ok else "red",
        "Health_30d_color": "green" if sst_ok else "red",
    })

    return rows, display_date

def safe_int(val):
    """Absolutnie bezpieczna konwersja na int."""
    if val is None:
        return 0
    try:
        # Jeśli val to NaN (float), math.isnan zwróci True
        if isinstance(val, (float, int)) and math.isinf(val):
            return 0
        if isinstance(val, float) and math.isnan(val):
            return 0
        return int(float(val)) # float(val) na wypadek gdyby val było stringiem
    except (ValueError, TypeError):
        return 0

def load_stape_overview_rows():
    qs = (
        SstMetric.objects
        .values("property_id")
        .annotate(
            client_name=Max(
                Cast("extra_json__client_name", output_field=CharField())
            ),
            purchase=Sum(
                Case(
                    When(conversion_action_name="purchase", then="conversions"),
                    default=0,
                    output_field=FloatField(), # Zmieniamy na FloatField, bo Sum może dać None/NaN
                )
            ),
            events_total=Sum("conversions"),
        )
        .order_by("property_id")
    )

    rows = []
    for r in qs:
        # Tutaj kluczowa poprawka:
        rows.append({
            "client": r.get("client_name") or r.get("property_id") or "UNKNOWN",
            "ads_id": r.get("property_id"),
            "purchase": safe_int(r.get("purchase")),
            "events_total": safe_int(r.get("events_total")),
        })
    return rows


def build_ads_query_payload(days=None): # <-- Dodajemy argument days
    """
    Zbiera unikalne Ads ID (property_id) z DB i buduje:
    - listę do Excela (1 na linię)
    - SQL (dynamiczny zakres dat)
    """

    # 1) unikalne Ads ID z bazy (bez zmian)
    ids = list(
        SstMetric.objects
        .exclude(property_id__isnull=True)
        .exclude(property_id="")
        .values_list("property_id", flat=True)
        .distinct()
        .order_by("property_id")
    )

    # 2) Okres: DYNAMICZNY zamiast sztywnego początku roku
    today = timezone.now().date()
    
    if days:
        # Jeśli kliknięto przycisk (7, 14, 30...), licz od dzisiaj wstecz
        start_date = today - timedelta(days=int(days))
    else:
        # Domyślnie (jeśli nie podano dni) start od początku roku
        start_date = date(today.year, 1, 1)

    # 3) formaty
    ids_lines = "\n".join(ids)
    ids_sql = ", ".join([f'"{x}"' for x in ids])

    # BigQuery SQL - teraz report_date używa start_date zamiast start_of_year
    bq_sql = f"""
SELECT *
FROM `external-customer-reporting.smk_internal_tracking.gads_conversion_metrics`
WHERE property_id IN ({ids_sql})
  AND report_date >= DATE("{start_date.isoformat()}")
""".strip()

    return {
        "ids": ids,
        "ids_lines": ids_lines,
        "start_date": start_date.isoformat(), # Zmieniliśmy nazwę klucza dla jasności
        "bq_sql": bq_sql,
    }

def load_overview_rows(start_date, end_date):
   
    rows = []
    
    # --- DATY DLA HEALTH CHECK (zawsze liczone od dzisiaj) ---
    today = timezone.now().date()
    d7 = today - timedelta(days=7)
    d14 = today - timedelta(days=14)
    d30 = today - timedelta(days=30)

    ads_ids = (
        SstMetric.objects
        .exclude(property_id__isnull=True)
        .exclude(property_id="")
        .values_list("property_id", flat=True)
        .distinct()
        .order_by("property_id")
    )

    for ads_id in ads_ids:
        # 1. Client name
        client_name = (
            SstMetric.objects
            .filter(property_id=ads_id)
            .exclude(extra_json__client_name__isnull=True)
            .values_list("extra_json__client_name", flat=True)
            .first()
        )

        # 2. BAZA DANYCH (Filtrowana kalendarzem dla głównego widoku)
        ads_base = SstMetric.objects.filter(
            property_id=ads_id,
            report_date__gte=start_date,
            report_date__lte=end_date,
            extra_json__container_id__isnull=True,
        )

        stape_base = SstMetric.objects.filter(
            property_id=ads_id,
            report_date__gte=start_date,
            report_date__lte=end_date,
        ).exclude(extra_json__container_id__isnull=True)

        # --- GŁÓWNE WARTOŚCI (Zależne od wybranego zakresu w UI) ---
        fast_qs = ads_base.filter(conversion_action_category__icontains="purchase", external_conversion_source__icontains="upload", conversion_action_name__icontains="gclid")
        fast_value = pick_max_action_value(fast_qs, ADS_VALUE_EXPR)

        sst_qs = ads_base.filter(conversion_action_category__icontains="purchase", external_conversion_source__icontains="webpage", conversion_action_name__icontains="sst")
        sst_value = pick_max_action_value(sst_qs, ADS_VALUE_EXPR)

        ga4_qs = ads_base.filter(conversion_action_category__icontains="purchase").filter(Q(conversion_action_name__icontains="ga4") | Q(external_conversion_source__icontains="unknown")).exclude(conversion_action_name__icontains="sst")
        ga4_value = pick_max_action_value(ga4_qs, ADS_VALUE_EXPR)

        classic_qs = ads_base.filter(conversion_action_category__icontains="purchase", external_conversion_source__icontains="webpage", conversion_action_name__iregex=PURCHASE_REGEX).exclude(conversion_action_name__icontains="sst")
        classic_value = pick_max_action_value(classic_qs, ADS_VALUE_EXPR)

        stape_events_total = int(stape_base.aggregate(total=Sum("conversions"))["total"] or 0)
        stape_purchase_total = int(stape_base.filter(conversion_action_name__iregex=PURCHASE_REGEX).aggregate(total=Sum("conversions"))["total"] or 0)

        has_ads_data = ads_base.exists()

        # --- HEALTH CHECK LOGIC (Niezależna od kalendarza - sprawdza realne dni wstecz) ---
        def get_sum_for_range(days_limit, is_sst):
            # WAŻNE: Filtrujemy po SstMetric bezpośrednio, nie po ads_base!
            qs = SstMetric.objects.filter(property_id=ads_id, report_date__gte=days_limit, extra_json__container_id__isnull=True)
            if is_sst:
                qs = qs.filter(conversion_action_name__icontains="sst")
            else:
                qs = qs.filter(conversion_action_name__iregex=PURCHASE_REGEX).exclude(conversion_action_name__icontains="sst")
            
            val = pick_max_action_value(qs, ADS_VALUE_EXPR)
            return val if isinstance(val, (int, float)) else 0

        # Liczymy okna czasowe
        s7, c7 = get_sum_for_range(d7, True), get_sum_for_range(d7, False)
        s14, c14 = get_sum_for_range(d14, True), get_sum_for_range(d14, False)
        s30, c30 = get_sum_for_range(d30, True), get_sum_for_range(d30, False)

        def check_h_color(sst, cl):
            if cl == 0: return "gray"
            ratio = sst / cl
            if ratio < 0.85: return "red"
            return "green"

        h7_col, h14_col, h30_col = check_h_color(s7, c7), check_h_color(s14, c14), check_h_color(s30, c30)

        # TREND & ALERT
        trend, alert_msg = "Stable ✅", ""
        if h7_col == "red":
            if h30_col == "green": trend, alert_msg = "Degrading 📉", "Notice: SST efficiency gap detected in the last 7 days."
            else: trend, alert_msg = "Consistent Gap 🚨", "Attention: Persistent SST efficiency gap across all ranges."
        elif h7_col == "green" and h30_col == "red":
            trend, alert_msg = "Improving 📈", "Info: Recent data shows SST performance recovery."

        # DIAGNOSIS TEXT
        def diagnose_value(val, source_exists, type_name):
            if val != "Not available": return f"✅ {val}"
            if not source_exists: return "❌ No BQ Data"
            if type_name == "SST": return "🚨 Missing SST!"
            if type_name == "FAST": return "⚠️ No FAST"
            if type_name == "GA4": return "⚠️ No GA4"
            return "❌ Not available"

        # --- FINAL ROW ---
        rows.append({
            "Client": client_name or "UNKNOWN",
            "Ads ID": ads_id,
            "Date": f"{start_date.strftime('%d.%m')} - {end_date.strftime('%d.%m')}",
            "Classic Purchase": diagnose_value(classic_value, has_ads_data, "Classic"),
            "GA4 Purchase": diagnose_value(ga4_value, has_ads_data, "GA4"),
            "SST Purchase": diagnose_value(sst_value, has_ads_data, "SST"),
            "FAST": diagnose_value(fast_value, has_ads_data, "FAST"),
            "STAPE Purchase": f"✅ {stape_purchase_total}",
            "STAPE Events": f"✅ {stape_events_total}",
            "Status": "Active" if has_ads_data else "Disconnected (BQ)",
            "Health_7d_color": h7_col,
            "Health_14d_color": h14_col,
            "Health_30d_color": h30_col,
            "Trend": trend,
            "Alert_Message": alert_msg,
        })

    # ... (tutaj kończy się Twoja pętla for ads_id in ads_ids) ...

    # --- OBLICZENIA DLA PANELU GÓRNEGO (Poza pętlą) ---
    total_clients = len(rows)
    
    if total_clients > 0:
        # 1. SST Efficiency (Średnia wydajność wszystkich klientów)
        eff_list = []
        for r in rows:
            # Wyciągamy liczby z Twojego formatu "✅ 123"
            try:
                # Zamieniamy stringi na liczby, ignorując ikony
                sst = float(str(r['SST Purchase']).replace('✅ ', '').replace('🚨 Missing SST!', '0').replace('❌ No BQ Data', '0'))
                classic = float(str(r['Classic Purchase']).replace('✅ ', '').replace('❌ No BQ Data', '0'))
                
                if classic > 0:
                    eff_list.append(min(sst / classic, 1.0)) # max 100%
                else:
                    eff_list.append(1.0)
            except:
                eff_list.append(0)
        
        # Waga 50%
        sst_score = (sum(eff_list) / total_clients) * 50

        # 2. Health Check Stability (Waga 30% - ile jest zielonych kropek 7d)
        green_7d = len([r for r in rows if r['Health_7d_color'] == 'green'])
        stability_score = (green_7d / total_clients) * 30

        # 3. Stape Uptime (Waga 20% - czy są błędy w eventach)
        # Jeśli w STAPE Events widzimy "✅ 0", traktujemy to jako problem
        stape_issues = len([r for r in rows if "✅ 0" in str(r['STAPE Events'])])
        uptime_score = 20 if stape_issues == 0 else max(0, 20 - (stape_issues * 5))

        final_score = int(sst_score + stability_score + uptime_score)
    else:
        final_score = 0

    # Zwracamy słownik, żeby widok (views.py) mógł łatwo wyciągnąć dane
    return {
        "rows": rows,
        "stats": {
            "health_score": final_score,
            "total_clients": total_clients,
            "stape_status": "All Systems Operational" if stape_issues == 0 else f"Issues in {stape_issues} nodes"
        }
    }



def pick_max_action_value(qs, value_expr, not_available=NOT_AVAILABLE):
    """
    qs: queryset już przefiltrowany do 'kandydatów' (np. GA4 + purchase-like)
    value_expr: ADS_VALUE_EXPR (Ads) albo F("conversions") (STAPE)
    Zwraca:
      - NOT_AVAILABLE jeśli brak jakichkolwiek rekordów po filtrach
      - 0 jeśli rekordy są, ale suma = 0
      - >0 jeśli są dane
    """
    row = (
        qs.values("conversion_action_name")
          .annotate(total=Sum(value_expr))
          .order_by("-total")
          .first()
    )
    if not row:
        return not_available
    # 0 ma być 0, a nie "—"
    return int(row["total"] or 0)

def update_metrics_from_sheet(csv_url):
    print(f"--- START IMPORTU --- URL: {csv_url}")
    try:
        response = requests.get(csv_url)
        if response.status_code != 200:
            return f"Błąd pobierania: {response.status_code}"

        df = pd.read_csv(io.StringIO(response.content.decode('utf-8')))
        df.columns = [c.strip() for c in df.columns]

        count = 0
        for _, row in df.iterrows():
            try:
                raw_date = row.get('report_date')
                if not raw_date: continue
                clean_date = pd.to_datetime(raw_date).date()

                # Wyciągamy wartości, o które nam chodzi
                c1 = float(row.get('conversions', 0) or 0)
                c2 = float(row.get('all_conversions', 0) or 0)
                final_val = max(c1, c2)
                
                # Pobieramy kategorię i źródło (normalizujemy do lowercase)
                cat = str(row.get('conversion_action_category', '')).strip().lower()
                src = str(row.get('external_conversion_source', '')).strip().lower()

                # Zapisujemy do bazy
                SstMetric.objects.update_or_create(
                    property_id=str(row['property_id']),
                    report_date=clean_date,
                    conversion_action_name=str(row['conversion_action_name']),
                    external_conversion_source=src, # Ważne dla unique_together!
                    defaults={
                        'conversions': final_val,
                        'all_conversions': c2, # warto zapisać oba jeśli masz w modelu
                        'conversion_action_category': cat,
                        # Dodaj resztę metryk jeśli chcesz (value itp.)
                    }
                )
                count += 1
            except Exception as e:
                print(f"Błąd wiersza: {e}")
                continue
        
        return f"Sukces! Zaimportowano {count} wierszy."
    except Exception as e:
        return f"Błąd: {str(e)}"