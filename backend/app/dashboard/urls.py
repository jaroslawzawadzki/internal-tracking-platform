from django.contrib import admin # Zostawiamy TYLKO to
from django.urls import path
from . import views

# Importujemy widoki
from .views import (
    dashboard_view,
    stape_overview_debug,
    stape_overview_view,
    dashboard_overview_view,
    client_conversions_view,
    client_sums_view,
    privacy_audit_view
)




urlpatterns = [
    path('audit/', privacy_audit_view, name='privacy_audit'),
    path('', dashboard_view, name='dashboard'),
    path('export-pdf/', views.export_pdf, name='export_pdf'),
     #path("stape/debug/", stape_overview_debug),
    path("stape/", stape_overview_view, name="stape_overview"),
    path("overview/", dashboard_overview_view, name="dashboard_overview"),
    path("client/<str:ads_id>/conversions/", client_conversions_view, name="client_conversions"),
    path("client/<str:ads_id>/conversions/", client_conversions_view, name="client_conversions"),
    path("client/<str:ads_id>/sums/", client_sums_view, name="client_sums"),
    # Wewnątrz urlpatterns = [ ... ]
    
]
