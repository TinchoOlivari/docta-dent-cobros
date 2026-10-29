import threading
from datetime import datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection

from sistema.auditoria.models import Evento
from sistema.cobros import servicios
from sistema.cobros.models import Cobro
from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.cobros.tests.test_comprobante import imagen
from sistema.core.tests import SistemaTransactionTestCase
from sistema.core.tiempo import ARG
from sistema.dentalink.tests.test_lectura import prestacion, tratamiento
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario

AHORA = datetime(2026, 10, 14, 10, 0, tzinfo=ARG)


class AdjuntarContraCancelarTest(SistemaTransactionTestCase):
    def setUp(self):
        super().setUp()
        reloj = mock.patch("django.utils.timezone.now", return_value=AHORA)
        reloj.start()
        self.addCleanup(reloj.stop)
        User = get_user_model()
        self.ana = User.objects.create_user("ana", email="ana@example.com", role="operator")
        self.admin = User.objects.create_user(
            "admin", email="admin@example.com", role="administrator"
        )
        dest = crear_destinatario(usuario=None, nombre="Dra Prueba", tipo="profesional")
        cuenta = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=dest,
            alias="prueba.uno",
            tope_mensual=Decimal("100000.00"),
        )
        self.cobro = cobro_prueba(
            cuenta=cuenta, monto=Decimal("50000.00"), estado="pendiente", fecha=AHORA
        )

    def test_us2_6_adjunta_primero_y_el_admin_recibe_el_aviso(self):
        dentro, soltar = threading.Event(), threading.Event()
        original = servicios._con_archivo
        resultado = {}

        def lento(cobro, archivo):
            original(cobro, archivo)
            dentro.set()
            soltar.wait(timeout=30)

        def adjuntar():
            try:
                with mock.patch.object(servicios, "_con_archivo", lento):
                    servicios.adjuntar_comprobante(self.cobro, imagen(), usuario=self.ana)
            finally:
                connection.close()

        def cancelar():
            try:
                servicios.cancelar_cobro(
                    self.cobro,
                    usuario=self.admin,
                    motivo="datos_erroneos",
                    estado_visto="pendiente",
                )
            except ValidationError as e:
                resultado["error"] = e
            finally:
                connection.close()

        h1 = threading.Thread(target=adjuntar)
        h1.start()
        self.assertTrue(dentro.wait(timeout=30))
        h2 = threading.Thread(target=cancelar)
        h2.start()
        h2.join(timeout=1)  # el admin espera el bloqueo del cobro
        self.assertTrue(h2.is_alive())
        soltar.set()
        h1.join(timeout=30)
        h2.join(timeout=30)
        self.cobro.refresh_from_db()
        self.assertEqual(self.cobro.estado, "finalizado")
        self.assertIn("El cobro cambió a Finalizado.", str(resultado["error"]))


