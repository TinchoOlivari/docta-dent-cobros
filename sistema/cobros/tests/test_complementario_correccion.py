from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError

from sistema.asignacion.servicios import lista_con_cupo
from sistema.auditoria.servicios import historial_de, lineas
from sistema.cobros import servicios
from sistema.cobros.models import PagoComplementario
from sistema.cobros.tests.test_comprobante import imagen
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario

D = Decimal
OCT = date(2026, 10, 1)
SEP = date(2026, 9, 1)
AHORA = datetime(2026, 11, 3, 10, 0, tzinfo=ARG)


class CorreccionPagoTest(SistemaTestCase):
    def setUp(self):
        super().setUp()
        reloj = mock.patch("django.utils.timezone.now", return_value=AHORA)
        reloj.start()
        self.addCleanup(reloj.stop)
        User = get_user_model()
        self.admin = User.objects.create_user("admin", email="a@example.com", role="administrator")
        self.ana = User.objects.create_user("ana", email="ana@example.com", role="operator")
        self.martin = crear_destinatario(
            usuario=None, nombre="Destinatario Ejemplo", tipo="profesional"
        )
        self.cuenta = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=self.martin,
            alias="alias.uno",
            tope_mensual=D("100000.00"),
        )
        self.pago = self.registrar()

    def registrar(self, **kw):
        datos = dict(
            usuario=self.admin,
            destinatario=self.martin,
            mes=OCT,
            monto=D("20000.00"),
            metodo="transferencia",
            cuenta=self.cuenta,
        )
        datos.update(kw)
        return servicios.registrar_pago_complementario(**datos)

    def cupo(self, mes=OCT):
        momento = datetime(mes.year, mes.month, 15, 10, 0, tzinfo=ARG)
        return next(c for c in lista_con_cupo(momento) if c.pk == self.cuenta.pk).cupo_mensual

    def recibido(self):
        return next(f for f in servicios.resumen_mes(OCT) if f.pk == self.martin.pk).recibido

    def test_us3_1_anular_devuelve_cupo_y_baja_recibido(self):
        self.assertEqual(self.cupo(), D("80000.00"))
        servicios.anular_pago_complementario(self.pago, usuario=self.admin, motivo="datos_erroneos")
        self.pago.refresh_from_db()
        self.assertEqual(self.pago.estado, "anulado")
        self.assertEqual(self.cupo(), D("100000.00"))
        self.assertEqual(self.recibido(), D("0.00"))
        self.assertEqual(historial_de(self.pago).count(), 2)

    def test_anular_otro_exige_detalle(self):
        with self.assertRaisesMessage(ValidationError, "Con el motivo «Otro», escribí el detalle."):
            servicios.anular_pago_complementario(self.pago, usuario=self.admin, motivo="otro")
        self.pago.refresh_from_db()
        self.assertEqual(self.pago.estado, "registrado")
        servicios.anular_pago_complementario(
            self.pago, usuario=self.admin, motivo="otro", detalle="duplicado"
        )
        self.pago.refresh_from_db()
        self.assertEqual(self.pago.detalle_anulacion, "duplicado")

    def test_anular_sin_motivo_falla(self):
        with self.assertRaises(ValidationError):
            servicios.anular_pago_complementario(self.pago, usuario=self.admin, motivo="")

    def test_anular_dos_veces(self):
        servicios.anular_pago_complementario(self.pago, usuario=self.admin, motivo="no_pago")
        with self.assertRaisesMessage(ValidationError, "El pago complementario ya está Anulado."):
            servicios.anular_pago_complementario(self.pago, usuario=self.admin, motivo="no_pago")

    def test_us3_3_anulado_no_se_edita(self):
        servicios.anular_pago_complementario(self.pago, usuario=self.admin, motivo="no_pago")
        with self.assertRaises(ValidationError):
            servicios.editar_pago_complementario(self.pago, usuario=self.admin, estado="registrado")
        with self.assertRaisesMessage(
            ValidationError, "Un pago complementario Anulado no se edita."
        ):
            servicios.editar_pago_complementario(self.pago, usuario=self.admin, monto=D("1.00"))
        self.pago.refresh_from_db()
        self.assertEqual(self.pago.estado, "anulado")

    def test_us3_2_editar_monto_deja_evento(self):
        servicios.editar_pago_complementario(self.pago, usuario=self.admin, monto=D("25000.00"))
        evento = historial_de(self.pago).latest("pk")
        self.assertIn(("Monto", "$20.000,00", "$25.000,00"), lineas(evento))
        self.assertEqual(self.cupo(), D("75000.00"))

    def test_editar_sin_cambios_no_crea_evento(self):
        servicios.editar_pago_complementario(self.pago, usuario=self.admin, monto=D("20000.00"))
        self.assertEqual(historial_de(self.pago).count(), 1)

    def test_campos_que_no_se_editan(self):
        for clave in ("creado_por", "fecha_alta", "estado"):
            with self.assertRaisesMessage(ValidationError, f"El campo {clave} no se edita."):
                servicios.editar_pago_complementario(
                    self.pago, usuario=self.admin, **{clave: self.admin}
                )

    def test_a_efectivo_libera_el_cupo(self):
        servicios.editar_pago_complementario(self.pago, usuario=self.admin, metodo="efectivo")
        self.pago.refresh_from_db()
        self.assertIsNone(self.pago.cuenta)
        self.assertEqual(self.cupo(), D("100000.00"))

    def test_cambiar_mes_mueve_el_cupo(self):
        servicios.editar_pago_complementario(self.pago, usuario=self.admin, mes=SEP)
        self.assertEqual(self.cupo(OCT), D("100000.00"))
        self.assertEqual(self.cupo(SEP), D("80000.00"))

    def test_cambiar_destinatario_sin_cambiar_cuenta(self):
        otro = crear_destinatario(usuario=None, nombre="Rodrigo", tipo="profesional")
        with self.assertRaisesMessage(
            ValidationError, "La cuenta debe ser del destinatario del pago."
        ):
            servicios.editar_pago_complementario(self.pago, usuario=self.admin, destinatario=otro)

    def test_reemplazar_comprobante_conserva_el_anterior(self):
        pago = self.registrar(comprobante=imagen())
        viejo = pago.comprobante.name
        pago = servicios.editar_pago_complementario(pago, usuario=self.admin, comprobante=imagen())
        nuevo = pago.comprobante.name
        self.addCleanup(pago.comprobante.storage.delete, viejo)
        self.addCleanup(pago.comprobante.storage.delete, nuevo)
        self.assertNotEqual(viejo, nuevo)
        self.assertTrue((Path(settings.MEDIA_ROOT) / viejo).exists())
        evento = historial_de(pago).latest("pk")
        self.assertIn(("Comprobante", "Archivo adjunto", "Archivo adjunto"), lineas(evento))
        self.assertNotIn(Path(viejo).name, str(lineas(evento)))

    def test_edicion_fallida_borra_solo_el_archivo_nuevo(self):
        pago = self.registrar(comprobante=imagen())
        viejo = pago.comprobante.name
        self.addCleanup(pago.comprobante.storage.delete, viejo)
        carpeta = Path(settings.MEDIA_ROOT)
        antes = set(carpeta.rglob("*.jpg"))
        with self.assertRaises(ValidationError):
            servicios.editar_pago_complementario(
                pago, usuario=self.admin, monto=D("0.00"), comprobante=imagen()
            )
        self.assertEqual(set(carpeta.rglob("*.jpg")), antes)
        self.assertTrue(pago.comprobante.storage.exists(viejo))

    def test_editar_anulado_con_comprobante_nuevo_conserva_el_anterior(self):
        pago = self.registrar(comprobante=imagen())
        viejo = pago.comprobante.name
        self.addCleanup(pago.comprobante.storage.delete, viejo)
        servicios.anular_pago_complementario(pago, usuario=self.admin, motivo="no_pago")
        with self.assertRaisesMessage(
            ValidationError, "Un pago complementario Anulado no se edita."
        ):
            servicios.editar_pago_complementario(pago, usuario=self.admin, comprobante=imagen())
        self.assertTrue(pago.comprobante.storage.exists(viejo))

    def test_operador_no_edita_ni_anula(self):
        with self.assertRaises(PermissionDenied):
            servicios.editar_pago_complementario(self.pago, usuario=self.ana, monto=D("1.00"))
        with self.assertRaises(PermissionDenied):
            servicios.anular_pago_complementario(self.pago, usuario=self.ana, motivo="no_pago")
        self.assertEqual(PagoComplementario.objects.get(pk=self.pago.pk).estado, "registrado")
