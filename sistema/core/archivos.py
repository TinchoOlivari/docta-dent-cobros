import mimetypes
from urllib.parse import quote

from django.http import FileResponse, HttpResponse


def servir(request, archivo):
    """Entrega un archivo subido después del control de permiso de la vista (020/FR-025).

    Si nginx anuncia `X-Sendfile-Type: X-Accel-Redirect`, responde vacío y nginx sirve el archivo
    desde `/media/` (interna). Si no (local, tests), lo sirve Django.
    """
    if request.headers.get("X-Sendfile-Type") == "X-Accel-Redirect":
        respuesta = HttpResponse(
            content_type=mimetypes.guess_type(archivo.name)[0] or "application/octet-stream"
        )
        respuesta["X-Accel-Redirect"] = "/media/" + quote(archivo.name)
        return respuesta
    return FileResponse(archivo.open("rb"))
