from datetime import timedelta
from decimal import Decimal
from smtplib import SMTPServerDisconnected
from unittest import mock

from django.core import mail
from django.urls import reverse

from sistema.auditoria.models import Evento
from sistema.avisos import servicios as avisos
from sistema.avisos.tests.test_tope import AHORA, BaseTope
from sistema.cobros import servicios
from sistema.cobros.models import Cobro, ItemCobro
from sistema.cobros.tests.fabrica import con_elegidas
from sistema.cobros.tests.test_efectivo import DATOS_PACIENTE, DELAY, PACIENTE, TRATAMIENTOS, item
from sistema.dentalink import servicios as dentalink
from sistema.dentalink.tests.test_lectura import prestacion, tratamiento
from sistema.destinatarios.models import Cuenta

D = Decimal
SEND = "django.core.mail.EmailMessage.send"
TASK_LOGGER = "sistema.avisos.tasks"


class BaseFallas(BaseTope):
    def setUp(self):
        super().setUp()
        for nombre, valor in (
            (TRATAMIENTOS, [tratamiento([prestacion(1, "450000", "0")])]),
            (PACIENTE, DATOS_PACIENTE),
        ):
            p = mock.patch(nombre, return_value=valor)
            p.start()
            self.addCleanup(p.stop)
        self.usado("420000.00")
        self.client.force_login(self.ana)

    def post_cobro(self):
        # 420000 + 30000 cruza el umbral (450000) y dispara el aviso de cerca del tope.
        return self.client.post(
            reverse("cobros:nuevo") + "?paciente=501",
            con_elegidas({"metodo": "transferencia", "monto_1": "30000,00"}),
        )


class FallaDeEmailTests(BaseFallas):
    def test_servidor_caido_el_cobro_se_guarda_y_el_log_dice_aviso_no_enviado(self):
        with mock.patch(SEND, side_effect=SMTPServerDisconnected()):
            with self.assertLogs(TASK_LOGGER, "ERROR") as logs:
                with self.captureOnCommitCallbacks(execute=True):
                    r = self.post_cobro()
        cobro = Cobro.objects.get(nombre_paciente="Juan Paciente")
        # 024/FR-014: la transferencia sigue en `cobros:nuevo` con su cuenta
        self.assertRedirects(r, f"{reverse('cobros:nuevo')}?cobro={cobro.pk}")
        mensajes = [str(m) for m in r.wsgi_request._messages]
        self.assertEqual(mensajes, [])  # la caja muestra la cuenta; el email caído no se ve
        self.assertTrue(any("Aviso no enviado" in linea for linea in logs.output))

    def test_servidor_que_vuelve_manda_el_email_y_no_deja_log_de_error(self):
        with mock.patch(SEND, side_effect=[SMTPServerDisconnected(), 1, 1]) as send:
            with self.assertNoLogs(TASK_LOGGER, "ERROR"):
                with self.captureOnCommitCallbacks(execute=True):
                    r = self.post_cobro()
        self.assertEqual(r.status_code, 302)
        self.assertEqual(send.call_count, 3)  # 1 fallo + reintento + segunda dirección
        self.assertTrue(Cobro.objects.filter(nombre_paciente="Juan Paciente").exists())


class SinAvisosPorOtrosEventosTests(BaseFallas):
    def test_otros_eventos_no_mandan_ningun_email(self):
        Cuenta.objects.get_or_create(destinatario=self.dest, tipo="efectivo")
        with mock.patch(DELAY), self.captureOnCommitCallbacks(execute=True):
            # cancelar un cobro pendiente
            cancelado = self.usado("1000.00")
            servicios.cancelar_cobro(
                cancelado, usuario=self.ana, motivo="datos_erroneos", estado_visto="pendiente"
            )
            # marcar revisado
            final = self.usado("2000.00", estado="finalizado")
            servicios.marcar_revisado(final, usuario=self.admin, comentario="Ok")
            # «faltan ítems» (efectivo) y completar ítems
            faltan = servicios.crear_cobro(
                usuario=self.ana,
                metodo="efectivo",
                monto=D("5000.00"),
                nombre_paciente="Juan Paciente",
            )
            servicios.completar_items(
                faltan, usuario=self.ana, id_paciente=501, items=[item(1, "5000.00")]
            )
            # configuración de Dentalink
            dentalink.guardar_configuracion(
                usuario=self.admin,
                id_prestacion_saldo_a_favor=1,
                nombre_prestacion_saldo_a_favor="Saldo",
                banco_por_defecto=None,
            )
        # login fallido y bloqueo por 5 fallos (003)
        self.client.logout()
        self.ana.set_password("clave-123")
        self.ana.save()
        for n in range(1, 6):  # el reloj fijo empataría con el cierre de sesión
            with mock.patch("django.utils.timezone.now", return_value=AHORA + timedelta(minutes=n)):
                self.client.post(reverse("usuarios:login"), {"username": "ana", "password": "mal"})
        self.assertEqual(Evento.objects.filter(tipo=Evento.Tipo.BLOQUEO).count(), 1)
        self.assertEqual(mail.outbox, [])


class ContenidoTests(BaseFallas):
    def test_los_siete_avisos_tienen_asunto_en_espanol_paciente_y_no_crean_eventos(self):
        cobro = self.usado("1000.00")
        cobro.nombre_paciente = "Juan Paciente"
        cobro.save()
        it = ItemCobro.objects.create(cobro=cobro, numero=1, id_prestacion=9001, monto=D("1000.00"))
        self.cuenta.objetivo = D("1000000.00")
        self.cuenta.acumula_desde = AHORA
        eventos = Evento.objects.count()
        with self.captureOnCommitCallbacks(execute=True):
            avisos.cerca_del_tope(
                cuenta=self.cuenta,
                momento=AHORA,
                antes=D("0.00"),
                despues=D("500000.00"),
                origen=cobro,
            )
            avisos.excedente(cobro=cobro, usado=D("600000.00"))
            avisos.no_hay_cuenta(usuario=self.ana, monto=D("1000.00"), paciente="Juan Paciente")
            avisos.cerca_del_objetivo(cuenta=self.cuenta, antes=D("0.00"), despues=D("1000000.00"))
            avisos.objetivo_alcanzado(cuenta=self.cuenta, acumulado=D("1000000.00"))
            for estado in (ItemCobro.EstadoEnvio.ERROR, ItemCobro.EstadoEnvio.REVISAR):
                it.estado_envio = estado
                it.ultimo_error = "caído"
                avisos.item_con_falla(it)
        pk = cobro.pk
        esperados = [
            "Cuenta Destinatario Ejemplo — alias.uno cerca del tope",
            "Cobro excedente: cuenta Destinatario Ejemplo — alias.uno",
            "No hay cuenta disponible para $1.000,00",
            "Cuenta Destinatario Ejemplo — alias.uno cerca del objetivo",
            "Cuenta Destinatario Ejemplo — alias.uno alcanzó su objetivo",
            f"Envío a Dentalink con error: cobro {pk}",
            f"Revisar en Dentalink: cobro {pk}",
        ]
        # Dos direcciones: cada aviso sale dos veces; el aviso "ya" sale primero.
        self.assertCountEqual(self.asuntos(), [a for a in esperados for _ in range(2)])
        con_paciente = [m for m in mail.outbox if "No hay cuenta" not in m.subject]
        for m in mail.outbox:
            if "objetivo" not in m.subject:
                self.assertIn("Paciente: Juan Paciente", m.body, m.subject)
        self.assertTrue(con_paciente)
        self.assertEqual(Evento.objects.count(), eventos)
