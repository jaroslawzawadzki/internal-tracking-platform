from multiprocessing import context

from django.shortcuts import render,redirect
from dashboard.data_sources import (
    load_monitoring_rows,
    load_stape_overview_rows,
    build_ads_query_payload,
    load_overview_rows,
    re,
)
from django.db.models import Sum
from django.http import JsonResponse
from datetime import date, timedelta
from django.utils import timezone
from dashboard.models import SstMetric
from dashboard.data_sources import PURCHASE_REGEX
import json
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from asgiref.sync import sync_to_async
from .scanner import test_scan # To zaimportuje funkcję z pliku scanner.py
from django.template.loader import render_to_string
from django.http import HttpResponse,JsonResponse
from weasyprint import HTML
from .scanner import test_scan, get_ai_privacy_analysis  # Importujemy Twój skaner
from .services.privacy_checker import PrivacyAuditService 
import asyncio
import os
from django.contrib import messages
from .data_sources import update_metrics_from_sheet # Twoja funkcja importu
from django.db.models import Sum, DecimalField,FloatField
from django.db.models.functions import Coalesce,Trim
from datetime import datetime, time
from django.core.management import call_command


def run_mass_audit_view(request):
    
    
    # Ścieżka, gdzie zapiszemy wynik (wewnątrz kontenera)
    output_path = "audit_results_700.json"

    async def run_and_save():
        results = await PrivacyAuditService.run_batch_from_text()
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=4)
        return results

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        # To potrwa długo!
        results = loop.run_until_complete(run_and_save())
        return JsonResponse({"status": "Finished", "file_saved": output_path, "count": len(results)})
    except Exception as e:
        return JsonResponse({"status": "Error", "message": str(e)})
    finally:
        loop.close()


def trigger_stape_ingest(request):
    call_command("ingest_stape_api")

    return JsonResponse({
        "status": "ok",
        "message": "STAPE ingest triggered"
    })




def refresh_data_view(request):
    # Wywołujemy funkcję, którą wcześniej napisaliśmy
    # Pamiętaj, żeby przekazać tam Twój link z gid=392405850
    csv_url = "https://docs.google.com/spreadsheets/d/1_1gVqt0LkFzVvdydOdMoHBjFlAa5W58I4IAKFlgYuRE/export?format=csv&gid=291644932"
    
    result = update_metrics_from_sheet(csv_url)
    messages.success(request, result) # Wyśle komunikat "Zaktualizowano X wierszy"
    
    return redirect('/dashboard/overview/') # Wraca na stronę główną dashboardu

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
    # 1. Pobierz 'days' z URL (domyślnie 30)
    days_param = request.GET.get('days', '30') 
    
    try:
        days_int = int(days_param)
    except ValueError:
        days_int = 30

    # 2. Pobierz dane do tabeli na podstawie zakresu dni
    rows, monitoring_date = load_monitoring_rows(days=days_int)
    print(f"PROFIL: ostatni  test ")
    # 3. NOWOŚĆ: Zbuduj payload SQL na ten sam zakres dni
    # Przekazujemy days_int, żeby BigQuery wiedziało, jaki zakres odświeżyć
    payload = build_ads_query_payload(days=days_int)

    context = {
        "title": "SST Dashboard – Monitoring",
        "subtitle": f"SST vs Classic vs GA4 (health check) - Last {days_int} days",
        "last_refresh": str(monitoring_date) if monitoring_date else "—",
        "active_clients": len(rows),
        "total_conversions": "—",
        "columns": ["Client", "Date", "Classic Purchase", "SST Purchase", "GA4 Purchase", "Status"],
        "rows": rows,
        "selected_days": days_int,
        
        # Przekazujemy wygenerowany SQL do szablonu (np. do modala lub przycisku kopiowania)
        "bq_sql": payload.get("bq_sql", ""),
        "start_date": payload.get("start_date", ""),
    }
    
    return render(request, "dashboard/dashboard.html", context)


