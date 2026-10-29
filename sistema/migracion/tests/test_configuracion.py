import copy
import json
import shutil
import tempfile
from datetime import datetime
from decimal import Decimal as D
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError

from sistema.auditoria.models import Evento
from sistema.avisos.models import ConfiguracionAvisos, DireccionAviso
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.dentalink.cliente import DentalinkNoDisponible
from sistema.dentalink.models import ConfiguracionDentalink
from sistema.destinatarios.models import Cuenta, Destinatario
from sistema.migracion.tests import fabricas as f
from sistema.terminales.models import Sesion, Terminal

AHORA = datetime(2026, 11, 30, 23, 30, tzinfo=ARG)

CONF = {
    "destinatarios": [
        {"nombre": "Clínica Ejemplo", "tipo": "clinica"},
        {"nombre": "Destinatario Ejemplo", "tipo": "profesional"},
    ],
    "efectivo": {"destinatario": "Clínica Ejemplo", "titular": ""},
    "terminales": [{"nombre": "Payway 1", "proveedor": "Payway", "metodos": ["credito", "debito"]}],
    "cuentas_pos": [
        {
            "destinatario": "Destinatario Ejemplo",
            "proveedor": "Payway",
            "titular": "",
            "objetivo": "500000.00",
            "sesion_en": "Payway 1",
        }
    ],
    "dentalink": {
        "id_prestacion_saldo_a_favor": 1,
        "nombre_prestacion_saldo_a_favor": "Saldo a favor",
        "banco_por_defecto": 3,
    },
    "avisos": {"direcciones": ["duenio@clinica.test"], "margen": 10},
}


def listar(nombre):
    return [{"id": 3, "nombre": "Banco Inventado"}] if nombre == "bancos" else []


