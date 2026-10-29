from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from sistema.auditoria.servicios import historial_de
from sistema.cobros import servicios
from sistema.cobros.models import Cobro
from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.cobros.tests.test_comprobante import imagen
from sistema.cobros.tests.test_transferencia import BaseTransferencia
from sistema.destinatarios.models import Cuenta

CBU = "0000000000000000000001"
CUIT = "20000000001"


def _nbsp(texto):
    return texto.replace("\xa0", " ")


class Base017(BaseTransferencia):
    def setUp(self):
        super().setUp()
        Cuenta.objects.filter(pk=self.cuenta.pk).update(
            cbu=CBU, titular="Titular Ejemplo", titular_cuit=CUIT
        )
        self.cuenta.refresh_from_db()
        self.prof = get_user_model().objects.create_user(
            "pro", email="pro@example.com", role="specialist"
        )

    def cobro(self, usuario=None, **campos):
        campos.setdefault("cuenta", self.cuenta)
        campos.setdefault("destinatario", self.dest)
        campos.setdefault("monto", Decimal("1000.00"))
        campos.setdefault("nombre_paciente", "Paciente Ejemplo")
        return cobro_prueba(creado_por=usuario or self.ana, **campos)

    def get_detalle(self, cobro, usuario=None):
        self.client.force_login(usuario or self.ana)
        return self.client.get(reverse("cobros:detalle", args=[cobro.pk]))


class DetalleOperadorTest(Base017):
    def test_transferencia_propia_muestra_titular_cuit_alias_y_cbu(self):
        r = self.get_detalle(self.cobro())
        self.assertContains(r, "Titular Ejemplo")
        self.assertContains(r, ">CUIT o CUIL<", count=1)
        self.assertContains(r, CUIT)
        self.assertContains(r, "prueba.uno")
        self.assertContains(r, CBU)

    def test_cobro_qr_propio_no_muestra_datos_de_cuenta(self):
        qr = self.cobro(metodo="qr", estado="finalizado")
        r = self.get_detalle(qr)
        self.assertNotContains(r, "Cuenta destino")
        self.assertNotContains(r, "Titular Ejemplo")
        self.assertNotContains(r, "Copiar todo")

    def test_profesional_es_403(self):
        self.assertEqual(self.get_detalle(self.cobro(), self.prof).status_code, 403)

    def test_cobro_de_otra_operadora_es_404(self):
        otra = get_user_model().objects.create_user("otra", email="o@example.com", role="operator")
        self.assertEqual(self.get_detalle(self.cobro(otra)).status_code, 404)


class ObservacionesTest(Base017):
    def adjuntar(self, cobro, **extra):
        self.client.force_login(self.ana)
        datos = {"comprobante": imagen(), "estado_visto": cobro.estado, **extra}
        return self.client.post(reverse("cobros:adjuntar", args=[cobro.pk]), datos)

    def ultimo(self, cobro):
        return historial_de(cobro).filter(tipo="cambio").order_by("-pk").first()

    def test_adjuntar_con_observaciones_nuevas_las_guarda_y_audita(self):
        cobro = self.cobro(observaciones="Paciente nuevo")
        self.adjuntar(cobro, observaciones=" Transfirió el hermano ")
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, "finalizado")
        self.assertEqual(cobro.observaciones, "Transfirió el hermano")
        cambio = next(c for c in self.ultimo(cobro).cambios if c["campo"] == "observaciones")
        self.assertEqual(cambio["anterior"], "Paciente nuevo")
        self.assertEqual(cambio["nuevo"], "Transfirió el hermano")

    def test_sin_el_campo_las_observaciones_no_cambian(self):
        cobro = self.cobro(observaciones="Paciente nuevo")
        self.adjuntar(cobro)
        cobro.refresh_from_db()
        self.assertEqual(cobro.observaciones, "Paciente nuevo")
        self.assertNotIn("observaciones", [c["campo"] for c in self.ultimo(cobro).cambios])

    def test_mismo_texto_no_figura_en_el_evento(self):
        cobro = self.cobro(observaciones="Paciente nuevo")
        self.adjuntar(cobro, observaciones="Paciente nuevo")
        self.assertNotIn("observaciones", [c["campo"] for c in self.ultimo(cobro).cambios])

    def test_pendiente_de_hace_16_dias_es_404(self):
        cobro = self.cobro(observaciones="Paciente nuevo")
        Cobro.objects.filter(pk=cobro.pk).update(fecha_alta=timezone.now() - timedelta(days=16))
        self.assertEqual(self.adjuntar(cobro, observaciones="x").status_code, 404)

    def test_pendiente_de_hace_15_dias_menos_un_minuto_se_ve(self):
        cobro = self.cobro()
        Cobro.objects.filter(pk=cobro.pk).update(
            fecha_alta=timezone.now() - timedelta(days=15) + timedelta(minutes=1)
        )
        self.assertEqual(self.get_detalle(cobro).status_code, 200)

    def test_operador_sobre_finalizado_propio_da_error_y_no_cambia(self):
        cobro = self.cobro(observaciones="Paciente nuevo", estado="finalizado")
        r = self.adjuntar(cobro, observaciones="Otro texto")
        self.assertEqual(r.status_code, 302)
        cobro.refresh_from_db()
        self.assertEqual(cobro.observaciones, "Paciente nuevo")

    def test_servicio_admin_agrega_a_finalizado_y_guarda_observaciones(self):
        cobro = self.cobro(observaciones="a", estado="finalizado")
        servicios.adjuntar_comprobante(
            cobro, imagen(), usuario=self.admin, estado_visto="finalizado", observaciones="b"
        )
        cobro.refresh_from_db()
        self.assertEqual(cobro.observaciones, "b")


class ListaOperadorTest(Base017):
    def lista(self, usuario=None, **q):
        self.client.force_login(usuario or self.ana)
        return self.client.get(reverse("cobros:lista"), q)

    def test_busqueda_por_paciente_sin_acentos_estrictos(self):
        for n in range(28):
            self.cobro(nombre_paciente=f"Persona{n} Ejemplo")
        for _ in range(2):
            self.cobro(nombre_paciente="Gómez Ejemplo")
        r = self.lista(q="góm")
        self.assertEqual(len(r.context["pagina"]), 2)

    def test_busqueda_por_numero_de_otra_operadora_no_devuelve(self):
        otra = get_user_model().objects.create_user("otra", email="o@example.com", role="operator")
        ajeno = self.cobro(otra)
        self.cobro()
        self.assertEqual(len(self.lista(q=str(ajeno.pk)).context["pagina"]), 0)

    def test_busqueda_por_numero_propio(self):
        propio = self.cobro()
        self.cobro()
        self.assertEqual(len(self.lista(q=str(propio.pk)).context["pagina"]), 1)

    def test_metodo_se_ignora(self):
        self.cobro()
        self.cobro(metodo="efectivo", cuenta=self.efectivo, destinatario=None, estado="finalizado")
        self.assertEqual(len(self.lista(metodo="efectivo").context["pagina"]), 2)

    def test_estado_finalizado_se_ignora(self):
        self.cobro()
        self.cobro(estado="finalizado")
        self.assertEqual(len(self.lista(estado="finalizado").context["pagina"]), 2)

    def test_pendiente_de_hace_16_dias_no_aparece(self):
        c = self.cobro()
        Cobro.objects.filter(pk=c.pk).update(fecha_alta=timezone.now() - timedelta(days=16))
        self.assertEqual(len(self.lista(estado="pendiente").context["pagina"]), 0)

    def test_profesional_es_403(self):
        self.assertEqual(self.lista(self.prof).status_code, 403)
