from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import RequestFactory

from sistema.auditoria.middleware import contexto_auditoria
from sistema.auditoria.models import Evento
from sistema.auditoria.servicios import (
    foto,
    lineas,
    quien,
    registrar,
    registrar_alta,
    registrar_borrado,
    registrar_cambio,
)
from sistema.core.tests import SistemaTestCase
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario, crear_proveedor

User = get_user_model()
D = Decimal
CBU = "0000000000000000000001"


class RegistroBase(SistemaTestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="maria", email="m@test.com", password="x", role="administrator"
        )
        self.prov = crear_proveedor(usuario=None, nombre="Proveedor Test")


class RegistrarTest(RegistroBase):
    def test_alta_graba_campos_sin_id_ni_fecha_cambio(self):
        ev = registrar_alta(self.prov, usuario=self.admin)
        ev.refresh_from_db()
        campos = {c["campo"] for c in ev.cambios}
        self.assertEqual(ev.tipo, "alta")
        self.assertIn("nombre", campos)
        self.assertNotIn("id", campos)
        self.assertNotIn("fecha_cambio", campos)
        self.assertTrue(all(c["anterior"] is None for c in ev.cambios))
        self.assertEqual(ev.entidad, "destinatarios.proveedor")
        self.assertEqual(ev.entidad_id, self.prov.pk)

    def test_alta_cuenta_guarda_monto_como_texto(self):
        d = crear_destinatario(usuario=None, nombre="Persona Test", tipo="profesional")
        c = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=d,
            alias="alias.test",
            cbu=CBU,
            tope_mensual=D("500000.00"),
        )
        ev = registrar_alta(c, usuario=self.admin)
        ev.refresh_from_db()
        nuevo = next(x["nuevo"] for x in ev.cambios if x["campo"] == "tope_mensual")
        self.assertEqual(nuevo, "500000.00")

    def test_cambio_con_dos_campos_graba_un_evento(self):
        antes = foto(self.prov)
        previos = Evento.objects.count()
        self.prov.nombre = "Otro Nombre"
        self.prov.observaciones = "nota"
        self.prov.save()
        ev = registrar_cambio(self.prov, antes, usuario=self.admin)
        self.assertEqual(Evento.objects.count(), previos + 1)
        self.assertEqual([c["campo"] for c in ev.cambios], ["nombre", "observaciones"])

    def test_cambio_sin_diferencias_no_graba(self):
        previos = Evento.objects.count()
        self.assertIsNone(registrar_cambio(self.prov, foto(self.prov), usuario=self.admin))
        self.assertEqual(Evento.objects.count(), previos)

    def test_cambio_sin_diferencias_con_detalle_graba(self):
        ev = registrar_cambio(
            self.prov, foto(self.prov), usuario=self.admin, detalle="Contraseña cambiada"
        )
        self.assertEqual(ev.cambios, [])
        self.assertEqual(ev.detalle, "Contraseña cambiada")

    def test_borrado_deja_valor_en_anterior(self):
        ev = registrar_borrado(self.prov, usuario=self.admin)
        self.assertEqual(ev.tipo, "borrado")
        self.assertTrue(all(c["nuevo"] is None for c in ev.cambios))
        nombre = next(c for c in ev.cambios if c["campo"] == "nombre")
        self.assertEqual(nombre["anterior"], "Proveedor Test")

    def test_foto_de_usuario_excluye_password_y_last_login(self):
        f = foto(self.admin)
        self.assertNotIn("password", f)
        self.assertNotIn("last_login", f)
        self.assertIn("username", f)


class MiddlewareTest(SistemaTestCase):
    def pedir(self, **meta):
        resultado = {}

        def vista(request):
            resultado["ev"] = registrar("alta", usuario=None)
            return None

        contexto_auditoria(vista)(RequestFactory().get("/", **meta))
        return resultado["ev"]

    def test_usa_x_real_ip(self):
        ev = self.pedir(HTTP_X_REAL_IP="190.0.0.1", REMOTE_ADDR="10.0.0.2")
        self.assertEqual(ev.ip, "190.0.0.1")

    def test_sin_x_real_ip_usa_remote_addr(self):
        self.assertEqual(self.pedir(REMOTE_ADDR="10.0.0.2").ip, "10.0.0.2")

    def test_x_real_ip_invalida_usa_remote_addr(self):
        ev = self.pedir(HTTP_X_REAL_IP="abc", REMOTE_ADDR="10.0.0.2")
        self.assertEqual(ev.ip, "10.0.0.2")

    def test_dispositivo_se_corta_en_300(self):
        self.assertEqual(len(self.pedir(HTTP_USER_AGENT="a" * 400).dispositivo), 300)
        self.assertEqual(len(self.pedir(HTTP_USER_AGENT="a" * 300).dispositivo), 300)
        self.assertEqual(len(self.pedir(HTTP_USER_AGENT="a" * 299).dispositivo), 299)

    def test_fuera_del_middleware_sin_ip_ni_dispositivo(self):
        ev = registrar("alta", usuario=None)
        self.assertIsNone(ev.ip)
        self.assertEqual(ev.dispositivo, "")

    def test_contexto_se_limpia_al_terminar_el_request(self):
        self.pedir(REMOTE_ADDR="10.0.0.2")
        self.assertIsNone(registrar("alta", usuario=None).ip)