class SaldoConcurrenteTest(SistemaTransactionTestCase):
    def setUp(self):
        super().setUp()
        for nombre, valor in (
            (
                "sistema.dentalink.cliente.tratamientos",
                [tratamiento([prestacion(1, "40000", "0")])],
            ),
            (
                "sistema.dentalink.cliente.paciente",
                {"id": 501, "nombre": "Juan", "dni": "30111222"},
            ),
            ("django.utils.timezone.now", AHORA),
        ):
            p = mock.patch(nombre, return_value=valor)
            p.start()
            self.addCleanup(p.stop)
        User = get_user_model()
        self.ana = User.objects.create_user("ana", email="ana@example.com", role="operator")
        self.luis = User.objects.create_user("luis", email="luis@example.com", role="operator")
        dest = crear_destinatario(usuario=None, nombre="Clinica", tipo="clinica")
        crear_cuenta(usuario=None, tipo="efectivo", destinatario=dest)

    def test_us4_7_el_segundo_alta_falla_sin_saldo_disponible(self):
        dentro, soltar = threading.Event(), threading.Event()
        original = servicios.cuenta_efectivo
        errores = {}

        def lenta():
            cuenta = original()
            if not dentro.is_set():  # solo el primero espera, con la fila tomada
                dentro.set()
                soltar.wait(timeout=30)
            return cuenta

        def alta(usuario):
            try:
                servicios.crear_cobro(
                    usuario=usuario,
                    metodo="efectivo",
                    monto=Decimal("40000.00"),
                    id_paciente=501,
                    items=[
                        {
                            "id_prestacion": 1,
                            "monto": Decimal("40000.00"),
                            "id_profesional": None,
                            "saldo_a_favor": False,
                        }
                    ],
                )
            except ValidationError as e:
                errores[usuario.username] = e
            finally:
                connection.close()

        with mock.patch.object(servicios, "cuenta_efectivo", lenta):
            h1 = threading.Thread(target=alta, args=(self.ana,))
            h1.start()
            self.assertTrue(dentro.wait(timeout=30))
            h2 = threading.Thread(target=alta, args=(self.luis,))
            h2.start()
            h2.join(timeout=1)  # luis espera la fila de ana
            self.assertTrue(h2.is_alive())
            soltar.set()
            h1.join(timeout=30)
            h2.join(timeout=30)
        self.assertNotIn("ana", errores)
        # sin saldo, la prestación sale de la lista (R3): luis no puede elegirla
        self.assertIn("no tiene saldo para cobrar", str(errores["luis"]))
        self.assertEqual(Cobro.objects.count(), 1)
        self.assertEqual(Cobro.objects.get().creado_por, self.ana)


class ReabrirContraCancelarTest(SistemaTransactionTestCase):
    def setUp(self):
        super().setUp()
        reloj = mock.patch("django.utils.timezone.now", return_value=AHORA)
        reloj.start()
        self.addCleanup(reloj.stop)
        User = get_user_model()
        self.admin1 = User.objects.create_user(
            "admin1", email="admin1@example.com", role="administrator"
        )
        self.admin2 = User.objects.create_user(
            "admin2", email="admin2@example.com", role="administrator"
        )
        dest = crear_destinatario(usuario=None, nombre="Dra Prueba", tipo="profesional")
        cuenta = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=dest,
            alias="prueba.uno",
            tope_mensual=Decimal("100000.00"),
        )
        self.cobro = cobro_prueba(
            cuenta=cuenta, monto=Decimal("50000.00"), estado="finalizado", fecha=AHORA
        )

    def test_gana_la_reapertura_y_la_cancelacion_ve_el_estado_nuevo(self):
        dentro, soltar = threading.Event(), threading.Event()
        resultado = {}

        def lento(*a, **kw):
            dentro.set()
            soltar.wait(timeout=30)

        def reabrir():
            try:
                with mock.patch("sistema.dentalink.servicios.al_cambiar_cobro", lento):
                    servicios.reabrir_cobro(
                        self.cobro, usuario=self.admin1, motivo="Falta", estado_visto="finalizado"
                    )
            finally:
                connection.close()

        def cancelar():
            try:
                servicios.cancelar_cobro(
                    self.cobro,
                    usuario=self.admin2,
                    motivo="datos_erroneos",
                    estado_visto="finalizado",
                )
            except ValidationError as e:
                resultado["error"] = e
            finally:
                connection.close()

        h1 = threading.Thread(target=reabrir)
        h1.start()
        self.assertTrue(dentro.wait(timeout=30))
        h2 = threading.Thread(target=cancelar)
        h2.start()
        h2.join(timeout=1)  # el segundo espera el bloqueo del cobro
        self.assertTrue(h2.is_alive())
        soltar.set()
        h1.join(timeout=30)
        h2.join(timeout=30)
        self.cobro.refresh_from_db()
        self.assertEqual(self.cobro.estado, "pendiente")
        self.assertIn("El cobro cambió a Pendiente.", str(resultado["error"]))
        self.assertEqual(Evento.objects.filter(tipo="cambio", entidad="cobros.cobro").count(), 1)


