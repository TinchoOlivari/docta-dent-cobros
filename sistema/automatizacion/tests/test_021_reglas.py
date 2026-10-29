"""021 fase D: día y posición (FR-044), choques al activar (FR-043), cuentas visibles (FR-033),
textos de acción (FR-047) y búsqueda sin acentos (FR-022)."""

from django.urls import reverse

from sistema.destinatarios.servicios import cambiar_estado_destinatario, crear_cuenta

from ..forms import ReglaForm
from ..models import Regla
from . import ReglasTestCase


class DatosMixin:
    def datos(self, **extra):
        d = {
            "nombre": "Regla",
            "cuenta": self.jimena.pk,
            "estado": "activa",
            "disparador": "mes",
            "dia_mes": "1",
            "hora": "08:00",
            "accion": "primero",
        }
        return d | extra

    def form(self, **extra):
        return ReglaForm(self.datos(**extra))


class DiaDelMesTest(DatosMixin, ReglasTestCase):
    MENSAJE = "El día debe ir de 1 a 31."

    def test_dia_fuera_de_rango_o_no_numerico_da_un_solo_mensaje(self):
        for dia in ("0", "32", "-1", "abc"):
            with self.subTest(dia=dia):
                f = self.form(dia_mes=dia)
                self.assertFalse(f.is_valid())
                self.assertEqual(f.errors["dia_mes"], [self.MENSAJE])

    def test_dia_1_y_31_son_validos(self):
        for dia in ("1", "31"):
            with self.subTest(dia=dia):
                f = self.form(dia_mes=dia)
                self.assertTrue(f.is_valid(), f.errors)

    def test_el_error_sale_bajo_el_campo(self):
        r = self.client_admin().post(reverse("automatizacion:nueva"), self.datos(dia_mes="32"))
        self.assertContains(r, self.MENSAJE, count=1)

    def client_admin(self):
        self.client.force_login(self.admin)
        return self.client


class HoraTest(DatosMixin, ReglasTestCase):
    def test_hora_invalida_da_el_mensaje_de_formato(self):
        for hora in ("25:00", "8", "abc"):
            with self.subTest(hora=hora):
                f = self.form(hora=hora)
                self.assertFalse(f.is_valid())
                self.assertEqual(f.errors["hora"], ["Escribí la hora como HH:MM."])

    def test_00_00_y_23_59_son_validas(self):
        for hora in ("00:00", "23:59"):
            with self.subTest(hora=hora):
                self.assertTrue(self.form(hora=hora).is_valid(), self.form(hora=hora).errors)


class PosicionTest(DatosMixin, ReglasTestCase):
    def posicion(self, valor):
        return self.form(accion="posicion", posicion=valor)

    def test_con_4_cuentas_cero_negativo_y_enorme_dan_el_mensaje_con_maximo(self):
        for valor in ("0", "-2", "99999999999", "5"):
            with self.subTest(valor=valor):
                f = self.posicion(valor)
                self.assertFalse(f.is_valid())
                self.assertEqual(f.errors["posicion"], ["La posición debe ir de 1 a 4."])

    def test_1_y_el_maximo_son_validos(self):
        for valor in ("1", "4"):
            with self.subTest(valor=valor):
                f = self.posicion(valor)
                self.assertTrue(f.is_valid(), f.errors)

    def test_con_5_cuentas_el_maximo_es_5(self):
        crear_cuenta(
            usuario=None, tipo="transferencia", destinatario=self.jimena.destinatario,
            alias="ejemplo.quinta", tope_mensual="1000.00",
        )  # fmt: skip
        self.assertEqual(self.posicion("6").errors["posicion"], ["La posición debe ir de 1 a 5."])
        self.assertTrue(self.posicion("5").is_valid())

    def test_sin_cuentas_en_la_lista_el_mensaje_es_uno_o_mas(self):
        from sistema.destinatarios.models import Cuenta

        Cuenta.objects.update(posicion=None)
        self.assertEqual(self.posicion("0").errors["posicion"], ["La posición debe ser 1 o más."])

    def test_otra_accion_ignora_la_posicion(self):
        f = self.form(accion="primero", posicion="0")
        self.assertTrue(f.is_valid(), f.errors)


