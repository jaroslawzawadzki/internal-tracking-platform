
from django.db.models.functions import Cast
from django.db.models import CharField
from collections import defaultdict
import re
from django.db.models import Sum, Case, When, IntegerField
from dashboard.models import SstMetric
from datetime import date
from django.utils import timezone
from django.db.models import Max
from datetime import timedelta
from django.db.models import F, Sum
from django.db.models.functions import Greatest



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


def load_monitoring_rows(monitoring_date=None):
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

    # 1) wybierz dzień (domyślnie: ostatni dzień w DB)
    if monitoring_date is None:
        monitoring_date = (
            SstMetric.objects
            .order_by("-report_date")
            .values_list("report_date", flat=True)
            .first()
        )

    if not monitoring_date:
        return [], None

    # 2) bierzemy wszystkie rekordy z tego dnia
    base = SstMetric.objects.filter(report_date=monitoring_date).only(
        "property_id", "conversions", "conversion_action_name"
    )

    # 3) mapy per klient
    sst_map = defaultdict(float)
    classic_map = defaultdict(float)
    ga4_map = defaultdict(float)

    for row in base:
        pid = row.property_id
        val = float(row.conversions or 0)
        name = normalize(getattr(row, "conversion_action_name", ""))

        # SST slot: SST + (purchase/kauf/bestellung)
        if "sst" in name and any(k in name for k in CLASSIC_KEYWORDS):
            sst_map[pid] += val
            continue

        # GA4 slot: GA4 + purchase
        if "ga4" in name and "purchase" in name:
            ga4_map[pid] += val
            continue

        # Classic slot: bez SST + (purchase/kauf/bestellung)
        if "sst" not in name and any(k in name for k in CLASSIC_KEYWORDS):
            classic_map[pid] += val
            continue

        # wszystko inne ignorujemy w monitoringu (zostaje do drill-down)

    # 4) union klientów, którzy mają jakiekolwiek dane w którymkolwiek slocie
    all_clients = set()
    all_clients.update(sst_map.keys())
    all_clients.update(classic_map.keys())
    all_clients.update(ga4_map.keys())

    rows = []
    for pid in sorted(all_clients):
        classic_val = float(classic_map.get(pid, 0) or 0)
        sst_val = float(sst_map.get(pid, 0) or 0)
        ga4_val = float(ga4_map.get(pid, 0) or 0)

        classic_ok = classic_val > 0
        sst_ok = sst_val > 0
        ga4_ok = ga4_val > 0

        # status wg ustaleń:
        # - OK: SST i Classic są
        # - UNIDENTIFIED: SST jest, Classic nie ma
        # - FAIL: SST nie ma
        if sst_ok and classic_ok:
            status = "OK"
        elif sst_ok and not classic_ok:
            status = "UNIDENTIFIED"
        else:
            status = "FAIL"

        rows.append({
            "Client": pid,
            "Date": monitoring_date,
            "Classic Purchase": ("✅" if classic_ok else "❌") + f" ({int(classic_val)})",
            "SST Purchase": ("✅" if sst_ok else "❌") + f" ({int(sst_val)})",
            "GA4 Purchase": ("✅" if ga4_ok else "❌") + f" ({int(ga4_val)})",
            "Status": status,
        })

    return rows, monitoring_date

def load_stape_overview_rows():
  
    """
    Overview per client:
    - purchase = SUM(event == 'purchase')
    - events_total = SUM(all events)
    """

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
                    output_field=IntegerField(),
                )
            ),
            events_total=Sum("conversions"),
        )
        .order_by("property_id")
    )

    rows = []
    for r in qs:
        rows.append({
            "client": r["client_name"] or "UNKNOWN",
            "ads_id": r["property_id"],
            "purchase": int(r["purchase"] or 0),
            "events_total": int(r["events_total"] or 0),
        })


    return rows