class QuienYLineasTest(RegistroBase):
    def test_quien(self):
        self.assertEqual(quien(registrar("alta", usuario=self.admin)), "maria")
        self.assertEqual(quien(registrar("alta", usuario=None)), "Sistema")
        ev = registrar("ejecucion_regla", usuario=None, origen="regla 'Desactivar Rodrigo'")
        self.assertEqual(quien(ev), "Sistema — regla 'Desactivar Rodrigo'")
        fallido = registrar("login_fallido", usuario=None, usuario_intentado="pepe")
        self.assertEqual(quien(fallido), "pepe")

    def cambio(self, entidad, campo, antes, nuevo):
        return registrar(
            "cambio",
            usuario=self.admin,
            entidad=entidad,
            cambios=[{"campo": campo, "anterior": antes, "nuevo": nuevo}],
        )

    def test_lineas_monto(self):
        d = crear_destinatario(usuario=None, nombre="Persona Test", tipo="profesional")
        c = crear_cuenta(usuario=None, tipo="efectivo", destinatario=d)
        ev = self.cambio(c, "tope_mensual", D("500000.00"), D("600000.00"))
        ev.refresh_from_db()
        self.assertEqual(lineas(ev), [("Tope mensual", "$500.000,00", "$600.000,00")])

    def test_lineas_choices_vacio_y_bool(self):
        d = crear_destinatario(usuario=None, nombre="Persona Test", tipo="profesional")
        c = crear_cuenta(usuario=None, tipo="efectivo", destinatario=d)
        ev = self.cambio(c, "estado", "activa", "inactiva")
        self.assertEqual(lineas(ev), [("Estado", "Activa", "Inactiva")])
        ev = self.cambio(c, "titular", None, "")
        self.assertEqual(lineas(ev), [])  # vacío a vacío no es un cambio
        ev = self.cambio(self.admin, "is_active", True, False)
        self.assertEqual(lineas(ev), [("Activo", "Sí", "No")])

    def test_lineas_campo_ajeno_al_modelo_y_fecha(self):
        ev = self.cambio(self.admin, "id_profesional_dentalink", None, 7)
        self.assertEqual(lineas(ev), [("id profesional dentalink", "—", "7")])
        ev = self.cambio(self.admin, "date_joined", "2026-10-31T03:00:00Z", None)
        ev.refresh_from_db()
        self.assertEqual(lineas(ev)[0][1], "31/10/2026 00:00")


class MecanismoSistemaTest(RegistroBase):
    def setUp(self):
        super().setUp()
        d = crear_destinatario(usuario=None, nombre="Persona Test", tipo="profesional")
        self.cuenta = crear_cuenta(usuario=None, tipo="efectivo", destinatario=d)

    def test_envio_dentalink_tres_intentos_en_historial(self):
        from sistema.auditoria.servicios import historial_de

        kw = {"usuario": None, "entidad": self.cuenta, "origen": "reintento de envío"}
        registrar(Evento.Tipo.ENVIO_DENTALINK, exito=False, detalle="timeout", **kw)
        registrar(Evento.Tipo.ENVIO_DENTALINK, exito=False, detalle="timeout", **kw)
        registrar(Evento.Tipo.ENVIO_DENTALINK, exito=True, **kw)
        eventos = list(historial_de(self.cuenta).filter(tipo="envio_dentalink"))
        self.assertEqual(len(eventos), 3)
        self.assertIs(eventos[0].exito, True)
        for e in eventos[1:]:
            self.assertEqual(e.detalle, "timeout")
        for e in eventos:
            self.assertEqual(quien(e), "Sistema — reintento de envío")
            self.assertIsNone(e.ip)

    def test_ejecucion_regla_lineas(self):
        ev = registrar(
            Evento.Tipo.EJECUCION_REGLA,
            usuario=None,
            entidad=self.cuenta,
            cambios=[{"campo": "estado", "anterior": "activa", "nuevo": "inactiva"}],
            exito=True,
            origen="regla 'Desactivar Rodrigo los lunes'",
        )
        self.assertEqual(lineas(ev), [("Estado", "Activa", "Inactiva")])


class FotoArchivoTest(RegistroBase):
    def test_foto_de_cobro_con_comprobante_da_el_nombre_y_el_cambio_se_registra(self):
        from sistema.cobros.tests.fabrica import cobro_prueba
        from sistema.destinatarios.models import Cuenta, Destinatario

        dest = Destinatario.objects.create(nombre="Dest", tipo="clinica", cuit="20000000001")
        cuenta = Cuenta.objects.create(
            destinatario=dest,
            tipo="transferencia",
            alias="alias.uno",
            tope_mensual=D("1000.00"),
            tope_semanal=D("0.00"),
            minimo_por_pago=D("0.00"),
        )
        cobro = cobro_prueba(
            cuenta=cuenta,
            monto=D("10.00"),
            estado="finalizado",
            comprobante="comprobantes/2026/10/a.pdf",
        )
        antes = foto(cobro)
        self.assertEqual(antes["comprobante"], "comprobantes/2026/10/a.pdf")
        cobro.comprobante = "comprobantes/2026/10/b.pdf"
        cobro.save()
        ev = registrar_cambio(cobro, antes, usuario=self.admin)
        cambio = next(c for c in ev.cambios if c["campo"] == "comprobante")
        self.assertEqual(cambio["anterior"], "comprobantes/2026/10/a.pdf")
        self.assertEqual(cambio["nuevo"], "comprobantes/2026/10/b.pdf")
