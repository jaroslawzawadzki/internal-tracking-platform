from collections import defaultdict
import re
from dashboard.models import SstMetric
from datetime import date
from django.utils import timezone
from datetime import timedelta
from django.db.models import F, Sum, Max, Case, When, IntegerField, Q ,CharField,FloatField
from django.db.models.functions import Greatest,Cast,Coalesce
from dashboard.models import MonitoringViewConfig
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
    

def get_health_analysis(
    sst,
    classic,
    *,
    green_threshold=0.90,   # >= 90% -> green
    yellow_threshold=0.60,  # >= 70% -> yellow
    min_classic_for_ratio=20,  # poniżej tego wolumenu nie ufamy ratio (za duży szum)
    low_volume_grace=0.20,  # przy małych wolumenach dopuszczamy większą tolerancję (ratio + 0.20)
    overdelivery_cap=1.30,  # ratio powyżej 1.30 traktuj jako "suspicious" (możliwy double count)
):
    """
    Porównuje SST vs Classic w sposób odporny na:
    - brak danych (classic=0)
    - małe wolumeny (ratio niestabilne)
    - sytuacje, gdzie SST > Classic (może oznaczać duplikację lub inną definicję)
    
    Zwraca dict:
      color: green/yellow/red/gray
      status: OK/WARNING/FAIL/NO_DATA/SUSPICIOUS
      alert: opis
      is_bad: bool (czy traktować jako problem)
      ratio: float|None
      sst, classic: float
    """
    # bezpieczne floaty
    s = float(sst or 0)
    c = float(classic or 0)

    # 1) Brak classic: nie da się liczyć ratio
    if c <= 0:
        if s > 0:
            # SST coś widzi, classic nie -> może być OK, ale raczej "brak punktu odniesienia"
            return {
                "color": "yellow",
                "status": "NO_BASELINE",
                "alert": "Classic = 0, SST > 0 (no baseline to compare).",
                "is_bad": False,
                "ratio": None,
                "sst": s,
                "classic": c,
            }
        return {
            "color": "gray",
            "status": "NO_DATA",
            "alert": "No purchase data (Classic = 0, SST = 0).",
            "is_bad": False,
            "ratio": None,
            "sst": s,
            "classic": c,
        }

    ratio = s / c

    # 2) Podejrzane: SST znacząco większe niż Classic
    # (często double counting / różna definicja purchase / mapowanie akcji)
    if ratio > overdelivery_cap:
        return {
            "color": "yellow",
            "status": "SUSPICIOUS",
            "alert": f"SST > Classic (ratio={ratio:.2f}). Possible double counting or definition mismatch.",
            "is_bad": False,
            "ratio": ratio,
            "sst": s,
            "classic": c,
        }

    # 3) Mały wolumen classic: ratio jest niestabilne
    # -> stosujemy "grace" (tolerancję) i łagodniejsze alerty
    if c < min_classic_for_ratio:
        # podbijamy ratio o tolerancję, żeby małe liczby nie waliły na czerwono
        adj_ratio = min(ratio + low_volume_grace, 1.0)

        if adj_ratio >= green_threshold:
            return {
                "color": "green",
                "status": "OK",
                "alert": f"Stable (low volume; ratio={ratio:.2f}).",
                "is_bad": False,
                "ratio": ratio,
                "sst": s,
                "classic": c,
            }

        if adj_ratio >= yellow_threshold:
            return {
                "color": "yellow",
                "status": "WARNING",
                "alert": f"Minor gap (low volume; ratio={ratio:.2f}).",
                "is_bad": False,
                "ratio": ratio,
                "sst": s,
                "classic": c,
            }

        return {
            "color": "yellow",
            "status": "WARNING",
            "alert": f"Gap detected but low volume (ratio={ratio:.2f}). Monitor trend.",
            "is_bad": False,
            "ratio": ratio,
            "sst": s,
            "classic": c,
        }

    # 4) Normalny wolumen: klasyczne progi
    if ratio >= green_threshold:
        return {
            "color": "green",
            "status": "OK",
            "alert": "Stable",
            "is_bad": False,
            "ratio": ratio,
            "sst": s,
            "classic": c,
        }

    if ratio >= yellow_threshold:
        return {
            "color": "yellow",
            "status": "WARNING",
            "alert": f"Notice: SST efficiency slightly lower (ratio={ratio:.2f}).",
            "is_bad": False,
            "ratio": ratio,
            "sst": s,
            "classic": c,
        }

    return {
        "color": "red",
        "status": "FAIL",
        "alert": f"Persistent SST efficiency gap (ratio={ratio:.2f}).",
        "is_bad": True,
        "ratio": ratio,
        "sst": s,
        "classic": c,
    }