class ChoquesAlActivarTest(ReglasTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        self.primero = self.regla(nombre="Lunes primero", estado="inactiva")
        self.uno = self.regla(nombre="Lunes uno", accion="posicion", posicion=1)

    def activar(self, *reglas):
        return self.client.post(
            reverse("automatizacion:estado"),
            {"estado": "activa", "pks": [r.pk for r in reglas]},
            follow=True,
        )

    def test_el_error_nombra_cada_regla_y_no_se_activa_ninguna(self):
        otra = self.regla(nombre="Martes primero", estado="inactiva", dia=2)
        r = self.activar(self.primero, otra)
        self.assertContains(
            r,
            "«Lunes primero» choca con «Lunes uno» (misma cuenta, disparador, día y hora). "
            "No se activó ninguna.",
        )
        self.primero.refresh_from_db()
        otra.refresh_from_db()
        self.assertEqual((self.primero.estado, otra.estado), ("inactiva", "inactiva"))

    def test_dos_reglas_elegidas_que_chocan_entre_si_se_nombran(self):
        self.uno.estado = "inactiva"
        self.uno.save(update_fields=["estado"])
        r = self.activar(self.primero, self.uno)
        self.assertContains(r, "«Lunes uno» choca con «Lunes primero»")
        self.assertEqual(Regla.objects.filter(estado="activa").count(), 0)

    def test_sin_choque_se_activan_todas(self):
        otra = self.regla(nombre="Martes primero", estado="inactiva", dia=2)
        self.client.post(reverse("automatizacion:estado"), {"estado": "activa", "pks": [otra.pk]})
        otra.refresh_from_db()
        self.assertEqual(otra.estado, "activa")


class CuentasVisiblesTest(DatosMixin, ReglasTestCase):
    def opciones(self):
        return ReglaForm().fields["cuenta"].queryset.count()

    def test_destinatario_archivado_con_3_cuentas_no_ofrece_ninguna(self):
        antes = self.opciones()
        dest = self.jimena.destinatario
        extra = [
            crear_cuenta(
                usuario=None,
                tipo="transferencia",
                destinatario=dest,
                alias=f"ejemplo.arch{n}",
                tope_mensual="1000.00",
            )  # fmt: skip
            for n in range(2)
        ]
        self.assertEqual(self.opciones(), antes + 2)
        cambiar_estado_destinatario(dest, "archivado", usuario=self.admin)
        self.assertEqual(self.opciones(), antes - 1)
        self.assertEqual(len(extra), 2)

    def test_cuenta_archivada_no_se_ofrece_pero_la_de_la_regla_se_conserva(self):
        regla = self.regla(cuenta=self.jimena)
        from sistema.destinatarios.models import Cuenta

        Cuenta.objects.filter(pk=self.jimena.pk).update(estado="archivada", posicion=None)
        self.assertNotIn(self.jimena, ReglaForm().fields["cuenta"].queryset)
        self.assertIn(self.jimena, ReglaForm(instance=regla).fields["cuenta"].queryset)


class BusquedaSinAcentosTest(ReglasTestCase):
    def test_busca_por_nombre_sin_tilde(self):
        self.client.force_login(self.admin)
        r = self.regla(nombre="Reunión de mañana")
        resp = self.client.get(reverse("automatizacion:lista"), {"q": "reunion de manana"})
        self.assertEqual([x.pk for x in resp.context["reglas"]], [r.pk])
        resp = self.client.get(reverse("automatizacion:lista"), {"q": "REUNIÓN"})
        self.assertEqual([x.pk for x in resp.context["reglas"]], [r.pk])
