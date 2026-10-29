import json
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import DatabaseError

from sistema.auditoria.models import Evento
from sistema.auditoria.servicios import historial_de
from sistema.core.tests import SistemaTestCase
from sistema.destinatarios.models import Cuenta, Destinatario
from sistema.destinatarios.servicios import (
    cambiar_estado_cuenta,
    crear_cuenta,
    crear_destinatario,
    crear_proveedor,
    desactivar_cuentas,
    editar_cuenta,
    editar_destinatario,
)
from sistema.usuarios.servicios import (
    cambiar_activo,
    crear_usuario,
    editar_usuario,
    poner_contrasena,
    vincular_profesional,
)

User = get_user_model()
D = Decimal
CBU = "0000000000000000000001"
CBU2 = "0000000000000000000002"


class Base(SistemaTestCase):
    def setUp(self):
        self.maria = User.objects.create_user(
            username="maria", email="m@test.com", password="x", role="administrator"
        )

    def destinatario(self):
        return crear_destinatario(usuario=self.maria, nombre="Persona Test", tipo="profesional")

    def cuenta(self, d, cbu=CBU, alias="alias.test"):
        return crear_cuenta(
            usuario=self.maria,
            tipo="transferencia",
            destinatario=d,
            alias=alias,
            cbu=cbu,
            tope_mensual=D("500000.00"),
            tope_semanal=D("100000.00"),
        )


class DestinatariosTest(Base):
    def test_crear_destinatario_graba_una_alta(self):
        d = self.destinatario()
        ev = historial_de(d).get()
        self.assertEqual((ev.tipo, ev.usuario), ("alta", self.maria))

    def test_editar_destinatario_graba_observaciones(self):
        d = self.destinatario()
        editar_destinatario(d, usuario=self.maria, observaciones="nota")
        ev = historial_de(d).first()
        self.assertEqual(ev.tipo, "cambio")
        self.assertEqual([c["campo"] for c in ev.cambios], ["observaciones"])

    def test_editar_destinatario_sin_cambios_no_graba(self):
        d = self.destinatario()
        editar_destinatario(d, usuario=self.maria, nombre="Persona Test")
        self.assertEqual(historial_de(d).count(), 1)

    def test_crear_cuenta_graba_una_alta(self):
        c = self.cuenta(self.destinatario())
        self.assertEqual(historial_de(c).get().tipo, "alta")

    def test_editar_cuenta_dos_topes_graba_un_evento(self):
        c = self.cuenta(self.destinatario())
        editar_cuenta(
            c, usuario=self.maria, tope_mensual=D("600000.00"), tope_semanal=D("150000.00")
        )
        ev = historial_de(c).first()
        self.assertEqual(historial_de(c).count(), 2)
        self.assertEqual(ev.usuario, self.maria)
        self.assertEqual(
            ev.cambios,
            [
                {"campo": "tope_mensual", "anterior": "500000.00", "nuevo": "600000.00"},
                {"campo": "tope_semanal", "anterior": "100000.00", "nuevo": "150000.00"},
            ],
        )

    def test_cambiar_un_tope_deja_un_evento_de_la_cuenta_y_ninguno_de_tope_vigente(self):
        c = self.cuenta(self.destinatario())
        editar_cuenta(c, usuario=self.maria, tope_mensual=D("600000.00"))
        self.assertEqual(historial_de(c).count(), 2)
        self.assertEqual(
            historial_de(c).first().cambios,
            [{"campo": "tope_mensual", "anterior": "500000.00", "nuevo": "600000.00"}],
        )
        self.assertFalse(Evento.objects.filter(entidad="destinatarios.topevigente").exists())

    def test_editar_cuenta_con_mismos_valores_no_graba(self):
        c = self.cuenta(self.destinatario())
        editar_cuenta(c, usuario=self.maria, tope_mensual=D("500000.00"))
        self.assertEqual(historial_de(c).count(), 1)

    def test_cambiar_estado_cuenta_al_mismo_estado_no_graba(self):
        c = self.cuenta(self.destinatario())
        cambiar_estado_cuenta(c, Cuenta.Estado.ACTIVA, usuario=self.maria)
        self.assertEqual(historial_de(c).count(), 1)

    def test_cambiar_estado_cuenta_graba_estado(self):
        c = self.cuenta(self.destinatario())
        cambiar_estado_cuenta(c, Cuenta.Estado.INACTIVA, usuario=self.maria)
        ev = historial_de(c).first()
        self.assertIn("estado", [x["campo"] for x in ev.cambios])

    def test_desactivar_cuentas_graba_un_evento_por_cuenta(self):
        d = self.destinatario()
        a = self.cuenta(d)
        b = self.cuenta(d, cbu=CBU2, alias="alias.dos")
        desactivar_cuentas(d, usuario=self.maria)
        self.assertEqual(historial_de(a).count(), 2)
        # b tiene un evento más: al salir a, b sube de posición (004)
        self.assertEqual(historial_de(b).count(), 3)

    def test_crear_proveedor_graba_una_alta(self):
        p = crear_proveedor(usuario=self.maria, nombre="Proveedor Test")
        self.assertEqual(historial_de(p).get().tipo, "alta")

    def test_historial_es_posterior_a_fecha_cambio(self):
        d = self.destinatario()
        c = self.cuenta(d)
        p = crear_proveedor(usuario=self.maria, nombre="Proveedor Test")
        editar_cuenta(c, usuario=self.maria, tope_mensual=D("600000.00"))
        editar_destinatario(d, usuario=self.maria, observaciones="x")
        for obj in (Destinatario.objects.get(pk=d.pk), Cuenta.objects.get(pk=c.pk), p):
            obj.refresh_from_db()
            self.assertGreaterEqual(historial_de(obj).first().fecha, obj.fecha_cambio)

    def test_guardar_sin_cambios_no_mueve_fecha_cambio_de_destinatario_ni_cuenta(self):
        d = self.destinatario()
        c = self.cuenta(d)
        fd, fc = d.fecha_cambio, c.fecha_cambio
        editar_destinatario(d, usuario=self.maria, nombre="Persona Test")
        editar_cuenta(c, usuario=self.maria, tope_mensual=D("500000.00"))
        d.refresh_from_db()
        c.refresh_from_db()
        self.assertEqual((d.fecha_cambio, c.fecha_cambio), (fd, fc))
        self.assertGreaterEqual(historial_de(c).first().fecha, c.fecha_cambio)