def build_ads_query_payload():
    """
    Zbiera unikalne Ads ID (property_id) z DB i buduje:
    - listę do Excela (1 na linię)
    - SQL (start of year -> today)
    """

    # 1) unikalne Ads ID z bazy (tylko te, które istnieją)
    ids = list(
        SstMetric.objects
        .exclude(property_id__isnull=True)
        .exclude(property_id="")
        .values_list("property_id", flat=True)
        .distinct()
        .order_by("property_id")
    )

    # 2) okres: od początku roku do dziś
    today = timezone.now().date()
    start_of_year = date(today.year, 1, 1)

    # 3) formaty
    ids_lines = "\n".join(ids)  # do Excela / Sheets

    # BigQuery SQL (dopasuj nazwę tabeli/kolumny jeśli masz inną)
    ids_sql = ", ".join([f'"{x}"' for x in ids])

    bq_sql = f"""
SELECT *
FROM `external-customer-reporting.smk_internal_tracking.gads_conversion_metrics`
WHERE property_id IN ({ids_sql})
  AND report_date >= DATE("{start_of_year.isoformat()}")
""".strip()

    # Alternatywa: jeśli w tabeli masz inną kolumnę daty:
    # AND date >= DATE("{start_of_year.isoformat()}")

    return {
        "ids": ids,
        "ids_lines": ids_lines,
        "start_of_year": start_of_year.isoformat(),
        "bq_sql": bq_sql,
    }

def load_overview_rows(start_date, end_date):
    """
    FIRST VIEW (OVERVIEW) - zakres dat
    GA4/SST/Classic/FAST: wybieramy 1 akcję (MAX) w danym typie w zakresie
    STAPE: sumy (events + purchase-like)
    """
    rows = []

    ads_ids = (
        SstMetric.objects
        .exclude(property_id__isnull=True)
        .exclude(property_id="")
        .values_list("property_id", flat=True)
        .distinct()
        .order_by("property_id")
    )

    for ads_id in ads_ids:
        # Client name (najpierw próbujemy z extra_json)
        client_name = (
            SstMetric.objects
            .filter(property_id=ads_id)
            .exclude(extra_json__client_name__isnull=True)
            .values_list("extra_json__client_name", flat=True)
            .first()
        )

        # ADS/SHEET (bez STAPE)
        ads_base = (
            SstMetric.objects
            .filter(
                property_id=ads_id,
                report_date__gte=start_date,
                report_date__lte=end_date,
                extra_json__container_id__isnull=True,
            )
        )

        # STAPE
        stape_base = (
            SstMetric.objects
            .filter(
                property_id=ads_id,
                report_date__gte=start_date,
                report_date__lte=end_date,
            )
            .exclude(extra_json__container_id__isnull=True)
        )

        # --- FAST (gclid...) -> MAX w zakresie ---
        fast_qs = (
            ads_base
            .filter(conversion_action_name__icontains="gclid")
            .filter(conversion_action_name__iregex=PURCHASE_REGEX)
        )
        fast_value = pick_max_action_value(fast_qs, ADS_VALUE_EXPR)

        # --- GA4 -> MAX w zakresie, tylko purchase-like (żeby GA4 form_send nie wpadało) ---
        ga4_qs = (
            ads_base
            .filter(conversion_action_name__icontains="ga4")
            .filter(conversion_action_name__iregex=PURCHASE_REGEX)
        )
        ga4_value = pick_max_action_value(ga4_qs, ADS_VALUE_EXPR)

        # --- SST -> MAX w zakresie, tylko purchase-like ---
        sst_qs = (
            ads_base
            .filter(conversion_action_name__icontains="sst")
            .filter(conversion_action_name__iregex=PURCHASE_REGEX)
        )
        sst_value = pick_max_action_value(sst_qs, ADS_VALUE_EXPR)

        # --- CLASSIC -> MAX w zakresie, purchase-like, bez ga4/sst/gclid ---
        classic_qs = (
            ads_base
            .filter(conversion_action_name__iregex=PURCHASE_REGEX)
            .exclude(conversion_action_name__icontains="ga4")
            .exclude(conversion_action_name__icontains="sst")
            .exclude(conversion_action_name__icontains="gclid")
        )
        classic_value = pick_max_action_value(classic_qs, ADS_VALUE_EXPR)

        # --- STAPE EVENTS -> SUMA ---
        stape_events_total = int(stape_base.aggregate(total=Sum("conversions"))["total"] or 0)

        # --- STAPE PURCHASE -> SUMA purchase-like ---
        stape_purchase_total = int(
            stape_base
            .filter(conversion_action_name__iregex=PURCHASE_REGEX)
            .aggregate(total=Sum("conversions"))["total"] or 0
        )

        rows.append({
            "Client": client_name or "UNKNOWN",
            "Ads ID": ads_id,
            "Date": f"{start_date} – {end_date}",
            "Classic Purchase": classic_value,
            "GA4 Purchase": ga4_value,
            "SST Purchase": sst_value,
            "FAST": fast_value,
            "STAPE Purchase": stape_purchase_total,
            "STAPE Events": stape_events_total,
            "Status": "—",
        })

    return rows
