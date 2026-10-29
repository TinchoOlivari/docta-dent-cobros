"""
URL configuration for payment_system project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import redirect
from django.views.generic import RedirectView, TemplateView


def redirect_to_home(request, path=""):
    return redirect("/")


def health(request):
    """Devuelve 200 si Django arrancó y la base responde.

    El deploy usa esta vista para decidir si el arranque salió bien.
    Sin base, devuelve 503: el sitio está en pie pero no sirve.
    """
    try:
        connection.ensure_connection()
    except Exception:
        return JsonResponse({"status": "error", "database": "down"}, status=503)
    return JsonResponse({"status": "ok"})


urlpatterns = [
    # Antes del catch-all de abajo: `/health/` termina en barra y caería ahí.
    path("health/", health),
    path(
        "robots.txt",
        TemplateView.as_view(template_name="robots.txt", content_type="text/plain"),
    ),
    path("sistema/", include("sistema.urls")),
    path("", RedirectView.as_view(pattern_name="usuarios:inicio", permanent=False)),
    # Catch-all pattern for misinterpreted URLs - redirect to home
    re_path(r"^(?!sistema/|media/).*/$", redirect_to_home),
] + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

# Herramientas de desarrollo (016): solo existen si local.py las activó.
if "debug_toolbar" in settings.INSTALLED_APPS:
    urlpatterns = [path("__debug__/", include("debug_toolbar.urls"))] + urlpatterns
if "django_browser_reload" in settings.INSTALLED_APPS:
    urlpatterns = [path("__reload__/", include("django_browser_reload.urls"))] + urlpatterns
