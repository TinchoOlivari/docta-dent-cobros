from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.conf import settings

from sistema.asignacion.servicios import lista_con_cupo
from sistema.auditoria.servicios import historial_de
from sistema.cobros.tests.fabrica import topes_desde_enero
from sistema.cobros import servicios
from sistema.cobros.models import ItemCobro, PagoComplementario
from sistema.cobros.tests.test_comprobante import TIPO, imagen
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario

D = Decimal
OCT = date(2026, 10, 1)
AHORA = datetime(2026, 11, 3, 10, 0, tzinfo=ARG)


class RegistrarPagoTest(SistemaTestCase):
    def setUp(self):
        super().setUp()
        reloj = mock.patch("django.utils.timezone.now", return_value=AHORA)
        reloj.start()
        self.addCleanup(reloj.stop)
        User = get_user_model()
        self.admin = User.objects.create_user("admin", email="a@example.com", role="administrator")
        self.ana = User.objects.create_user("ana", email="ana@example.com", role="operator")
        self.drax = User.objects.create_user("drax", email="d@example.com", role="specialist")
        self.jimena = crear_destinatario(usuario=None, nombre="Jimena", tipo="profesional")
        self.cuenta = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=self.jimena,
            alias="jimena.roble.rana",
            tope_mensual=D("600000.00"),
        )
        topes_desde_enero()

    def registrar(self, **kw):
        datos = dict(
            usuario=self.admin,
            destinatario=self.jimena,
            mes=OCT,
            monto=D("30000.00"),
            metodo="transferencia",
            cuenta=self.cuenta,
        )
        datos.update(kw)
        return servicios.registrar_pago_complementario(**datos)

    def falta(self):
        return next(f for f in servicios.resumen_mes(OCT) if f.pk == self.jimena.pk).falta

    def test_us2_1_registra_y_baja_la_falta(self):
        from sistema.cobros.tests.fabrica import cobro_prueba

        cobro_prueba(
            destinatario=self.jimena,
            cuenta=self.cuenta,
            monto=D("570000.00"),
            estado="finalizado",
            fecha=datetime(2026, 10, 15, tzinfo=ARG),
        )
        self.assertEqual(self.falta(), D("30000.00"))
        pago = self.registrar()
        self.assertEqual(pago.estado, "registrado")
        self.assertEqual(pago.creado_por, self.admin)
        self.assertIsNotNone(pago.fecha_alta)
        self.assertEqual(self.falta(), D("0.00"))
        self.assertEqual(historial_de(pago).count(), 1)

    def test_us2_4_pago_de_mas_deja_falta_negativa(self):
        from sistema.cobros.tests.fabrica import cobro_prueba

        cobro_prueba(
            destinatario=self.jimena,
            cuenta=self.cuenta,
            monto=D("570000.00"),
            estado="finalizado",
            fecha=datetime(2026, 10, 15, tzinfo=ARG),
        )
        self.registrar(monto=D("35000.00"))
        self.assertEqual(self.falta(), D("-5000.00"))

    def test_monto_cero_se_rechaza_y_un_centavo_y_gigante_pasan(self):
        with self.assertRaisesMessage(ValidationError, "El monto debe ser mayor que $0,00."):
            self.registrar(monto=D("0.00"))
        self.registrar(monto=D("0.01"))
        self.registrar(monto=D("9999999999.99"))
        self.assertEqual(PagoComplementario.objects.count(), 2)

    def test_us2_6_efectivo_sin_cuenta_no_toca_el_cupo(self):
        antes = lista_con_cupo(AHORA)[0].cupo_mensual
        self.registrar(metodo="efectivo", cuenta=None, monto=D("10000.00"))
        self.assertEqual(lista_con_cupo(AHORA)[0].cupo_mensual, antes)

    def test_efectivo_con_cuenta_y_transferencia_sin_cuenta_fallan(self):
        with self.assertRaises(ValidationError):
            self.registrar(metodo="efectivo", cuenta=self.cuenta)
        with self.assertRaises(ValidationError):
            self.registrar(cuenta=None)
        self.assertEqual(PagoComplementario.objects.count(), 0)

    def test_cuenta_de_otro_destinatario(self):
        otro = crear_destinatario(usuario=None, nombre="Rodrigo", tipo="profesional")
        ajena = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=otro,
            alias="rodrigo.mp",
            tope_mensual=D("100000.00"),
        )
        with self.assertRaisesMessage(
            ValidationError, "La cuenta debe ser del destinatario del pago."
        ):
            self.registrar(cuenta=ajena)

    def test_mes_posterior_al_corriente_falla_y_corriente_o_anterior_pasan(self):
        with self.assertRaisesMessage(
            ValidationError, "El mes no puede ser posterior al mes corriente."
        ):
            self.registrar(mes=date(2026, 12, 1))
        self.registrar(mes=date(2026, 11, 1))
        self.registrar(mes=date(2026, 9, 1))

    def test_comprobante_opcional_y_guardado(self):
        self.assertFalse(self.registrar().comprobante)
        pago = self.registrar(comprobante=imagen())
        self.assertRegex(pago.comprobante.name, r"^comprobantes/\d{4}/\d{2}/[0-9a-f-]+\.jpg$")
        self.addCleanup(pago.comprobante.storage.delete, pago.comprobante.name)

    def test_comprobante_de_tipo_invalido_no_deja_pago_ni_archivo(self):
        txt = SimpleUploadedFile("c.txt", b"hola")
        with self.assertRaisesMessage(ValidationError, TIPO):
            self.registrar(comprobante=txt)
        self.assertEqual(PagoComplementario.objects.count(), 0)

    def test_comprobante_de_5_mb_mas_un_byte_se_rechaza(self):
        archivo = imagen()
        archivo.size = 5 * 1024 * 1024 + 1
        with self.assertRaises(ValidationError):
            self.registrar(comprobante=archivo)

    def test_falla_de_validacion_borra_el_archivo_nuevo(self):
        carpeta = Path(settings.MEDIA_ROOT)
        antes = set(carpeta.rglob("*.jpg"))
        with self.assertRaises(ValidationError):
            self.registrar(monto=D("0.00"), comprobante=imagen())
        self.assertEqual(set(carpeta.rglob("*.jpg")), antes)

    def test_solo_el_administrador(self):
        for u in (self.ana, self.drax, None):
            with self.assertRaises(PermissionDenied):
                self.registrar(usuario=u)
        self.assertEqual(PagoComplementario.objects.count(), 0)

    def test_us2_7_sin_items_sin_dentalink_y_sin_mover_la_lista(self):
        posicion = self.cuenta.posicion
        with mock.patch("sistema.dentalink.servicios.encolar_envio") as envio:
            self.registrar()
        envio.assert_not_called()
        self.assertEqual(ItemCobro.objects.count(), 0)
        self.cuenta.refresh_from_db()
        self.assertEqual(self.cuenta.posicion, posicion)