class ConfiguracionBase(SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.carpeta = tempfile.mkdtemp(prefix="test-configuracion-")
        self.addCleanup(shutil.rmtree, self.carpeta, ignore_errors=True)
        self.ruta = Path(self.carpeta) / "configuracion.json"

    def conf(self, **cambios):
        conf = copy.deepcopy(CONF)
        conf.update(cambios)
        return conf

    def correr(self, conf=None, crudo=None, comando="cargar_configuracion", **opciones):
        self.ruta.write_text(
            crudo if crudo is not None else json.dumps(conf or CONF), encoding="utf-8"
        )
        salida = StringIO()
        with (
            patch("django.utils.timezone.now", return_value=AHORA),
            patch("sistema.dentalink.cliente.listar", side_effect=opciones.pop("listar", listar)),
        ):
            call_command(comando, datos=str(self.ruta), stdout=salida, **opciones)
        return salida.getvalue()

    def assertError(self, texto, **kw):
        with self.assertRaisesMessage(CommandError, texto):
            self.correr(**kw)
        self.assertFalse(Terminal.objects.exists())
        self.assertFalse(Cuenta.objects.filter(tipo="efectivo").exists())

    def pos(self, **campos):
        c = self.conf()
        c["cuentas_pos"][0].update(campos)
        return c


class CargaCompletaTest(ConfiguracionBase):
    def test_carga_completa(self):
        salida = self.correr()
        self.assertIn(
            "Configuración inicial cargada: 1 cuenta de efectivo, 1 terminal, "
            "1 cuenta de terminal POS, 1 sesión, 1 dirección de aviso.",
            salida,
        )
        efectivo = Cuenta.objects.get(tipo="efectivo")
        self.assertEqual(efectivo.estado, "activa")
        self.assertEqual(efectivo.destinatario.nombre, "Clínica Ejemplo")
        terminal = Terminal.objects.get()
        self.assertEqual(terminal.nombre, "Payway 1")
        self.assertEqual(
            (terminal.acepta_credito, terminal.acepta_debito),
            (True, True),
        )
        self.assertEqual((terminal.acepta_qr, terminal.acepta_link), (False, False))
        pos = Cuenta.objects.get(tipo="terminal_pos")
        self.assertEqual(pos.objetivo, D("500000.00"))
        self.assertEqual(pos.destinatario.nombre, "Destinatario Ejemplo")
        sesion = Sesion.objects.get()
        self.assertEqual((sesion.terminal, sesion.cuenta, sesion.inicio), (terminal, pos, AHORA))
        dl = ConfiguracionDentalink.actual()
        self.assertEqual(dl.id_prestacion_saldo_a_favor, 1)
        self.assertEqual(dl.banco_por_defecto.id_dentalink, 3)
        self.assertEqual(DireccionAviso.objects.get().email, "duenio@clinica.test")
        self.assertEqual(ConfiguracionAvisos.actual().margen, 10)
        altas = Evento.objects.filter(tipo="alta")
        for obj in (efectivo, terminal, sesion, pos):
            self.assertTrue(altas.filter(entidad_id=obj.pk).exists())
        self.assertFalse(altas.exclude(usuario=None).exists())

    def test_dentalink_caido_no_frena_la_carga(self):
        def caido(nombre):
            raise DentalinkNoDisponible()

        salida = self.correr(listar=caido)
        self.assertIn("Dentalink no respondió", salida)
        self.assertEqual(Terminal.objects.count(), 1)
        self.assertIsNone(ConfiguracionDentalink.actual().banco_por_defecto)

    def test_banco_inexistente_con_dentalink_arriba_da_error(self):
        c = self.conf()
        c["dentalink"]["banco_por_defecto"] = 99
        self.assertError("dentalink.banco_por_defecto: no existe el banco 99", conf=c)

    def test_segunda_carga_da_error(self):
        self.correr()
        with self.assertRaisesMessage(CommandError, "La configuración inicial ya está cargada."):
            self.correr()

    def test_verificacion_sigue_en_0_despues_de_la_configuracion(self):
        f.usuario("completar", role="professional")
        ana, perez = f.usuario("ana"), f.usuario("drperez", role="professional")
        f.pago(f.fila("Tincho", alias="alias.uno"), 1000, ana, perez)
        datos = Path(self.carpeta) / "datos.json"
        datos.write_text(
            json.dumps({"para_completar_pagos": "completar", "tipos": {}, "vinculos": {}}),
            encoding="utf-8",
        )
        with patch("django.utils.timezone.now", return_value=AHORA):
            call_command("migrar_prod", datos=str(datos), definitiva=True, stdout=StringIO())
        self.correr()
        salida = StringIO()
        call_command("migrar_prod", datos=str(datos), verificar=True, stdout=salida)
        self.assertIn("Verificación: 0 diferencias", salida.getvalue())


class LimitesYErroresTest(ConfiguracionBase):
    def test_objetivo_0_00_se_rechaza(self):
        self.assertError("cuentas_pos[0]", conf=self.pos(objetivo="0.00"))

    def test_objetivo_0_01_se_acepta(self):
        self.correr(self.pos(objetivo="0.01"))
        self.assertEqual(Cuenta.objects.get(tipo="terminal_pos").objetivo, D("0.01"))

    def test_objetivo_numero_json_se_rechaza(self):
        self.assertError("escribí el monto como texto", crudo=json.dumps(self.pos(objetivo=1)))
        self.assertError(
            "escribí el monto como texto",
            crudo=json.dumps(self.pos()).replace('"500000.00"', "500000.5"),
        )

    def test_destinatario_inexistente_da_error_con_ruta(self):
        self.assertError(
            'cuentas_pos[0].destinatario: no existe el destinatario "Nadie"',
            conf=self.pos(destinatario="Nadie"),
        )

    def test_dos_destinatarios_con_el_mismo_nombre_da_error(self):
        c = self.conf()
        c["destinatarios"].append({"nombre": "destinatario  ejemplo", "tipo": "profesional"})
        # El segundo ya existe con otra grafía: no se crea. Dos previos sí chocan.
        Destinatario.objects.create(nombre="Destinatario Ejemplo", tipo="profesional")
        Destinatario.objects.create(nombre="destinatario  ejemplo", tipo="profesional")
        self.assertError("hay 2 destinatarios con el nombre", conf=c)

    def test_proveedor_inexistente_da_error(self):
        c = self.conf()
        c["terminales"][0]["proveedor"] = "Pay way"
        self.assertError('terminales[0].proveedor: no existe el proveedor "Pay way"', conf=c)

    def test_metodo_desconocido_da_error(self):
        c = self.conf()
        c["terminales"][0]["metodos"] = ["credito", "cheque"]
        self.assertError('método desconocido "cheque"', conf=c)

    def test_metodos_vacios_da_error(self):
        c = self.conf()
        c["terminales"][0]["metodos"] = []
        self.assertError("Elegí al menos un método de cobro.", conf=c)

    def test_margen_100_se_rechaza(self):
        c = self.conf()
        c["avisos"]["margen"] = 100
        self.assertError("avisos:", conf=c)

    def test_margen_99_se_acepta(self):
        c = self.conf()
        c["avisos"]["margen"] = 99
        self.correr(c)
        self.assertEqual(ConfiguracionAvisos.actual().margen, 99)

    def test_falta_una_clave_da_error(self):
        c = self.conf()
        del c["avisos"]
        self.assertError('Falta la clave "avisos" en el archivo de configuración.', conf=c)

    def test_json_roto_da_error(self):
        self.assertError("no es un JSON válido", crudo="{roto")
