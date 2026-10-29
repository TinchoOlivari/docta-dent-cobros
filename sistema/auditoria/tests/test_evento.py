from datetime import UTC, date, datetime

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, connection, transaction

from sistema.auditoria.models import Evento
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import inicio_dia_arg


class EventoInmutableTests(SistemaTestCase):
    def setUp(self):
        self.evento = Evento.objects.create(
            tipo="alta", entidad="destinatarios.proveedor", entidad_id=1
        )

    def test_crear_funciona(self):
        self.assertEqual(Evento.objects.count(), 1)

    def test_segundo_save_falla(self):
        with self.assertRaises(PermissionDenied):
            self.evento.save()

    def test_delete_de_instancia_falla(self):
        with self.assertRaises(PermissionDenied):
            self.evento.delete()

    def test_update_de_queryset_falla(self):
        with self.assertRaises(PermissionDenied):
            Evento.objects.update(detalle="x")

    def test_delete_de_queryset_falla(self):
        with self.assertRaises(PermissionDenied):
            Evento.objects.all().delete()

    def test_sql_crudo_update_lo_frena_el_trigger(self):
        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as c:
                c.execute("UPDATE auditoria_evento SET detalle='x'")

    def test_sql_crudo_delete_lo_frena_el_trigger(self):
        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as c:
                c.execute("DELETE FROM auditoria_evento")

    def test_no_esta_en_el_admin(self):
        self.assertNotIn(Evento, admin.site._registry)


class TiempoTests(SistemaTestCase):
    def test_inicio_dia_arg_es_03_utc(self):
        self.assertEqual(
            inicio_dia_arg(date(2026, 10, 31)), datetime(2026, 10, 31, 3, 0, tzinfo=UTC)
        )