def dashboard_overview_view(request):
    # 1. Pobieramy liczbę dni z URL (domyślnie 14, jeśli nic nie kliknięto)
    days = int(request.GET.get('days', 14)) 
    
    # 2. Obliczamy daty na podstawie wyboru użytkownika
    today = timezone.now().date()
    end_date = today - timedelta(days=1)
    start_date = today - timedelta(days=days)

    # 3. Przekazujemy te daty do funkcji ładującej dane
    data_bundle = load_overview_rows(start_date, end_date)
    print(f"PROFIL: Co jest ")
    rows = data_bundle["rows"]
    stats = data_bundle["stats"]
    

    context = {

        "title": "SST Dashboard – Overview (LIVE)",
        "subtitle": f"{start_date} – {end_date} • Classic vs GA4 vs SST",
        "last_refresh": f"{start_date} – {end_date}",
        
        # Używamy danych z obiektu stats
        "active_clients": stats["total_clients"],
        "health_score": stats["health_score"],
        "stape_status": stats["stape_status"],
        "days_selected": days, # Przekazujemy to, żeby frontend wiedział, który przycisk podświetlić
        "start_date": start_date,
        "end_date": end_date,
        "columns": [
            "Client", "Date", "Classic Purchase", "GA4 Purchase", 
            "SST Purchase", "FAST", "STAPE Purchase", "STAPE Events", "Status",
        ],
        "rows": rows,
        "stats": stats,
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
    
    # Naprawa: usuwamy odwołanie do 'context.get', którego nie ma
    # Zamiast tego używamy po prostu aktualnego roku
    current_year = timezone.now().year

    return render(
        request,
        "dashboard/stape_overview.html",
        {
            "rows": rows,
            "ads_ids_lines": payload.get("ids_lines", ""),  # .get() jest bezpieczniejsze
            "ads_query_sql": payload.get("bq_sql", ""),
            "start_of_year": current_year,                  # Tutaj była przyczyna błędu
            "ads_ids_count": len(payload.get("ids", [])),
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

def client_revenue_analysis_view(request, ads_id):
    payload = json.loads(request.body.decode("utf-8"))
    start_date = payload.get("start_date")
    end_date = payload.get("end_date")
    items = payload.get("items", [])

    # 1. NAPRAWA DATY: Rozszerzamy end_date o 1 dzień, 
    # żeby na 100% złapać rekordy z 13, 14 i 15 maja (nawet z godziną)
    try:
        end_dt = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
    except:
        end_dt = end_date

    results = []

    for it in items:
        name = it.get("name", "").strip()
        source = it.get("source", "ADS")

        # 2. NAPRAWA FILTRA: Używamy Trim i icontains, 
        # żeby spacje w nazwach nie zerowały nam wyniku
        qs = SstMetric.objects.filter(
            property_id=ads_id,
            report_date__gte=start_date,
            report_date__lt=end_dt, # mniejsze niż jutro = cały dzisiejszy dzień włącznie
        ).annotate(
            clean_name=Trim('conversion_action_name')
        ).filter(clean_name__icontains=name)

        # 3. PANCERNA AGREGACJA: sprawdzamy obie kolumny
        aggr = qs.aggregate(
            v_all=Coalesce(Sum("all_conversion_value"), 0.0),
            v_single=Coalesce(Sum("conversion_value"), 0.0),
            q_all=Coalesce(Sum("all_conversions"), 0.0),
            q_single=Coalesce(Sum("conversions"), 0.0)
        )

        # Wybór wartości (jeśli v_all jest zerem, bierze v_single)
        final_val = float(aggr['v_all'] if aggr['v_all'] > 0 else aggr['v_single'])
        final_qty = float(aggr['q_all'] if aggr['q_all'] > 0 else aggr['q_single'])

        results.append({
            "name": it.get("name"), # Zwracamy oryginalną nazwę dla JS
            "source": source,
            "quantity": final_qty,
            "value": final_val,
            "revenue": final_val
        })

    return JsonResponse({"status": "success", "data": results})



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
    # Sprawdzamy, czy widok jest generowany dla PDF (ustawiane w funkcji eksportu)
    is_pdf = request.GET.get('pdf') == 'true'
    
    # PDF ZAWSZE po niemiecku, dashboard wedle wyboru
    lang = 'de' if is_pdf else request.GET.get('lang', 'pl')
    
    report = None

    translations = {
        'pl': {
                'title': 'Ondua Audyt Prywatności',
                'nav_audit': 'Skaner Prywatności',
                'btn_scan': 'SKANUJ PONOWNIE',
                'btn_pdf': 'POBIERZ PDF',
                'card_cookies': 'Ciasteczka / Cookies',
                'card_https': 'Szyfrowanie HTTPS',
                'card_trackers': 'Zewnętrzne Trackery',
                'status_yes': 'TAK',
                'status_no': 'NIE',
                'status_missing': 'BRAK',
                'header_trackers': 'Wykryte Trackery',
                'header_security': 'Bezpieczeństwo Nagłówków',
                'header_cookie_details': 'Szczegóły Ciasteczek (Przed zgodą)',
                'header_network': 'Szczegóły Żądań Sieciowych',
                'col_origin': 'Pochodzenie',
                'col_name': 'Nazwa / Dostawca',
                'col_domain': 'Domena',
                'col_category': 'Kategoria',
                'col_security': 'Bezpieczeństwo',
                'col_status': 'Status',
                'col_method': 'Metoda',
                'visual_proof': 'Dowód wizualny (Stan przed zgodą)',
                'visual_user': 'Widok widoczny dla użytkownika',
                'visual_deep': 'Techniczna analiza "Deep Scan"',
                'footer_copy': 'Raport wygenerowany automatycznie przez Ondua Audit Tool.',
            },
        'de': {
                'title': 'Ondua Privacy Audit', # Nagłówek okna
                'nav_audit': 'Datenschutz-Scanner', 
                'btn_scan': 'ERNEUT SCANNEN',
                'btn_pdf': 'PDF HERUNTERLADEN',
                'card_cookies': 'Cookies / Kekse',
                'card_https': 'HTTPS-Verschlüsselung',
                'card_trackers': 'Externe Tracker',
                'status_yes': 'JA',
                'status_no': 'NEIN',
                'status_missing': 'FEHLT',
                'header_trackers': 'Erkannte Tracker',
                'header_security': 'Header-Sicherheit',
                'header_cookie_details': 'Cookie-Details (Pre-Consent)',
                'header_network': 'Netzwerk-Inspector',
                'col_origin': 'Herkunft',
                'col_name': 'Name / Anbieter',
                'col_domain': 'Domain',
                'col_category': 'Kategorie',
                'col_security': 'Sicherheit',
                'col_status': 'Status',
                'col_method': 'Methode',
                'visual_proof': 'Visueller Nachweis (Vor der Einwilligung)',
                'visual_user': 'Vorderansicht (Benutzeransicht)',
                'visual_deep': 'Technische "Deep Scan" Analyse',
                'footer_copy': 'Bericht automatisch generiert von Ondua Audit Tool.',
            }
    }

    t = translations.get(lang, translations['pl'])

    if target_url:
        if not target_url.startswith(('http://', 'https://')):
            target_url = 'https://' + target_url
        report = test_scan(target_url)

    return render(request, "dashboard/audit_results.html", {
        "report": report,
        "target_url": target_url,
        "t": t,
        "lang": lang,
        "is_pdf": is_pdf
    })



def privacy_audit_view(request):
    # 1. Pobieramy parametry
    target_url = request.GET.get('url', '')
    lang = request.GET.get('lang', 'pl')
    is_pdf = request.GET.get('pdf') == 'true'
    
    # 2. DEFINIUJEMY SŁOWNIK (TUTAJ LUB WYCIĄGNIĘTY WYŻEJ)
    translations = {
        'pl': {
            'nav_audit': 'Skaner Prywatności',
            'card_cookies': 'Ciasteczka',
            # ... reszta kluczy ...
        },
        'de': {
            'nav_audit': 'Datenschutz-Scanner',
            'card_cookies': 'Cookies',
            # ... reszta kluczy ...
        }
    }

    # 3. KLUCZOWA POPRAWKA: Definiujemy 't' ZANIM wejdziemy w jakiekolwiek if-y
    t = translations.get(lang, translations['pl'])
    report = None

    # 4. Teraz idzie reszta logiki
    if target_url:
        if not target_url.startswith(('http://', 'https://')):
            target_url = 'https://' + target_url
            
        try:
            report = test_scan(target_url)
            if report and not report.get('error'):
                report['ai_analysis'] = get_ai_privacy_analysis(report)
        except Exception as e:
            report = {'error': str(e)}

    # 5. Tutaj linia 347 - teraz 't' na pewno istnieje!
    return render(request, "dashboard/audit_results.html", {
        "report": report,
        "target_url": target_url,
        "t": t,
        "lang": lang,
        "is_pdf": is_pdf
    })

def get_ai_analysis_view(request):
    target_url = request.GET.get('url')
    # Tutaj możesz pobrać dane z bazy/cache zamiast skanować od nowa
    # Na potrzeby testu symulujemy wywołanie Gemini:
    report_data = {
        'cookies_count': 19, # pobierz to z sesji lub bazy
        # ... reszta danych
    }
    analysis = get_ai_privacy_analysis(report_data)
    return JsonResponse({'analysis': analysis})