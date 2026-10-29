from datetime import datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError

from sistema.asignacion.servicios import SinCuentaDisponible, lista_con_cupo
from sistema.auditoria.models import Evento
from sistema.cobros import servicios
from sistema.cobros.models import Cobro, ItemCobro
from sistema.cobros.tests.test_comprobante import imagen
from sistema.cobros.tests.test_efectivo import ARG, DELAY, BaseEfectivo, item
from sistema.destinatarios.models import Cuenta
from sistema.destinatarios.servicios import (
    cambiar_estado_cuenta,
    crear_cuenta,
    crear_destinatario,
)

D = Decimal


class BaseTransferencia(BaseEfectivo):
    def setUp(self):
        super().setUp()
        self.admin = get_user_model().objects.create_user(
            "admin", email="admin@example.com", role="administrator"
        )
        self.dest = crear_destinatario(usuario=None, nombre="Dra Prueba", tipo="profesional")
        self.cuenta = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=self.dest,
            alias="prueba.uno",
            tope_mensual=D("60000.00"),
        )

    def alta(self, monto="50000.00", items=None):
        items = items if items is not None else [item(1, "40000.00"), item(2, "10000.00")]
        return servicios.crear_cobro(
            usuario=self.ana,
            metodo="transferencia",
            monto=D(monto),
            id_paciente=501,
            items=items,
        )

    def cancelar(self, cobro, usuario=None, **kw):
        kw.setdefault("motivo", "datos_erroneos")
        kw.setdefault("estado_visto", "pendiente")
        return servicios.cancelar_cobro(cobro, usuario=usuario or self.ana, **kw)


class AltaTest(BaseTransferencia):
    def test_us2_1_queda_pendiente_en_la_cuenta_de_la_lista(self):
        with mock.patch(DELAY) as delay, self.captureOnCommitCallbacks(execute=True):
            cobro = self.alta()
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, "pendiente")
        self.assertEqual(cobro.cuenta, self.cuenta)
        self.assertEqual(cobro.destinatario, self.dest)
        self.assertEqual(cobro.metodo, "transferencia")
        self.assertEqual(cobro.nombre_paciente, "Juan Paciente")
        self.assertEqual(cobro.id_paciente_dentalink, 501)
        self.assertIsNone(cobro.numero_operacion)
        self.assertFalse(cobro.comprobante)
        self.assertEqual(cobro.items.count(), 2)
        delay.assert_not_called()  # Pendiente no se envía

    def test_sin_cuenta_con_cupo_no_deja_items(self):
        with self.assertRaises(SinCuentaDisponible):
            self.alta(monto="60000.01", items=[item(1, "40000.00"), item(2, "20000.01")])
        self.assertEqual(Cobro.objects.count(), 0)
        self.assertEqual(ItemCobro.objects.count(), 0)

    def test_monto_justo_en_el_cupo_entra(self):
        cobro = self.alta(monto="60000.00", items=[item(1, "40000.00"), item(2, "20000.00")])
        self.assertEqual(cobro.cuenta, self.cuenta)

    def test_pendiente_de_hace_20_dias_sigue_reservando_cupo(self):
        cobro = self.alta()
        Cobro.objects.filter(pk=cobro.pk).update(fecha=datetime(2026, 10, 5, 10, 0, tzinfo=ARG))
        with mock.patch(
            "django.utils.timezone.now", return_value=datetime(2026, 10, 25, 10, 0, tzinfo=ARG)
        ):
            self.assertEqual(lista_con_cupo()[0].cupo_mensual, D("10000.00"))