class UsuariosTest(Base):
    def nuevo(self, password="Clave-Falsa-123", role="operator"):
        return crear_usuario(usuario=self.maria, username="ana", password=password, role=role)

    def test_crear_usuario_graba_alta_sin_password_con_observaciones(self):
        ev = historial_de(self.nuevo()).get()
        campos = [c["campo"] for c in ev.cambios]
        self.assertEqual(ev.tipo, "alta")
        self.assertNotIn("password", campos)
        self.assertIn("observaciones", campos)
        self.assertIn("fecha_activacion", campos)

    def test_editar_usuario_observaciones_graba_un_cambio(self):
        u = self.nuevo()
        editar_usuario(u, usuario=self.maria, observaciones="nota")
        ev = historial_de(u).first()
        self.assertEqual([c["campo"] for c in ev.cambios], ["observaciones"])

    def test_cambiar_activo_false_graba_fecha_desactivacion(self):
        u = self.nuevo()
        cambiar_activo(u, False, usuario=self.maria)
        u.perfil.refresh_from_db()
        self.assertIsNotNone(u.perfil.fecha_desactivacion)
        campos = [c["campo"] for c in historial_de(u).first().cambios]
        self.assertIn("is_active", campos)

    def test_cambiar_activo_al_mismo_estado_no_mueve_fechas(self):
        u = self.nuevo()
        cambiar_activo(u, True, usuario=self.maria)
        u.perfil.refresh_from_db()
        self.assertIsNone(u.perfil.fecha_desactivacion)
        self.assertEqual(historial_de(u).count(), 1)

    def test_guardar_usuario_sin_cambios_no_mueve_fecha_cambio_del_perfil(self):
        u = self.nuevo()
        fecha = u.perfil.fecha_cambio
        editar_usuario(u, usuario=self.maria, first_name="")
        cambiar_activo(u, True, usuario=self.maria)
        u.perfil.refresh_from_db()
        self.assertEqual(u.perfil.fecha_cambio, fecha)
        self.assertEqual(historial_de(u).count(), 1)
        self.assertGreaterEqual(historial_de(u).first().fecha, u.perfil.fecha_cambio)

    def test_vincular_profesional_graba_el_id(self):
        u = self.nuevo(role="professional")
        vincular_profesional(
            u,
            usuario=self.maria,
            id_profesional_dentalink=101,
            nombre_profesional_dentalink="Dra. X",
        )
        campos = [c["campo"] for c in historial_de(u).first().cambios]
        self.assertIn("id_profesional_dentalink", campos)

    def test_poner_contrasena_graba_detalle_sin_cambios(self):
        u = self.nuevo()
        poner_contrasena(u, "Otra-Falsa-456", usuario=self.maria)
        ev = historial_de(u).first()
        self.assertEqual(ev.detalle, "Contraseña cambiada")
        self.assertEqual(ev.cambios, [])

    def test_ningun_evento_contiene_contrasenas(self):
        u = self.nuevo()
        poner_contrasena(u, "Otra-Falsa-456", usuario=self.maria)
        u.refresh_from_db()
        texto = " ".join(json.dumps(e.cambios) + e.detalle for e in Evento.objects.all())
        for prohibido in ("Clave-Falsa-123", "Otra-Falsa-456", u.password, "pbkdf2"):
            self.assertNotIn(prohibido, texto)


class AtomicidadTest(Base):
    def falla(self):
        return mock.patch(
            "sistema.auditoria.servicios.Evento.objects.create", side_effect=DatabaseError
        )

    def test_editar_cuenta_con_evento_roto_no_cambia_el_tope(self):
        c = self.cuenta(self.destinatario())
        with self.falla(), self.assertRaises(DatabaseError):
            editar_cuenta(c, usuario=self.maria, tope_mensual=D("600000.00"))
        c.refresh_from_db()
        self.assertEqual(c.tope_mensual, D("500000.00"))

    def test_crear_usuario_con_evento_roto_no_crea_usuario(self):
        with self.falla(), self.assertRaises(DatabaseError):
            crear_usuario(usuario=self.maria, username="ana", password="x1", role="operator")
        self.assertFalse(User.objects.filter(username="ana").exists())

    def test_crear_destinatario_con_evento_roto_no_crea_destinatario(self):
        with self.falla(), self.assertRaises(DatabaseError):
            self.destinatario()
        self.assertFalse(Destinatario.objects.exists())
