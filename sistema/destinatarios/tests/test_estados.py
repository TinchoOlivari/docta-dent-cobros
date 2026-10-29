from decimal import Decimal
from unittest import mock

from django.core.exceptions import ValidationError
from django.urls import reverse

from payment_instructions.models import User
from sistema.core.tests import SistemaTestCase
from sistema.destinatarios import servicios
from sistema.destinatarios.models import Cuenta, Destinatario
from sistema.destinatarios.servicios import (
    cambiar_estado_cuenta,
    cambiar_estado_destinatario,
    crear_cuenta,
    crear_destinatario,
    desactivar_cuentas,
)

D = Decimal
ESTADOS_D = ["activo", "inactivo", "archivado"]
ESTADOS_C = ["activa", "inactiva", "archivada"]


class EstadosTest(SistemaTestCase):
    def setUp(self):
        self.d = crear_destinatario(usuario=None, nombre="Ana Test", tipo="profesional")

    def cuenta(self, n=1, **kw):
        return crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=self.d,
            alias=f"ana.test{n}",
            tope_mensual=D("500000.00"),
            **kw,
        )

    def test_las_6_transiciones_de_destinatario(self):
        for origen in ESTADOS_D:
            for destino in ESTADOS_D:
                if origen == destino:
                    continue
                Destinatario.objects.filter(pk=self.d.pk).update(estado=origen)
                d = cambiar_estado_destinatario(self.d, destino, usuario=None)
                self.assertEqual(Destinatario.objects.get(pk=d.pk).estado, destino)

    def test_las_6_transiciones_de_cuenta(self):
        c = self.cuenta()
        for origen in ESTADOS_C:
            for destino in ESTADOS_C:
                if origen == destino:
                    continue
                Cuenta.objects.filter(pk=c.pk).update(estado=origen, posicion=None)
                cambiar_estado_cuenta(c, destino, usuario=None)
                self.assertEqual(Cuenta.objects.get(pk=c.pk).estado, destino)

    def test_fechas_de_activacion_y_archivado(self):
        c = self.cuenta(estado="inactiva")
        self.assertIsNone(c.fecha_activacion)
        c = cambiar_estado_cuenta(c, "activa", usuario=None)
        self.assertIsNotNone(c.fecha_activacion)
        self.assertIsNone(c.fecha_archivado)
        c = cambiar_estado_cuenta(c, "archivada", usuario=None)
        self.assertIsNotNone(c.fecha_archivado)
        d = cambiar_estado_destinatario(self.d, "archivado", usuario=None)
        self.assertIsNotNone(d.fecha_archivado)

    def test_mismo_estado_no_cambia_fecha_activacion(self):
        fecha = self.d.fecha_activacion
        d = cambiar_estado_destinatario(self.d, "activo", usuario=None)
        self.assertEqual(d.fecha_activacion, fecha)
        c = self.cuenta()
        fecha = c.fecha_activacion
        c = cambiar_estado_cuenta(c, "activa", usuario=None)
        self.assertEqual(c.fecha_activacion, fecha)

    def test_estado_invalido_rechazado(self):
        with self.assertRaises(ValidationError):
            cambiar_estado_destinatario(self.d, "borrado", usuario=None)

    def test_us2_1_destinatario_inactivo_saca_cuentas_de_habilitadas(self):
        c = self.cuenta()
        cambiar_estado_destinatario(self.d, "inactivo", usuario=None)
        self.assertNotIn(c, Cuenta.objects.habilitadas())
        self.assertIn(self.d, Destinatario.objects.visibles())

    def test_us2_2_archivado_vuelve_a_activo(self):
        c = self.cuenta()
        cambiar_estado_destinatario(self.d, "archivado", usuario=None)
        cambiar_estado_destinatario(self.d, "activo", usuario=None)
        self.assertIn(c, Cuenta.objects.habilitadas())

    def test_us2_3_archivado_no_esta_en_visibles(self):
        cambiar_estado_destinatario(self.d, "archivado", usuario=None)
        self.assertNotIn(self.d, Destinatario.objects.visibles())

    def test_us2_4_cuenta_inactiva_fuera_de_habilitadas(self):
        c = self.cuenta()
        cambiar_estado_cuenta(c, "inactiva", usuario=None)
        self.assertNotIn(c, Cuenta.objects.habilitadas())

    def test_archivado_conserva_estado_de_cuentas_activas(self):
        c = self.cuenta()
        cambiar_estado_destinatario(self.d, "archivado", usuario=None)
        self.assertEqual(Cuenta.objects.get(pk=c.pk).estado, "activa")
        self.assertNotIn(c, Cuenta.objects.habilitadas())

    def test_segunda_efectivo_activa_por_cambio_de_estado_rechazada(self):
        crear_cuenta(usuario=None, tipo="efectivo", destinatario=self.d)
        otra = crear_cuenta(usuario=None, tipo="efectivo", destinatario=self.d, estado="inactiva")
        with self.assertRaises(ValidationError) as cm:
            cambiar_estado_cuenta(otra, "activa", usuario=None)
        self.assertIn("Ya hay una cuenta de efectivo activa.", cm.exception.messages)

    def test_us2_5_desactivar_todas(self):
        for n in range(3):
            self.cuenta(n)
        self.assertEqual(desactivar_cuentas(self.d, usuario=None), 3)
        self.assertEqual(self.d.cuentas.filter(estado="inactiva").count(), 3)

    def test_desactivar_sin_cuentas_activas_devuelve_cero(self):
        self.cuenta(estado="inactiva")
        self.assertEqual(desactivar_cuentas(self.d, usuario=None), 0)

    def test_desactivar_todas_revierte_si_una_falla(self):
        for n in range(3):
            self.cuenta(n)
        real = servicios.cambiar_estado_cuenta
        llamadas = []

        def falla_en_la_segunda(cuenta, estado, *, usuario):
            llamadas.append(cuenta.pk)
            if len(llamadas) == 2:
                raise ValidationError("falla forzada")
            return real(cuenta, estado, usuario=usuario)

        with mock.patch.object(servicios, "cambiar_estado_cuenta", falla_en_la_segunda):
            with self.assertRaises(ValidationError):
                desactivar_cuentas(self.d, usuario=None)
        self.assertEqual(self.d.cuentas.filter(estado="activa").count(), 3)


