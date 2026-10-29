"""024: Nuevo cobro en una vista. Método inicial, transferencia asignada en `cobros:nuevo`."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.urls import reverse

from sistema.cobros.models import Cobro
from sistema.cobros.tests.fabrica import con_elegidas
from sistema.cobros.tests.test_comprobante import imagen
from sistema.cobros.tests.test_transferencia import BaseTransferencia

NUEVO = reverse("cobros:nuevo")
PACIENTE = f"{NUEVO}?paciente=501"


def asignado(pk):
    return f"{NUEVO}?cobro={pk}"


class BaseNuevo(BaseTransferencia):
    """Prestaciones de $40.000 y $25.000; una cuenta de transferencia con tope de $60.000."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.ana)

    def transferir(self, monto_1, monto_2):
        datos = {"metodo": "transferencia", "monto_1": monto_1, "monto_2": monto_2}
        return self.client.post(PACIENTE, con_elegidas(datos))


class MetodoInicialTest(BaseNuevo):
    def test_sin_metodo_en_la_url_arranca_con_efectivo(self):
        r = self.client.get(PACIENTE)
        self.assertEqual(r.context["form"]["metodo"].value(), "efectivo")

    def test_metodo_en_la_url_sigue_mandando(self):
        r = self.client.get(f"{PACIENTE}&metodo=transferencia")
        self.assertEqual(r.context["form"]["metodo"].value(), "transferencia")


class EnlaceDentalinkTest(BaseNuevo):
    """FR-020: el tratamiento abre en la web de Dentalink, en una pestaña nueva."""

    def test_el_tratamiento_enlaza_a_dentalink_con_paciente_e_id(self):
        r = self.client.get(PACIENTE)
        rama = r.context["form"].ramas[0]
        url = f"/pacientes/501/tratamiento/{rama['tratamiento']['id']}"
        self.assertContains(r, url)
        self.assertContains(r, 'target="_blank" rel="noopener"')


class PedidosDentalinkTest(BaseNuevo):
    def test_el_paciente_se_pide_una_sola_vez(self):
        datos = {"id": 501, "nombre": "Ana Ejemplo", "dni": "1", "nacimiento": None}
        with mock.patch("sistema.dentalink.cliente.paciente", return_value=datos) as paciente:
            r = self.client.get(PACIENTE)
        paciente.assert_called_once_with(501)
        self.assertContains(r, "Ana Ejemplo")


class BuscarDestinatarioTest(BaseNuevo):
    """FR-014 y FR-015: la cuenta se asigna en `cobros:nuevo` o no se guarda nada."""

    def test_justo_en_el_tope_asigna_la_cuenta_y_sigue_en_nuevo(self):
        r = self.transferir("40.000,00", "20.000,00")
        cobro = Cobro.objects.get()
        self.assertRedirects(r, asignado(cobro.pk))
        self.assertEqual(cobro.estado, Cobro.Estado.PENDIENTE)
        self.assertEqual(cobro.cuenta, self.cuenta)

    def test_la_vista_asignada_muestra_el_alias_y_queda_igual_al_recargar(self):
        self.transferir("40.000,00", "10.000,00")
        cobro = Cobro.objects.get()
        for _ in range(2):
            r = self.client.get(asignado(cobro.pk))
            self.assertContains(r, "prueba.uno")
            self.assertContains(r, "Destinatario asignado")

    def test_un_centavo_arriba_del_tope_no_guarda_y_lo_dice_la_caja(self):
        r = self.transferir("40.000,00", "20.000,01")
        self.assertEqual(Cobro.objects.count(), 0)
        self.assertContains(r, "No hay destinatario con cupo para $60.000,01.")
        self.assertContains(r, "El cobro no se guardó.")

    def test_sin_cupo_conserva_lo_cargado(self):
        r = self.transferir("40.000,00", "20.000,01")
        self.assertEqual(r.context["form"]["monto_2"].value(), "20.000,01")


