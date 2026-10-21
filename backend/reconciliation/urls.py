# app-level URL routing
from django.urls import path

from . import views

urlpatterns = [
    path('invoices', views.ingest_invoice, name='ingest_invoice'),
    path('reports/summary', views.summary, name='summary'),
    path('healthz', views.healthz, name='healthz'),
    path('readyz', views.readyz, name='readyz'),
    path('metrics', views.metrics, name='metrics'),
]
