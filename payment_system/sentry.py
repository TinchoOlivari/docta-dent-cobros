"""Arranque de Sentry.

Vive aparte de los settings para poder testear qué se manda.

Decisión del 12/08/2026: Sentry recibe el contexto completo, sin filtrar.
Eso incluye montos, CBU, alias y nombres de pacientes. Se eligió a
propósito: un error de pago sin los datos que lo produjeron cuesta más
tiempo de diagnóstico que lo que ahorra ocultarlos.

Consecuencia: los eventos de Sentry contienen datos personales de
clientes. Tratá el acceso al panel como acceso a la base.
"""

import os


def init_sentry():
    """Arranca Sentry si hay DSN. Sin DSN no hace nada.

    Devuelve True si arrancó. Así `local` y `ci` quedan mudos sin ninguna
    condición extra en los settings.
    """
    dsn = os.environ.get("SENTRY_DSN", "")
    if not dsn:
        return False

    import sentry_sdk

    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("SENTRY_ENVIRONMENT", "unknown"),
        # Las tres opciones que siguen mandan el contexto completo. Están
        # explícitas para que se vea la decisión, no para que se cambien solas.
        #
        # Variables locales de cada frame del stack trace.
        include_local_variables=True,
        # Cuerpo del request, incluido el POST de un pago.
        max_request_body_size="always",
        # IP y datos del usuario logueado.
        send_default_pii=True,
        # Performance apagado: cuesta cuota y no resuelve un error.
        traces_sample_rate=0.0,
    )
    return True