class AdjuntarTest(BaseTransferencia):
    def test_us2_2_adjuntar_finaliza_y_encola_el_envio(self):
        cobro = self.alta()
        with mock.patch(DELAY) as delay, self.captureOnCommitCallbacks(execute=True):
            servicios.adjuntar_comprobante(cobro, imagen(), usuario=self.ana)
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, "finalizado")
        self.assertEqual(cobro.monto, D("50000.00"))
        self.assertTrue(cobro.comprobante)
        delay.assert_called_once_with(cobro.pk)
        self.assertTrue(
            Evento.objects.filter(tipo="cambio", entidad__startswith="cobros.").exists()
        )

    def test_us2_3_sin_archivo_rechaza_y_sigue_pendiente(self):
        cobro = self.alta()
        with self.assertRaisesMessage(ValidationError, "Falta el comprobante."):
            servicios.adjuntar_comprobante(cobro, None, usuario=self.ana)
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, "pendiente")

    def test_us2_7_cancelado_no_admite_adjuntar(self):
        cobro = self.alta()
        self.cancelar(cobro)
        with self.assertRaisesMessage(ValidationError, "El cobro ya está Cancelado."):
            servicios.adjuntar_comprobante(cobro, imagen(), usuario=self.ana)

    def test_adjuntar_dos_veces_rechaza_y_no_borra_el_archivo(self):
        cobro = self.alta()
        servicios.adjuntar_comprobante(cobro, imagen(), usuario=self.ana)
        cobro.refresh_from_db()
        nombre = cobro.comprobante.name
        with self.assertRaisesMessage(ValidationError, "El cobro ya está Finalizado."):
            servicios.adjuntar_comprobante(cobro, imagen(), usuario=self.ana)
        self.assertTrue(cobro.comprobante.storage.exists(nombre))

    def test_edge_adjuntar_en_noviembre_deja_la_fecha_en_octubre(self):
        with mock.patch(
            "django.utils.timezone.now",
            return_value=datetime(2026, 10, 31, 23, 50, tzinfo=ARG),
        ):
            cobro = self.alta()
        with mock.patch(
            "django.utils.timezone.now",
            return_value=datetime(2026, 11, 1, 0, 10, tzinfo=ARG),
        ):
            servicios.adjuntar_comprobante(cobro, imagen(), usuario=self.ana)
        cobro.refresh_from_db()
        self.assertEqual(cobro.fecha, datetime(2026, 10, 31, 23, 50, tzinfo=ARG))
        self.assertEqual(cobro.estado, "finalizado")

    def test_fr047_cuenta_archivada_igual_finaliza_en_la_misma_cuenta(self):
        cobro = self.alta()
        cambiar_estado_cuenta(self.cuenta, Cuenta.Estado.ARCHIVADA, usuario=self.admin)
        servicios.adjuntar_comprobante(cobro, imagen(), usuario=self.ana)
        cobro.refresh_from_db()
        self.assertEqual((cobro.estado, cobro.cuenta), ("finalizado", self.cuenta))

    def test_si_falla_despues_de_escribir_el_archivo_se_borra(self):
        cobro = self.alta()
        escritos = []

        def falla(c):
            escritos.append(c.comprobante.name)
            self.assertTrue(c.comprobante.storage.exists(c.comprobante.name))
            raise RuntimeError("falla")

        with mock.patch("sistema.dentalink.servicios.encolar_envio", side_effect=falla):
            with self.assertRaises(RuntimeError):
                servicios.adjuntar_comprobante(cobro, imagen(), usuario=self.ana)
        self.assertEqual(len(escritos), 1)
        self.assertFalse(cobro.comprobante.storage.exists(escritos[0]))
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, "pendiente")
        self.assertFalse(cobro.comprobante)


