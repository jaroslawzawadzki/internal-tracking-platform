from django.core.management.base import BaseCommand
from dashboard.services.stape import stape_get
from dashboard.models import SstMetric

import re
from collections import defaultdict
from datetime import datetime


class Command(BaseCommand):
    def handle(self, *args, **options):
        print("=== STAPE INGEST START ===")

        # 1) Pobierz kontenery
        data = stape_get("/containers")
        body = data.get("body", {})
        items = body.get("items", [])

        print("CONTAINERS COUNT:", len(items))
        if not items:
            print("NO CONTAINERS FOUND")
            print("=== STAPE INGEST END ===")
            return

        # 2) Filtr: SST + running
        sst_running = []
        for c in items:
            name = c.get("name", "")
            status = c.get("status", {}).get("type")

            if "SST" not in name:
                continue
            if status != "running":
                continue

            sst_running.append(c)

        print("SST RUNNING COUNT:", len(sst_running))
        if not sst_running:
            print("NO SST RUNNING CONTAINERS")
            print("=== STAPE INGEST END ===")
            return

        # 3) Parsowanie: client_name + ads_id (z [....])
        parsed = []
        for c in sst_running:
            name = c.get("name", "")

            # client = wszystko przed " - "
            client_name = name.split(" - ")[0].strip()

            # Ads ID w [ ... ] (jeśli brak -> pomijamy kontener)
            match = re.search(r"\[([0-9-\s]{6,})\]", name)
            if not match:
                print("NO ADS ID FOUND IN NAME:", name)
                continue

            ads_id = re.sub(r"\D", "", match.group(1))
            if not ads_id:
                print("EMPTY ADS ID AFTER CLEAN:", name)
                continue

            parsed.append({
                "client_name": client_name,
                "ads_id": ads_id,
                "container_id": c.get("identifier"),
            })

        print("PARSED COUNT:", len(parsed))
        if not parsed:
            print("NO PARSED CLIENTS")
            print("=== STAPE INGEST END ===")
            return

        # 4) Globalny overview (sumujemy po wszystkich kontenerach)
        overview_per_day = defaultdict(lambda: {
            "purchase": 0,
            "events_total": 0,
        })

        saved_rows = 0
        processed_containers = 0

        # 5) Iteracja po WSZYSTKICH kontenerach
        for cont in parsed:
            processed_containers += 1
            print("PROCESSING CONTAINER:", cont)

            container_id = cont["container_id"]

            usage_data = stape_get(f"/containers/{container_id}/analytics/clients")
            analytics_body = usage_data.get("body", [])

            # BACKFILL: ostatnie 14 dni
            analytics_body = analytics_body[:14]

            print("ANALYTICS DAYS COUNT:", len(analytics_body))
            if not analytics_body:
                print("NO ANALYTICS FOR THIS CONTAINER")
                continue

            # zapis + overview
            for day in analytics_body:
                ts = day.get("date")
                if not ts:
                    continue

                report_date = datetime.utcfromtimestamp(ts).date()
                day_str = report_date.strftime("%Y-%m-%d")

                for client in day.get("clients", []):
                    stape_client = client.get("name", "unknown")

                    for event in client.get("events", []):
                        event_name = (event.get("name") or "").strip().lower()
                        count = int(event.get("count") or 0)

                        if not event_name:
                            continue

                        # ✅ OVERVIEW (heartbeat)
                        overview_per_day[day_str]["events_total"] += count
                        if event_name == "purchase":
                            overview_per_day[day_str]["purchase"] += count

                        # ✅ ZAPIS DO DB (wszystkie eventy osobno)
                        SstMetric.objects.update_or_create(
                            property_id=cont["ads_id"],
                            report_date=report_date,
                            conversion_action_name=event_name,
                            defaults={
                                "conversions": count,
                                "extra_json": {
                                    "client_name": cont["client_name"],
                                    "container_id": cont["container_id"],
                                    "stape_client": stape_client,
                                    "ads_id_present": True,
                                },
                            },
                        )
                        saved_rows += 1

        print("---- SAVING STAPE ANALYTICS TO DB ----")
        print("PROCESSED CONTAINERS:", processed_containers)
        print("SAVED/UPDATED ROWS:", saved_rows)

        # 6) OVERVIEW wynik (purchase + events_total)
        print("---- OVERVIEW: PURCHASE + EVENTS_TOTAL PER DAY ----")
        if not overview_per_day:
            print("OVERVIEW RESULT: (empty)")
        else:
            print("OVERVIEW RESULT:")
            for day, data in sorted(overview_per_day.items()):
                print(f"{day} → purchase: {data['purchase']}, events_total: {data['events_total']}")

        print("=== STAPE INGEST END ===")