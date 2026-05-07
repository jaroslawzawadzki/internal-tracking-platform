from django.shortcuts import render
from dashboard.data_sources import (
    load_monitoring_rows,
    load_stape_overview_rows,
    build_ads_query_payload,
    load_overview_rows,
    re,
)
from django.http import JsonResponse
from datetime import date, timedelta
from django.utils import timezone
from dashboard.models import SstMetric
from dashboard.data_sources import PURCHASE_REGEX
from django.db.models import Sum
import json
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from asgiref.sync import sync_to_async
from .scanner import test_scan # To zaimportuje funkcję z pliku scanner.py
from django.template.loader import render_to_string
from django.http import HttpResponse
from weasyprint import HTML
from .scanner import test_scan  # Importujemy Twój skaner


def export_pdf(request):
    # 1. Pobieramy URL ze strony (przekazany w linku)
    target_url = request.GET.get('url')
    
    if not target_url:
        return HttpResponse("Błąd: Nie podano adresu URL do raportu.")

    # 2. Uruchamiamy skaner ponownie, żeby pobrać świeże dane do PDF
    report_data = test_scan(target_url)

    # 3. Renderujemy nasz piękny szablon HTML do zwykłego tekstu (stringa)
    html_string = render_to_string('dashboard/audit_results.html', {
        'report': report_data,
        'target_url': target_url,
        'is_pdf': True  # Ta flaga pozwoli nam ukryć formularz w PDF-ie
    })

    # 4. Tworzymy obiekt PDF
    # base_url jest ważny, żeby WeasyPrint wiedział skąd brać obrazki/style
    html = HTML(string=html_string, base_url=request.build_absolute_uri('/'))
    pdf_file = html.write_pdf()

    # 5. Przygotowujemy odpowiedź przeglądarki (plik do pobrania)
    response = HttpResponse(pdf_file, content_type='application/pdf')
    
    # Nazwa pliku będzie wyglądać np. Audyt_wp_pl.pdf
    filename = f"Audyt_{target_url.replace('https://', '').replace('http://', '').replace('/', '_')}.pdf"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    
    return response


def dashboard_view(request):
    rows, monitoring_date = load_monitoring_rows()

    context = {
        "title": "SST Dashboard – Monitoring",
        "subtitle": "SST vs Classic vs GA4 (health check)",
        "last_refresh": str(monitoring_date) if monitoring_date else "—",
        "active_clients": len(rows),
        "total_conversions": "—",
        "columns": ["Client", "Date", "Classic Purchase", "SST Purchase", "GA4 Purchase", "Status"],
        "rows": rows,
    }
    return render(request, "dashboard/dashboard.html", context)


def dashboard_overview_view(request):
    
    today = timezone.now().date()
    start_date = date(today.year, 1, 1)
    end_date = today - timedelta(days=1)

    rows = load_overview_rows(start_date, end_date)

    context = {
        "title": "SST Dashboard – Overview (LIVE)",
        "subtitle": f"{start_date} – {end_date} • Classic vs GA4 vs SST",
        "last_refresh": f"{start_date} – {end_date}",
        "active_clients": len(rows),
        "total_conversions": "—",
        "columns": [
            "Client",
            "Ads ID",
            "Date",
            "Classic Purchase",
            "GA4 Purchase",
            "SST Purchase",
            "FAST",
            "STAPE Purchase",
            "STAPE Events",
            "Status",
        ],
        "rows": rows,
    }
    return render(request, "dashboard/dashboard.html", context)


def stape_overview_debug(request):
    rows = load_stape_overview_rows()
    return JsonResponse({"rows": rows})


def stape_overview_view(request):
    rows = load_stape_overview_rows()
    return render(
        request,
        "dashboard/stape_overview.html",
        {
            "rows": rows
        }
    )


def stape_overview_view(request):
    rows = load_stape_overview_rows()
    payload = build_ads_query_payload()

    return render(
        request,
        "dashboard/stape_overview.html",
        {
            "rows": rows,
            "ads_ids_lines": payload["ids_lines"],
            "ads_query_sql": payload["bq_sql"],
            "start_of_year": payload["start_of_year"],
            "ads_ids_count": len(payload["ids"]),
        }
    )

def client_conversions_view(request, ads_id):
    qs = (
        SstMetric.objects
        .filter(property_id=ads_id)
        .exclude(conversion_action_name__isnull=True)
        .values_list("conversion_action_name", flat=True)
        .distinct()
        .order_by("conversion_action_name")
    )

    conversions = []

    for name in qs:
        name_l = name.lower()

        if "ga4" in name_l:
            conv_type = "GA4"
        elif "sst" in name_l:
            conv_type = "SST"
        elif "gclid" in name_l:
            conv_type = "FAST"
        else:
            conv_type = "CLASSIC"

        # ✅ KLUCZOWE: skąd pochodzi konwersja
        is_stape = (
            SstMetric.objects
            .filter(
                property_id=ads_id,
                conversion_action_name=name
            )
            .exclude(extra_json__container_id__isnull=True)
            .exists()
        )

        source = "STAPE" if is_stape else "ADS"

        conversions.append({
            "name": name,
            "purchase_like": bool(re.search(PURCHASE_REGEX, name_l)),
            "type": conv_type,
            "source": source,   # ✅ TO JEST NOWE I WAŻNE
        })

    return JsonResponse({
        "ads_id": ads_id,
        "conversions": conversions
    })


@csrf_exempt
@require_POST
def client_sums_view(request, ads_id):

    """
    Zwraca sumy dla wybranych conversion_action_name w zadanym zakresie dat.
    Body JSON:
      {
        "start_date": "2026-01-01",
        "end_date": "2026-04-28",
        "items": [
          {"name": "...", "source": "ADS"|"STAPE"}
        ]
      }
    """
    payload = json.loads(request.body.decode("utf-8"))
    start_date = payload.get("start_date")
    end_date = payload.get("end_date")
    items = payload.get("items", [])

    results = []

    for it in items:
        name = it.get("name")
        source = it.get("source", "ADS")

        if not name:
            continue

        qs = SstMetric.objects.filter(
            property_id=ads_id,
            report_date__gte=start_date,
            report_date__lte=end_date,
            conversion_action_name=name,
        )

        # rozdział ADS vs STAPE
        if source == "STAPE":
            qs = qs.exclude(extra_json__container_id__isnull=True)
            total = qs.aggregate(total=Sum("conversions"))["total"] or 0
        else:
            qs = qs.filter(extra_json__container_id__isnull=True)
            # po naprawie ingest: all_conversions jest w kolumnie
            total = qs.aggregate(total=Sum("all_conversions"))["total"] or 0

        results.append({
            "name": name,
            "source": source,
            "sum": round(float(total), 0),
        })

    return JsonResponse({"ads_id": ads_id, "results": results})



def privacy_audit_view(request):
    target_url = request.GET.get('url')
    report = None
    
    if target_url:
        # 1. Poprawiamy protokół
        if not target_url.startswith(('http://', 'https://')):
            target_url = 'https://' + target_url
            
        # 2. Wywołujemy skaner (zwykłe wywołanie, bez await)
        # Przekazujemy target_url, a wynik zapisujemy do report
        report = test_scan(target_url)
    
    return render(request, "dashboard/audit_results.html", {
        "report": report,
        "target_url": target_url,
        "title": "Skaner Prywatności"
    })