class CancelarTest(BaseTransferencia):
    def test_us2_4_cancelar_libera_el_cupo(self):
        cobro = self.alta()
        self.assertEqual(lista_con_cupo()[0].cupo_mensual, D("10000.00"))
        self.cancelar(cobro)
        self.assertEqual(lista_con_cupo()[0].cupo_mensual, D("60000.00"))
        nuevo = self.alta(monto="45000.00", items=[item(1, "40000.00"), item(2, "5000.00")])
        self.assertEqual(nuevo.cuenta, self.cuenta)

    def test_cancelar_cancela_los_items_pendientes(self):
        cobro = self.alta()
        self.cancelar(cobro)
        self.assertEqual(set(cobro.items.values_list("estado_envio", flat=True)), {"cancelado"})

    def test_us2_5_otro_sin_detalle_rechaza_y_con_detalle_cancela(self):
        cobro = self.alta()
        with self.assertRaisesMessage(ValidationError, "Con el motivo «Otro», escribí el detalle."):
            self.cancelar(cobro, motivo="otro")
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, "pendiente")
        self.cancelar(cobro, motivo="otro", detalle="El paciente se arrepintió")
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, "cancelado")
        self.assertEqual(cobro.detalle_cancelacion, "El paciente se arrepintió")

    def test_us2_7_cancelar_un_cancelado_rechaza(self):
        cobro = self.alta()
        self.cancelar(cobro)
        with self.assertRaisesMessage(ValidationError, "El cobro ya está Cancelado."):
            self.cancelar(cobro, estado_visto="cancelado")

    def test_estado_visto_viejo_rechaza_y_el_cobro_sigue_finalizado(self):
        cobro = self.alta()
        servicios.adjuntar_comprobante(cobro, imagen(), usuario=self.ana)
        with self.assertRaisesMessage(
            ValidationError,
            "El cobro cambió a Finalizado. Recargá la página para ver el estado actual.",
        ):
            self.cancelar(cobro, usuario=self.admin)
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, "finalizado")

    def test_operador_no_cancela_un_finalizado(self):
        cobro = self.alta()
        servicios.adjuntar_comprobante(cobro, imagen(), usuario=self.ana)
        with self.assertRaises(PermissionDenied):
            self.cancelar(cobro, estado_visto="finalizado")
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, "finalizado")

    def test_administrador_cancela_un_finalizado(self):
        cobro = self.alta()
        servicios.adjuntar_comprobante(cobro, imagen(), usuario=self.ana)
        self.cancelar(cobro, usuario=self.admin, estado_visto="finalizado")
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, "cancelado")

    def test_cancelar_un_excedente_lo_deja_sin_excedente(self):
        cobro = self.alta()
        Cobro.objects.filter(pk=cobro.pk).update(excedente=True)
        self.cancelar(cobro)
        cobro.refresh_from_db()
        self.assertFalse(cobro.excedente)


class ArchivoTest(BaseTransferencia):
    def test_con_archivo_sin_archivo_no_toca_nada(self):
        cobro = Cobro()
        servicios._con_archivo(cobro, None)
        self.assertFalse(cobro.comprobante)

    def test_con_archivo_pone_el_comprobante_validado(self):
        cobro = Cobro()
        servicios._con_archivo(cobro, imagen())
        self.assertTrue(cobro.comprobante)

    def test_con_archivo_de_tipo_invalido_rechaza(self):
        with self.assertRaises(ValidationError):
            servicios._con_archivo(Cobro(), SimpleUploadedFile("c.docx", b"PK\x03\x04" * 20))

    def test_borrar_archivo_borra_del_almacenamiento(self):
        cobro = Cobro()
        servicios._con_archivo(cobro, imagen())
        cobro.comprobante.save(cobro.comprobante.name, cobro.comprobante.file, save=False)
        nombre = cobro.comprobante.name
        self.assertTrue(cobro.comprobante.storage.exists(nombre))
        servicios._borrar_archivo(cobro)
        self.assertFalse(cobro.comprobante.storage.exists(nombre))

    def test_borrar_archivo_sin_archivo_no_falla(self):
        servicios._borrar_archivo(Cobro())

    def test_alta_que_falla_borra_el_archivo_escrito(self):
        escritos = []

        def escribe_y_falla(cobro, *a, **kw):
            servicios._con_archivo(cobro, imagen())
            cobro.comprobante.save(cobro.comprobante.name, cobro.comprobante.file, save=False)
            escritos.append(cobro.comprobante.name)
            raise ValidationError("falla")

        with mock.patch.object(Cobro, "full_clean", escribe_y_falla):
            with self.assertRaises(ValidationError):
                self.crear(items=[item(1, "100.00")], monto="100.00")
        self.assertEqual(len(escritos), 1)
        self.assertFalse(default_storage.exists(escritos[0]))

    def test_integrity_error_de_constraint_pasa_a_validation_error(self):
        error = IntegrityError("viola la restricción «cobro_monto_positivo» de la tabla")
        with mock.patch.object(servicios, "cuenta_efectivo", side_effect=error):
            with self.assertRaisesMessage(ValidationError, "El monto debe ser mayor que $0,00."):
                self.crear(items=[item(1, "100.00")], monto="100.00")

    def test_integrity_error_desconocido_sale_tal_cual(self):
        error = IntegrityError("otra cosa")
        self.assertIsNone(servicios._traducir(error))
        with mock.patch.object(servicios, "cuenta_efectivo", side_effect=error):
            with self.assertRaises(IntegrityError):
                self.crear(items=[item(1, "100.00")], monto="100.00")
