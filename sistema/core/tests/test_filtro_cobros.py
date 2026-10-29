from datetime import datetime, timedelta, timezone

from django.db.models import Q

from payment_instructions.models import User
from sistema.core.permisos import VENTANA_OPERADOR, filtro_cobros_visibles
from sistema.core.tests import SistemaTestCase

AHORA = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)


def _u(role):
    return User.objects.create_user(f"f-{role}", email=f"{role}@example.com", role=role)


class FiltroCobrosVisiblesTest(SistemaTestCase):
    def test_administrador_ve_todo(self):
        self.assertEqual(filtro_cobros_visibles(_u("administrator"), AHORA), Q())

    def test_operador_ve_los_propios_de_los_ultimos_15_dias(self):
        op = _u("operator")
        esperado = Q(creado_por=op, fecha_alta__gte=AHORA - timedelta(days=15))
        self.assertEqual(filtro_cobros_visibles(op, AHORA), esperado)

    def test_profesional_no_ve_ninguno(self):
        self.assertEqual(filtro_cobros_visibles(_u("professional"), AHORA), Q(pk__in=[]))

    def test_ventana_son_360_horas(self):
        self.assertEqual(VENTANA_OPERADOR, timedelta(hours=360))
