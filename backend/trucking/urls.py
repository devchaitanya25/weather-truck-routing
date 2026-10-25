from django.urls import path

from . import views

urlpatterns = [
    path("health/", views.health),
    path("geocode/", views.geocode),
    path("plan/", views.plan_view),
    path("analyze/", views.analyze_view),
]