class DobleAccionTest(SistemaTransactionTestCase):
    """Dos administradores a la vez sobre el mismo cobro: el segundo ve el estado nuevo."""

    def setUp(self):
        super().setUp()
        reloj = mock.patch("django.utils.timezone.now", return_value=AHORA)
        reloj.start()
        self.addCleanup(reloj.stop)
        User = get_user_model()
        self.a1 = User.objects.create_user("a1", email="a1@example.com", role="administrator")
        self.a2 = User.objects.create_user("a2", email="a2@example.com", role="administrator")
        dest = crear_destinatario(usuario=None, nombre="Dra Prueba", tipo="profesional")
        cuenta = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=dest,
            alias="prueba.uno",
            tope_mensual=Decimal("100000.00"),
        )
        self.cobro = cobro_prueba(
            cuenta=cuenta, monto=Decimal("50000.00"), estado="finalizado", fecha=AHORA
        )

    def correr(self, primero, segundo, lento_en):
        """`primero` queda dentro del bloqueo hasta que `segundo` espera. Devuelve el error de 2."""
        dentro, soltar = threading.Event(), threading.Event()
        resultado = {}

        def lento(*a, **kw):
            dentro.set()
            soltar.wait(timeout=30)

        def h1():
            try:
                with mock.patch(lento_en, lento):
                    primero()
            finally:
                connection.close()

        def h2():
            try:
                segundo()
            except ValidationError as e:
                resultado["error"] = e
            finally:
                connection.close()

        t1 = threading.Thread(target=h1)
        t1.start()
        self.assertTrue(dentro.wait(timeout=30))
        t2 = threading.Thread(target=h2)
        t2.start()
        t2.join(timeout=1)
        self.assertTrue(t2.is_alive())
        soltar.set()
        t1.join(timeout=30)
        t2.join(timeout=30)
        return resultado.get("error")

    def test_dos_reaperturas_simultaneas_gana_una(self):
        def reabrir(usuario):
            return lambda: servicios.reabrir_cobro(
                self.cobro, usuario=usuario, motivo="Falta", estado_visto="finalizado"
            )

        error = self.correr(
            reabrir(self.a1), reabrir(self.a2), "sistema.dentalink.servicios.al_cambiar_cobro"
        )
        self.cobro.refresh_from_db()
        self.assertEqual(self.cobro.estado, "pendiente")
        self.assertIn("El cobro cambió a Pendiente.", str(error))
        self.assertEqual(Evento.objects.filter(tipo="cambio", entidad="cobros.cobro").count(), 1)

    def test_dos_adjuntos_simultaneos_del_mismo_estado_visto_suman_dos(self):
        # FR-052: ambos vieron Finalizado; adjuntar a un Finalizado es válido, no hay conflicto.
        self.cobro.comprobante = "comprobantes/prueba.pdf"
        self.cobro.save()

        def adjuntar(usuario):
            return lambda: servicios.adjuntar_comprobante(
                self.cobro, imagen(), usuario=usuario, estado_visto="finalizado"
            )

        error = self.correr(
            adjuntar(self.a1), adjuntar(self.a2), "sistema.cobros.servicios.registrar_alta"
        )
        self.assertIsNone(error)
        self.assertEqual(self.cobro.otros_comprobantes.count(), 2)

    def test_adjuntar_contra_reabrir_el_adjunto_con_estado_viejo_falla(self):
        def reabrir():
            servicios.reabrir_cobro(
                self.cobro, usuario=self.a1, motivo="Falta", estado_visto="finalizado"
            )

        def adjuntar():
            servicios.adjuntar_comprobante(
                self.cobro, imagen(), usuario=self.a2, estado_visto="finalizado"
            )

        error = self.correr(reabrir, adjuntar, "sistema.dentalink.servicios.al_cambiar_cobro")
        self.assertIn("El cobro cambió a Pendiente.", str(error))
        self.assertEqual(self.cobro.otros_comprobantes.count(), 0)
