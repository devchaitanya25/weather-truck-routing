import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-insecure-key-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",") if h.strip()]

INSTALLED_APPS = ["trucking"]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.middleware.common.CommonMiddleware",
]

# Django's default ("same-origin") strips the Referer on cross-origin requests, and
# OpenStreetMap's tile servers block requests without one.
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# The app is stateless: no database is needed.
DATABASES = {}

# The browser sends the plan plus hourly weather for a few hundred points back to /api/analyze/.
DATA_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024

USE_TZ = True
TIME_ZONE = "UTC"
STATIC_URL = "/static/"

WHITENOISE_ROOT = BASE_DIR / "frontend_dist"
WHITENOISE_INDEX_FILE = True

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# --- Third-party services (all key-free by default) -------------------------
OSRM_BASE_URL = os.environ.get("OSRM_BASE_URL", "https://router.project-osrm.org")
OPEN_METEO_URL = os.environ.get("OPEN_METEO_URL", "https://api.open-meteo.com/v1/forecast")
PHOTON_URL = os.environ.get("PHOTON_URL", "https://photon.komoot.io/api/")
HTTP_USER_AGENT = os.environ.get(
    "HTTP_USER_AGENT", "weather-aware-truck-routing/1.0 (assessment project)"
)

# OSRM only has a car profile; trucks are slower, so scale its durations.
TRUCK_SPEED_FACTOR = float(os.environ.get("TRUCK_SPEED_FACTOR", "1.15"))