def _safe_float(x):
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _severity(r, bad=0.60, warn=0.9):
    if r is None:
        return "nodata"
    if r < bad:
        return "bad"
    if r < warn:
        return "warn"
    return "ok"


def get_trend_and_alert(h7, h14, h30, disconnected=False):
    """
    Final version:
    - handles 7 / 14 / 30 days
    - separates WARNING vs FAIL
    - handles missing data + disconnected
    """

    # --- 1) DISCONNECTED ---
    if disconnected:
        return (
            "Disconnected ⚪",
            "No BigQuery data available. Please check connection, permissions and data pipeline."
        )

    # --- 2) SAFE RATIOS ---
    r7  = _safe_float((h7 or {}).get("ratio"))
    r14 = _safe_float((h14 or {}).get("ratio"))
    r30 = _safe_float((h30 or {}).get("ratio"))

    s7  = _severity(r7)
    s14 = _severity(r14)
    s30 = _severity(r30)

    states = [s7, s14, s30]

    # --- 3) NO DATA ---
    if all(s == "nodata" for s in states):
        return (
            "No Data ⚪",
            "Not enough data to evaluate SST performance."
        )

    # --- 4) COUNT STATES ---
    bad_count  = states.count("bad")
    warn_count = states.count("warn")

    # --- 5) HARD FAIL ---
    if bad_count >= 2:
        return (
            "Consistent Gap 🚨",
            "Action required: Strong and persistent SST underperformance across multiple periods (7d/14d/30d)."
        )

    # --- 6) WARNING ---
    if bad_count == 1 or warn_count >= 2:
        return (
            "Watch ⚠️",
            "Monitor: SST performance below optimal level. Check recent changes and validate tracking."
        )

    # --- 7) TREND ANALYSIS (FULL 7/14/30) ---
    if r7 is not None and r14 is not None and r30 is not None:
        delta = 0.05

        # improving: 7 > 14 > 30
        if (r7 - r14) > delta and (r14 - r30) > delta:
            return (
                "Improving 📈",
                "Info: Clear recovery trend (7d > 14d > 30d)."
            )

        # degrading: 7 < 14 < 30
        if (r14 - r7) > delta and (r30 - r14) > delta:
            return (
                "Degrading 📉",
                "Notice: Performance declining over time (7d < 14d < 30d)."
            )

        # volatile (skacze)
        if abs(r7 - r14) > 2 * delta and abs(r14 - r30) > 2 * delta:
            return (
                "Volatile 🔄",
                "Notice: Inconsistent SST performance across periods."
            )

        # stable (małe różnice)
        if max(r7, r14, r30) - min(r7, r14, r30) <= delta:
            return (
                "Stable ✅",
                "Stable SST performance across all periods."
            )

    # --- 8) FALLBACK ---
    return (
        "Stable ✅",
        "Stable."
    )

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


        # 1. Pobieramy unikalne pary ID i nazwa z bazy SstMetric
        # Wybieramy property_id i client_name, ignorując te, gdzie nazwy nie ma
        raw_names = SstMetric.objects.exclude(
            extra_json__client_name__isnull=True
        ).values_list("property_id", "extra_json__client_name").distinct()

        # 2. Zamieniamy to na słownik (mapę) dla szybkiego wyszukiwania
        names_map = {str(pid): name for pid, name in raw_names}
        # UŻYWAMY NASZEGO "MÓZGU" LOGIKI
       # 1. Wywołujesz funkcję
        analysis = get_health_analysis(sst_val, classic_val)

        # 2. Wyciągasz wartości ze słownika
        # status = analysis["status"]
        # health_color = analysis["color"]
        # alert_msg = analysis["alert"]
        status = "FROM_MONITORING_ROWS"
        health_color = "green"
        alert_msg = "TMONITORING_FUNCTION_HIT"

        print(f"PROFIL: {pid} | SST: {sst_val} ({type(sst_val)}) | CL: {classic_val} ({type(classic_val)})")
        # Boolean do ikonek (zostawiamy dla czytelności ✅/❌)
        classic_ok = classic_val > 0
        sst_ok = sst_val > 0
        
        rows.append({
            "Client": names_map.get(pid, pid),
            "Ads ID": pid,
            "Date": f"{start_date.strftime('%d.%m')} - {today.strftime('%d.%m')}",
            "Classic Purchase": ("✅" if classic_ok else "❌") + f" ({int(classic_val)})",
            "GA4 Purchase": ("✅" if ga4_val > 0 else "❌") + f" ({int(ga4_val)})",
            "SST Purchase": ("✅" if sst_ok else "❌") + f" ({int(sst_val)})",
            "FAST": ("✅" if fast_val > 0 else "⚠️") + f" ({int(fast_val)})",
            "STAPE Purchase": f"✅ {int(stape_p_val)}",
            "STAPE Events": f"✅ {int(stape_e_val)}",
            "Status": status,
            "Alert_Message": alert_msg,
            "Health_7d_color": health_color,
            "Health_14d_color": health_color,
            "Health_30d_color": health_color,
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

def get_client_mapping(property_id: str) -> dict:
    obj = MonitoringViewConfig.objects.filter(property_id=str(property_id)).only("config").first()
    if not obj or not obj.config or not isinstance(obj.config, dict):
        return {}
    return obj.config.get("selected") or {}


def load_overview_rows(start_date, end_date):  # ta uzywam w widoku dashboard_overview_view

    rows = []
    
    # Expression do liczenia "ilości" per wiersz:
    # max(all_conversions, conversions) z NULL-safe
    ADS_QTY_EXPR = Greatest(
        Coalesce(F("all_conversions"), 0.0),
        Coalesce(F("conversions"), 0.0),
    )

    def sum_action_value(qs):
        """
        Zwraca SUMĘ (po zakresie dat w qs) zamiast max/peak.
        Dzięki temu 7/14/30/60 realnie zmienia liczby w tabeli.
        """
        return float(qs.aggregate(total=Sum(ADS_QTY_EXPR))["total"] or 0.0)


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
        selected = get_client_mapping(ads_id)
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
        selected_fast = selected.get("fast")

        if selected_fast:
            # 1) Najpierw: to co user przypiął (exact match)
            fast_qs = ads_base.filter(conversion_action_name=selected_fast)
        else:
            # 2) Fallback: dotychczasowe AUTO (heurystyka FAST)
            fast_qs = ads_base.filter(
                conversion_action_category__icontains="purchase",
                external_conversion_source__icontains="upload",
                conversion_action_name__icontains="gclid"
            )

        fast_value = sum_action_value(fast_qs)


        selected_sst = selected.get("sst")

        if selected_sst:
            # 1) Najpierw: to co user przypiął (exact match)
            sst_qs = ads_base.filter(conversion_action_name=selected_sst)
        else:
            # 2) Fallback: dotychczasowe AUTO (heurystyka SST)
            sst_qs = ads_base.filter(
                conversion_action_category__icontains="purchase",
                external_conversion_source__icontains="webpage",
                conversion_action_name__icontains="sst"
            )

        sst_value = sum_action_value(sst_qs)


        selected_ga4 = selected.get("ga4")

        if selected_ga4:
            # 1) Najpierw: to co user przypiął (exact match)
            ga4_qs = ads_base.filter(conversion_action_name=selected_ga4)
        else:
            # 2) Fallback: dotychczasowe AUTO (heurystyka GA4)
            ga4_qs = (
                ads_base
                .filter(conversion_action_category__icontains="purchase")
                .filter(Q(conversion_action_name__icontains="ga4") | Q(external_conversion_source__icontains="unknown"))
                .exclude(conversion_action_name__icontains="sst")
            )
        ga4_value = sum_action_value(ga4_qs)

        selected_classic = selected.get("classic")

        if selected_classic:
            # 1) Najpierw: to co user przypiął (exact match)
            classic_qs = ads_base.filter(conversion_action_name=selected_classic)
        else:
            # 2) Fallback: dotychczasowe AUTO (heurystyka purchase)
            classic_qs = (
                ads_base
                .filter(
                    conversion_action_category__icontains="purchase",
                    external_conversion_source__icontains="webpage",
                    conversion_action_name__iregex=PURCHASE_REGEX
                )
                .exclude(conversion_action_name__icontains="sst")
            )

        classic_value = sum_action_value(classic_qs)

        stape_events_total = int(stape_base.aggregate(total=Sum("conversions"))["total"] or 0)
        stape_purchase_total = int(
            stape_base.filter(conversion_action_name__iregex=PURCHASE_REGEX).aggregate(total=Sum("conversions"))["total"] or 0
        )

        has_ads_data = ads_base.exists()

        # --- HEALTH CHECK LOGIC ---

        def get_sum_for_range(start_dt, end_dt, is_sst):
            """
            Liczy SUMĘ konwersji w oknie [start_dt, end_dt] dla:
            - SST purchase (is_sst=True)
            - Classic purchase (is_sst=False)

            Spójne z filtrami używanymi w tabeli:
            - conversion_action_category zawiera "purchase"
            - external_conversion_source zawiera "webpage"
            - classic: regex PURCHASE_REGEX i exclude 'sst'
            - sst: name contains 'sst'

            Zwraca float.
            """
            qs = SstMetric.objects.filter(
                property_id=ads_id,
                report_date__range=[start_dt, end_dt],
                extra_json__container_id__isnull=True,
                conversion_action_category__icontains="purchase",
                external_conversion_source__icontains="webpage",
            )

            if is_sst:
                qs = qs.filter(conversion_action_name__icontains="sst")
            else:
                qs = qs.filter(conversion_action_name__iregex=PURCHASE_REGEX).exclude(
                    conversion_action_name__icontains="sst"
                )

            total = qs.aggregate(total=Sum(ADS_VALUE_EXPR))["total"] or 0
            return float(total)

        # 1. Pobieramy surowe dane dla okien czasowych
        # ✅ POPRAWKA: teraz przekazujemy end_dt=today + is_sst
        s7  = get_sum_for_range(d7,  today, True)
        c7  = get_sum_for_range(d7,  today, False)
        s14 = get_sum_for_range(d14, today, True)
        c14 = get_sum_for_range(d14, today, False)
        s30 = get_sum_for_range(d30, today, True)
        c30 = get_sum_for_range(d30, today, False)

        # 2. UŻYWAMY GLOBALNYCH FUNKCJI (Analiza zdrowia)
        h7 = get_health_analysis(s7, c7)
        h14 = get_health_analysis(s14, c14)
        h30 = get_health_analysis(s30, c30)

        # 3. UŻYWAMY GLOBALNEJ FUNKCJI (Trend & Alert)
        trend, alert_msg = get_trend_and_alert(h7, None, h30)

        # --- DIAGNOSIS TEXT ---
        def diagnose_value(val, source_exists, type_name):
            
            if val not in ("Not available", None) and float(val) > 0:
                    return f"✅ {int(round(float(val)))}"
            if not source_exists:
                return "❌ No BQ Data"

            mapping = {
                "SST": "🚨 Missing SST!",
                "FAST": "⚠️ No FAST",
                "GA4": "⚠️ No GA4"
            }
            return mapping.get(type_name, "❌ Not available")

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
            "Status": h7['status'] if has_ads_data else "Disconnected (BQ)",
            "Health_7d_color": h7['color'],
            "Health_14d_color": h14['color'],
            "Health_30d_color": h30['color'],
            "Trend": trend,
            "Alert_Message": alert_msg,
        })

    # --- OBLICZENIA DLA PANELU GÓRNEGO (Poza pętlą) ---
    total_clients = len(rows)

    # ✅ zabezpieczenie, żeby nie było NameError gdy total_clients == 0
    stape_issues = 0

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
                    eff_list.append(min(sst / classic, 1.0))  # max 100%
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

        df = pd.read_csv(io.StringIO(response.content.decode("utf-8")))
        df.columns = [c.strip() for c in df.columns]

        # (opcjonalny debug na 1 uruchomienie — możesz potem usunąć)
        # print("COLUMNS:", df.columns.tolist())

        def to_float_or_zero(x):
            """Bezpiecznie zamienia na float; None/NaN/pusty -> 0.0"""
            if x is None:
                return 0.0
            try:
                # pandas czasem daje NaN jako float
                if isinstance(x, float) and math.isnan(x):
                    return 0.0
            except Exception:
                pass
            s = str(x).strip()
            if s == "" or s.lower() in ("nan", "none", "null"):
                return 0.0
            # czasem wartości przychodzą z przecinkiem
            s = s.replace(",", ".")
            try:
                return float(s)
            except Exception:
                return 0.0

        count = 0
        updated = 0
        inserted = 0
        skipped = 0

        for _, row in df.iterrows():
            try:
                raw_date = row.get("report_date")
                if not raw_date:
                    skipped += 1
                    continue
                clean_date = pd.to_datetime(raw_date).date()

                property_id_raw = row.get("property_id")
                conv_name_raw = row.get("conversion_action_name")
                if property_id_raw is None or conv_name_raw is None:
                    skipped += 1
                    continue

                property_id = str(property_id_raw).strip()
                conversion_action_name = str(conv_name_raw).strip()
                if not property_id or not conversion_action_name:
                    skipped += 1
                    continue

                # Liczby
                c1 = to_float_or_zero(row.get("conversions"))
                c2 = to_float_or_zero(row.get("all_conversions"))
                final_val = max(c1, c2)

                # VALUE (w sheet widzę: conversions_value / all_conversions_value)
                # + fallback, jeśli export używa innych nazw
                cv = row.get("conversions_value", None)
                if cv is None:
                    cv = row.get("conversion_value", None)  # fallback

                acv = row.get("all_conversions_value", None)
                if acv is None:
                    acv = row.get("all_conversion_value", None)  # fallback

                conversion_value = to_float_or_zero(cv)
                all_conversion_value = to_float_or_zero(acv)

                # Meta
                cat = str(row.get("conversion_action_category", "")).strip().lower()

                # Normalizacja source (ważne dla update_or_create)
                src = row.get("external_conversion_source", "")
                src = "" if src is None else str(src).strip().lower()

                obj, created = SstMetric.objects.update_or_create(
                    property_id=property_id,
                    report_date=clean_date,
                    conversion_action_name=conversion_action_name,
                    external_conversion_source=src,
                    defaults={
                        "conversions": final_val,
                        "all_conversions": c2,
                        "conversion_value": conversion_value,
                        "all_conversion_value": all_conversion_value,
                        "conversion_action_category": cat,
                        "external_conversion_source": src,
                    },
                )

                count += 1
                if created:
                    inserted += 1
                else:
                    updated += 1

            except Exception as e:
                print(f"Błąd wiersza: {e}")
                continue

        return f"Sukces! processed={count}, inserted={inserted}, updated={updated}, skipped={skipped}"

    except Exception as e:
        return f"Błąd: {str(e)}"