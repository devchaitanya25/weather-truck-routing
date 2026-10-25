from django.urls import path

from . import views

urlpatterns = [
    path("health/", views.health),
    path("geocode/", views.geocode),
    path("analyze/", views.analyze_view),
]
