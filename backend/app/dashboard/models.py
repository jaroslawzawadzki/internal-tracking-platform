from django.db import models

class ObservedClient(models.Model):
    property_id = models.CharField(max_length=32, unique=True)
    client_name = models.CharField(max_length=255)
    source = models.CharField(max_length=32)  # sheet / stape / manual
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.client_name} ({self.property_id})"



class SstMetric(models.Model):
    property_id = models.CharField(max_length=32)
    report_date = models.DateField()

    # kluczowe z Sheeta (do filtrowania/monitoringu)
    conversion_action_name = models.CharField(max_length=255)
    conversion_action_category = models.CharField(max_length=255, null=True, blank=True)
    external_conversion_source = models.CharField(max_length=255, null=True, blank=True)

    # metryki
    conversions = models.FloatField()
    all_conversions = models.FloatField(null=True, blank=True)
    conversion_value = models.FloatField(null=True, blank=True)
    all_conversion_value = models.FloatField(null=True, blank=True)

    # wszystko inne z Sheeta (1:1)
    extra_json = models.JSONField(default=dict, blank=True)

    loaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = (
            "property_id",
            "report_date",
            "conversion_action_name",
            "external_conversion_source",
        )

    def __str__(self):
        return f"{self.property_id} | {self.report_date} | {self.conversion_action_name}"

class TrackerLibrary(models.Model):
    domain = models.CharField(max_length=255, unique=True)
    name = models.CharField(max_length=255)
    description_pl = models.TextField(blank=True)
    description_en = models.TextField(blank=True)
    description_de = models.TextField(blank=True)
    category = models.CharField(max_length=100, blank=True)

    def __str__(self):
            return self.domain
    
class AuditReport(models.Model):
    url = models.URLField()
    timestamp = models.DateTimeField(auto_now_add=True)
    # Pole JSON jest najlepsze, bo Playwright zwraca nam skomplikowane struktury
    raw_results = models.JSONField() 

    def __str__(self):
        return f"Raport {self.url} - {self.timestamp}"