class EstadosPantallaTest(SistemaTestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            "adm", email="adm@example.com", password="x", role="administrator"
        )
        self.d = crear_destinatario(usuario=None, nombre="Ana Test", tipo="profesional")
        self.c = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=self.d,
            alias="ana.test",
            tope_mensual=D("500000.00"),
        )
        self.client.force_login(self.admin)

    def test_post_cambia_estado_de_destinatario_y_de_cuenta(self):
        self.client.post(
            reverse("destinatarios:estado_destinatario", args=[self.d.pk]), {"estado": "inactivo"}
        )
        self.client.post(
            reverse("destinatarios:estado_cuenta", args=[self.c.pk]), {"estado": "archivada"}
        )
        self.assertEqual(Destinatario.objects.get(pk=self.d.pk).estado, "inactivo")
        self.assertEqual(Cuenta.objects.get(pk=self.c.pk).estado, "archivada")

    def test_desactivar_todas_muestra_mensajes(self):
        url = reverse("destinatarios:desactivar_cuentas", args=[self.d.pk])
        r = self.client.post(url, follow=True)
        self.assertContains(r, "Se desactivaron 1 cuentas.")
        r = self.client.post(url, follow=True)
        self.assertContains(r, "El destinatario no tiene cuentas activas.")


class FechaArchivadoAlAltaTest(SistemaTestCase):
    def test_destinatario_archivado_al_nacer_tiene_fecha_archivado(self):
        d = crear_destinatario(
            usuario=None, nombre="Ana Test", tipo="profesional", estado="archivado"
        )
        self.assertIsNotNone(d.fecha_archivado)
        self.assertIsNone(d.fecha_activacion)

    def test_cuenta_archivada_al_nacer_tiene_fecha_archivado(self):
        d = crear_destinatario(usuario=None, nombre="Ana Test", tipo="profesional")
        c = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=d,
            alias="ana.test1",
            tope_mensual=D("500000.00"),
            estado="archivada",
        )
        self.assertIsNotNone(c.fecha_archivado)
        self.assertIsNone(c.fecha_activacion)

    def test_destinatario_activo_al_nacer_no_tiene_fecha_archivado(self):
        d = crear_destinatario(usuario=None, nombre="Ana Test", tipo="profesional")
        self.assertIsNone(d.fecha_archivado)
