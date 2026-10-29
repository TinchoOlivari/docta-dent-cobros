from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ARG = ZoneInfo("America/Argentina/Buenos_Aires")


def inicio_dia_arg(fecha: date) -> datetime:
    return datetime.combine(fecha, time.min, tzinfo=ARG)


def rango_mes_arg(momento: datetime) -> tuple[datetime, datetime]:
    """Mes de `momento` en hora Argentina. `hasta` es exclusivo."""
    local = momento.astimezone(ARG)
    anio, mes = (local.year + 1, 1) if local.month == 12 else (local.year, local.month + 1)
    return datetime(local.year, local.month, 1, tzinfo=ARG), datetime(anio, mes, 1, tzinfo=ARG)


def rango_semana_arg(momento: datetime) -> tuple[datetime, datetime]:
    """Semana lunes a domingo de `momento` en hora Argentina. `hasta` es exclusivo."""
    local = momento.astimezone(ARG)
    lunes = local.date() - timedelta(days=local.weekday())
    return inicio_dia_arg(lunes), inicio_dia_arg(lunes + timedelta(days=7))