def load_overview_rows(start_date, end_date):
    """
    FIRST VIEW (OVERVIEW) - zakres dat
    GA4/SST/Classic/FAST: wybieramy 1 akcję (MAX) w danym typie w zakresie
    STAPE: sumy (events + purchase-like)
    """
    rows = []

    ads_ids = (
        SstMetric.objects
        .exclude(property_id__isnull=True)
        .exclude(property_id="")
        .values_list("property_id", flat=True)
        .distinct()
        .order_by("property_id")
    )

    for ads_id in ads_ids:
        # Client name (najpierw próbujemy z extra_json)
        client_name = (
            SstMetric.objects
            .filter(property_id=ads_id)
            .exclude(extra_json__client_name__isnull=True)
            .values_list("extra_json__client_name", flat=True)
            .first()
        )

        # ADS/SHEET (bez STAPE)
        ads_base = (
            SstMetric.objects
            .filter(
                property_id=ads_id,
                report_date__gte=start_date,
                report_date__lte=end_date,
                extra_json__container_id__isnull=True,
            )
        )

        # STAPE
        stape_base = (
            SstMetric.objects
            .filter(
                property_id=ads_id,
                report_date__gte=start_date,
                report_date__lte=end_date,
            )
            .exclude(extra_json__container_id__isnull=True)
        )

        # --- FAST (gclid...) -> MAX w zakresie ---
        fast_qs = (
            ads_base
            .filter(conversion_action_name__icontains="gclid")
            .filter(conversion_action_name__iregex=PURCHASE_REGEX)
        )
        fast_value = pick_max_action_value(fast_qs, ADS_VALUE_EXPR)

        # --- GA4 -> MAX w zakresie, tylko purchase-like (żeby GA4 form_send nie wpadało) ---
        ga4_qs = (
            ads_base
            .filter(conversion_action_name__icontains="ga4")
            .filter(conversion_action_name__iregex=PURCHASE_REGEX)
        )
        ga4_value = pick_max_action_value(ga4_qs, ADS_VALUE_EXPR)

        # --- SST -> MAX w zakresie, tylko purchase-like ---
        sst_qs = (
            ads_base
            .filter(conversion_action_name__icontains="sst")
            .filter(conversion_action_name__iregex=PURCHASE_REGEX)
        )
        sst_value = pick_max_action_value(sst_qs, ADS_VALUE_EXPR)

        # --- CLASSIC -> MAX w zakresie, purchase-like, bez ga4/sst/gclid ---
        classic_qs = (
            ads_base
            .filter(conversion_action_name__iregex=PURCHASE_REGEX)
            .exclude(conversion_action_name__icontains="ga4")
            .exclude(conversion_action_name__icontains="sst")
            .exclude(conversion_action_name__icontains="gclid")
        )
        classic_value = pick_max_action_value(classic_qs, ADS_VALUE_EXPR)

        # --- STAPE EVENTS -> SUMA ---
        stape_events_total = int(stape_base.aggregate(total=Sum("conversions"))["total"] or 0)

        # --- STAPE PURCHASE -> SUMA purchase-like ---
        stape_purchase_total = int(
            stape_base
            .filter(conversion_action_name__iregex=PURCHASE_REGEX)
            .aggregate(total=Sum("conversions"))["total"] or 0
        )

        rows.append({
            "Client": client_name or "UNKNOWN",
            "Ads ID": ads_id,
            "Date": f"{start_date} – {end_date}",
            "Classic Purchase": classic_value,
            "GA4 Purchase": ga4_value,
            "SST Purchase": sst_value,
            "FAST": fast_value,
            "STAPE Purchase": stape_purchase_total,
            "STAPE Events": stape_events_total,
            "Status": "—",
        })

    return rows

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