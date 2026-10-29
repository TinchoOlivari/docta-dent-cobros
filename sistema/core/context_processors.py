from django.conf import settings


def ambiente(request):
    """Expone si el ambiente es staging (017/FR-002) y la web de Dentalink (024/FR-020)."""
    return {
        "is_staging": settings.ENVIRONMENT == "staging",
        "dentalink_web": settings.DENTALINK_WEB,
    }
