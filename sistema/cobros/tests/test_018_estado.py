"""018/FR-028: ícono de estado de la lista del administrador (`estado_visual`)."""

from datetime import date
from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from payment_instructions.models import User
from sistema.arca.models import ComprobanteFiscal
from sistema.arca.tests.fabricas import factura_prueba
from sistema.cobros import servicios
from sistema.cobros.models import Cobro, ItemCobro
from sistema.cobros.views import _anotadas
from sistema.core.tests import SistemaTestCase
from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.dentalink.tests import cobro_con_items
from sistema.destinatarios.models import Cuenta, Destinatario


def factura(cobro, **campos):
    """Factura sin pasar por ARCA: el estado se fija con `update` (las constraints piden datos)."""
    estado = campos.pop("estado", "pendiente")
    f = factura_prueba(cobro, **campos)
    extra = {}
    if estado == "emitido":
        extra = dict(numero=1, cae="1" * 14, cae_vence=date(2026, 12, 1), fecha=date(2026, 10, 1))
        extra["total"] = Decimal("150.00")
    if estado == "error":
        extra = {"motivo": "Error de prueba."}
    ComprobanteFiscal.objects.filter(pk=f.pk).update(estado=estado, **extra)
    return f


def visual(cobro):
    c = _anotadas(Cobro.objects.filter(pk=cobro.pk)).prefetch_related("items").get()
    return servicios.estado_visual(c)


class EstadoVisualTest(SistemaTestCase):
    def cobro(self, envio="enviado", **campos):
        cobro = cobro_con_items(["100.00", "50.00"], **{"estado": "finalizado", **campos})
        ItemCobro.objects.filter(cobro=cobro, numero=1).update(estado_envio=envio)
        ItemCobro.objects.filter(cobro=cobro, numero=2).update(estado_envio="enviado")
        return cobro

    def test_finalizado_con_items_enviados_es_verde_con_un_motivo(self):
        self.assertEqual(visual(self.cobro()), ("verde", ["Finalizado."]))

    def test_items_ajustado_historico_y_cancelado_no_generan_motivo(self):
        for envio in ("ajustado", "historico", "cancelado"):
            with self.subTest(envio=envio):
                self.assertEqual(visual(self.cobro(envio)), ("verde", ["Finalizado."]))

    def test_pendiente_es_amarillo(self):
        c = self.cobro(estado="pendiente")
        self.assertEqual(visual(c), ("amarillo", ["Pendiente: espera el comprobante y toma cupo."]))

    def test_pendiente_con_excedente_sin_revisar_tiene_dos_motivos(self):
        c = self.cobro(estado="pendiente")
        Cobro.objects.filter(pk=c.pk).update(excedente=True)
        nivel, motivos = visual(c)
        self.assertEqual(nivel, "amarillo")
        self.assertEqual(len(motivos), 2)
        self.assertIn("Excedente sin revisar.", motivos)

    def test_excedente_revisado_no_genera_motivo(self):
        c = self.cobro()
        Cobro.objects.filter(pk=c.pk).update(excedente=True, revisado=True)
        self.assertEqual(visual(c), ("verde", ["Finalizado."]))

    def test_envio_pendiente_y_revisar_son_amarillos(self):
        self.assertEqual(
            visual(self.cobro("pendiente")), ("amarillo", ["Envío a Dentalink pendiente."])
        )
        self.assertEqual(visual(self.cobro("revisar")), ("amarillo", ["Revisar en Dentalink."]))

    def test_sin_vincular_finalizado_en_efectivo_es_amarillo(self):
        c = cobro_prueba(
            cuenta=Cuenta.objects.create(
                destinatario=Destinatario.objects.create(nombre="Clínica Ejemplo", tipo="clinica"),
                tipo="efectivo",
            ),
            metodo="efectivo",
            estado="finalizado",
            monto=Decimal("100.00"),
            faltan_items=True,
        )
        self.assertEqual(visual(c), ("amarillo", ["Sin vincular a Dentalink."]))

    def test_factura_a_revisar_y_pendiente_son_amarillas(self):
        c = self.cobro()
        factura(c, estado="pendiente", a_revisar=True)
        self.assertEqual(
            visual(c), ("amarillo", ["Factura a revisar.", "Factura pendiente de emisión."])
        )

    def test_factura_emitida_o_descartada_no_genera_motivo(self):
        for estado in ("emitido", "descartado"):
            with self.subTest(estado=estado):
                c = self.cobro()
                factura(c, estado=estado)
                self.assertEqual(visual(c), ("verde", ["Finalizado."]))

    def test_error_y_requiere_ajuste_son_rojos(self):
        self.assertEqual(visual(self.cobro("error")), ("rojo", ["Error al enviar a Dentalink."]))
        self.assertEqual(
            visual(self.cobro("requiere_ajuste")),
            ("rojo", ["Requiere ajuste manual en Dentalink."]),
        )

    def test_factura_con_error_es_roja(self):
        c = self.cobro()
        factura(c, estado="error")
        self.assertEqual(visual(c), ("rojo", ["Factura con error."]))

    def test_peor_caso_gana_y_lista_todos_los_motivos(self):
        c = self.cobro("error")
        factura(c, estado="emitido", a_revisar=True)
        self.assertEqual(
            visual(c), ("rojo", ["Error al enviar a Dentalink.", "Factura a revisar."])
        )

    def test_cancelado_es_gris_con_un_motivo(self):
        c = self.cobro("cancelado", estado="cancelado")
        self.assertEqual(visual(c), ("gris", ["Cancelado."]))

    def test_cancelado_con_excedente_sin_revisar_sigue_gris(self):
        c = self.cobro(estado="cancelado")
        Cobro.objects.filter(pk=c.pk).update(excedente=True)
        self.assertEqual(visual(c), ("gris", ["Cancelado."]))

    def test_cancelado_con_factura_con_error_es_rojo_con_dos_motivos(self):
        c = self.cobro(estado="cancelado")
        factura(c, estado="error")
        self.assertEqual(visual(c), ("rojo", ["Factura con error.", "Cancelado."]))

    def test_veinte_cobros_no_hacen_consultas_por_fila(self):
        for _ in range(20):
            self.cobro("error")
        cobros = list(_anotadas(Cobro.objects.all()).prefetch_related("items"))
        self.assertEqual(len(cobros), 20)
        with self.assertNumQueries(0):
            for c in cobros:
                servicios.estado_visual(c)


class ConsultasListaTest(SistemaTestCase):
    def test_la_lista_hace_las_mismas_consultas_con_5_o_con_50_filas(self):
        self.client.force_login(User.objects.create(username="adm", role="administrator"))

        def consultas():
            with CaptureQueriesContext(connection) as q:
                self.client.get(reverse("cobros:lista"))
            return len(q)

        for _ in range(5):
            cobro_con_items(["100.00"])
        con_5 = consultas()
        for _ in range(45):
            cobro_con_items(["100.00"])
        self.assertEqual(consultas(), con_5)
