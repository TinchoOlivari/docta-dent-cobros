from django.conf import settings


def environment(request):
    """Expone si el ambiente actual es staging, para mostrar el aviso visual."""
    return {"is_staging": settings.ENVIRONMENT == "staging"}