class VistaAsignadaTest(BaseNuevo):
    """FR-019: solo una transferencia Pendiente visible para el usuario."""

    def setUp(self):
        super().setUp()
        self.transferir("40.000,00", "10.000,00")
        self.cobro = Cobro.objects.get()

    def test_cobro_de_otro_operador_da_404(self):
        otro = get_user_model().objects.create_user("beto", email="b@example.com", role="operator")
        self.client.force_login(otro)
        self.assertEqual(self.client.get(asignado(self.cobro.pk)).status_code, 404)

    def test_id_que_no_es_numero_da_404(self):
        self.assertEqual(self.client.get(f"{NUEVO}?cobro=abc").status_code, 404)

    def test_cobro_finalizado_redirige_al_detalle(self):
        self.client.post(
            reverse("cobros:registrar_transferencia", args=[self.cobro.pk]),
            {"comprobante": imagen(), "estado_visto": "pendiente"},
        )
        r = self.client.get(asignado(self.cobro.pk))
        self.assertRedirects(r, reverse("cobros:detalle", args=[self.cobro.pk]))

    def test_la_tabla_queda_bloqueada(self):
        r = self.client.get(asignado(self.cobro.pk))
        self.assertTrue(r.context["form"].fields["monto_1"].disabled)
        self.assertFalse(r.context["form"].fields["comprobante"].disabled)


class RegistrarTransferenciaTest(BaseNuevo):
    """FR-017: el comprobante cierra el cobro; sin archivo sigue Pendiente."""

    def setUp(self):
        super().setUp()
        self.transferir("40.000,00", "10.000,00")
        self.cobro = Cobro.objects.get()
        self.url = reverse("cobros:registrar_transferencia", args=[self.cobro.pk])

    def test_sin_archivo_marca_el_error_y_sigue_pendiente(self):
        r = self.client.post(self.url, {"estado_visto": "pendiente"})
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "Falta el comprobante.", status_code=400)
        self.cobro.refresh_from_db()
        self.assertEqual(self.cobro.estado, Cobro.Estado.PENDIENTE)

    def test_con_imagen_finaliza_y_va_al_detalle(self):
        r = self.client.post(self.url, {"comprobante": imagen(), "estado_visto": "pendiente"})
        self.assertRedirects(r, reverse("cobros:detalle", args=[self.cobro.pk]))
        self.cobro.refresh_from_db()
        self.assertEqual(self.cobro.estado, Cobro.Estado.FINALIZADO)


class EmpezarDeNuevoTest(BaseNuevo):
    """FR-018: el usuario elige el motivo y el cobro queda Cancelado."""

    def setUp(self):
        super().setUp()
        self.transferir("40.000,00", "10.000,00")
        self.cobro = Cobro.objects.get()
        self.url = reverse("cobros:empezar_de_nuevo", args=[self.cobro.pk])

    def test_sin_motivo_no_cancela(self):
        r = self.client.post(self.url, {"estado_visto": "pendiente"})
        self.assertContains(r, "Elegí un motivo de cancelación.", status_code=400)
        self.cobro.refresh_from_db()
        self.assertEqual(self.cobro.estado, Cobro.Estado.PENDIENTE)

    def test_otro_sin_detalle_no_cancela(self):
        r = self.client.post(self.url, {"motivo": "otro", "estado_visto": "pendiente"})
        self.assertContains(r, "Escribí el detalle del motivo.", status_code=400)

    def test_con_motivo_cancela_y_vuelve_al_paciente(self):
        r = self.client.post(self.url, {"motivo": "datos_erroneos", "estado_visto": "pendiente"})
        self.assertRedirects(r, PACIENTE)
        self.cobro.refresh_from_db()
        self.assertEqual(self.cobro.estado, Cobro.Estado.CANCELADO)
        self.assertEqual(self.cobro.motivo_cancelacion, "datos_erroneos")
