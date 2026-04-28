from django.core.management.base import BaseCommand
from dashboard.models import SstMetric
from datetime import date, datetime
import pandas as pd
import os
import math


SHEET_CSV_URL = os.getenv(
    "SHEET_CSV_URL",
    "https://docs.google.com/spreadsheets/d/1_1gVqt0LkFzVvdydOdMoHBjFlAa5W58I4IAKFlgYuRE/export?format=csv&gid=119022929"
)


def to_float(value):
    if value is None:
        return None
    try:
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)
    except Exception:
        return None
    
def to_float_or_zero(x):
    v = to_float(x)
    return 0.0 if v is None else v



def json_safe(value):
    """
    Zamienia wartości nieobsługiwane przez JSON na string
    (date, datetime, NaN).
    """
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if value is None:
        return None
    try:
        if isinstance(value, float) and math.isnan(value):
            return None
    except Exception:
        pass
    return value

def is_blank(x):
    """True jeśli None/NaN/pusty string/'nan'."""
    if x is None:
        return True
    try:
        if isinstance(x, float) and math.isnan(x):
            return True
    except Exception:
        pass
    s = str(x).strip().lower()
    return s == "" or s == "nan" or s == "none"

class Command(BaseCommand):
    help = "Ingest SST metrics from Google Sheet into Postgres"

    def handle(self, *args, **options):
        self.stdout.write("📥 Start ingest SST from Sheet")

        df = pd.read_csv(SHEET_CSV_URL)

        # report_date → date
        if "report_date" in df.columns:
            df["report_date"] = pd.to_datetime(
                df["report_date"], errors="coerce"
            ).dt.date

        inserted = 0
        updated = 0
        skipped = 0

        for _, row in df.iterrows():
      
            property_id_raw = row.get("property_id")
            conversion_action_raw = row.get("conversion_action_name")
            report_date = row.get("report_date")
            property_id = str(property_id_raw).strip()
            conversion_action_name = str(conversion_action_raw).strip()

            if is_blank(property_id) or not report_date or is_blank(conversion_action_name):
                skipped += 1
                continue
            external_conversion_source = row.get("external_conversion_source")

            # 1:1 surowy backup
            extra_json = {
                k: json_safe(v)
                for k, v in row.to_dict().items()
            }

            obj, created = SstMetric.objects.update_or_create(
                property_id=property_id,
                report_date=report_date,
                conversion_action_name=conversion_action_name,
                external_conversion_source=external_conversion_source,
                defaults={
                    # ✅ LICZBY (NAJWAŻNIEJSZE)
                    "conversions": to_float_or_zero(row.get("conversions")),
                    "all_conversions": to_float(row.get("all_conversions")),

                    # ✅ WARTOŚCI (UWAGA NA NAZWY!)
                    # Excel: conversions_value
                    # Model: conversion_value
                    "conversion_value": to_float(row.get("conversions_value")),

                    # Excel: all_conversions_value
                    # Model: all_conversion_value  (bez „s”!)
                    "all_conversion_value": to_float(row.get("all_conversions_value")),

                    # ✅ META
                    "conversion_action_category": row.get("conversion_action_category"),
                    "external_conversion_source": row.get("external_conversion_source"),

                    # ✅ RAW BACKUP (DEBUG)
                    "extra_json": extra_json,
                },
            )

            if created:
                inserted += 1
            else:
                updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"✅ Ingest done: inserted={inserted}, updated={updated}, skipped={skipped}"
            )
        )