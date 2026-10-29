import json
import os
import shutil
import tempfile

from django.core.management.base import CommandError

from sistema.core.tests import SistemaTestCase
from sistema.migracion.servicios import Informe, leer_datos
from sistema.migracion.tests.fabricas import usuario


class LeerDatosTest(SistemaTestCase):
    def setUp(self):
        super().setUp()
        usuario("completar")
        usuario("drperez", role="professional")
        usuario("operador1")

    def archivo(self, contenido):
        directorio = tempfile.mkdtemp()
        ruta = os.path.join(directorio, "datos.json")
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(contenido if isinstance(contenido, str) else json.dumps(contenido))
        self.addCleanup(shutil.rmtree, directorio, ignore_errors=True)
        return ruta

    def base(self, **cambios):
        datos = {
            "para_completar_pagos": "completar",
            "tipos": {"Clínica  Ejemplo": "clinica"},
            "vinculos": {"drperez": {"id": 12, "nombre": "Dr. Pérez"}},
        }
        return datos | cambios

    def test_json_valido_devuelve_las_3_claves_y_normaliza_tipos(self):
        datos = leer_datos(self.archivo(self.base()))
        self.assertEqual(set(datos), {"para_completar_pagos", "tipos", "vinculos"})
        self.assertEqual(datos["tipos"], {"clínica ejemplo": "clinica"})

    def test_tipos_y_vinculos_vacios_se_aceptan(self):
        datos = leer_datos(self.archivo(self.base(tipos={}, vinculos={})))
        self.assertEqual(datos["vinculos"], {})

    def test_archivo_que_no_existe_falla(self):
        with self.assertRaisesMessage(CommandError, "No existe el archivo"):
            leer_datos("/ruta/que/no/existe.json")

    def test_json_roto_falla(self):
        with self.assertRaisesMessage(CommandError, "no es un JSON válido"):
            leer_datos(self.archivo("{roto"))

    def test_falta_vinculos_falla(self):
        datos = self.base()
        del datos["vinculos"]
        with self.assertRaisesMessage(CommandError, '"vinculos"'):
            leer_datos(self.archivo(datos))

    def test_tipo_socio_falla_con_el_nombre(self):
        with self.assertRaisesMessage(CommandError, "Clínica X"):
            leer_datos(self.archivo(self.base(tipos={"Clínica X": "socio"})))

    def test_vinculo_a_usuario_que_no_existe_falla(self):
        vinculos = {"fantasma": {"id": 1, "nombre": "X"}}
        with self.assertRaisesMessage(CommandError, "fantasma"):
            leer_datos(self.archivo(self.base(vinculos=vinculos)))

    def test_vinculo_a_operador_falla(self):
        vinculos = {"operador1": {"id": 1, "nombre": "X"}}
        with self.assertRaisesMessage(CommandError, "operador1"):
            leer_datos(self.archivo(self.base(vinculos=vinculos)))

    def test_vinculo_con_id_no_entero_falla(self):
        vinculos = {"drperez": {"id": "12", "nombre": "X"}}
        with self.assertRaisesMessage(CommandError, "drperez"):
            leer_datos(self.archivo(self.base(vinculos=vinculos)))

    def test_vinculo_con_nombre_vacio_falla(self):
        vinculos = {"drperez": {"id": 12, "nombre": " "}}
        with self.assertRaisesMessage(CommandError, "drperez"):
            leer_datos(self.archivo(self.base(vinculos=vinculos)))

    def test_para_completar_pagos_que_no_existe_falla(self):
        with self.assertRaisesMessage(CommandError, "nadie"):
            leer_datos(self.archivo(self.base(para_completar_pagos="nadie")))


class InformeTest(SistemaTestCase):
    def test_un_conflicto_bloquea_y_la_verificacion_no_corre(self):
        informe = Informe(modo="revisión", conflictos=['Alias "a": filas 7 y 9'])
        texto = informe.texto()
        self.assertTrue(informe.bloquea)
        self.assertIn("Conflictos: 1", texto)
        self.assertIn("Verificación: No corre: hay conflictos o errores", texto)

    def test_sin_conflictos_ni_errores_no_bloquea(self):
        self.assertFalse(Informe(modo="revisión").bloquea)

    def test_un_error_bloquea(self):
        self.assertTrue(Informe(modo="revisión", errores=["x"]).bloquea)

    def test_montos_se_muestran_con_pesos(self):
        from decimal import Decimal

        informe = Informe(
            modo="revisión",
            montos={("alias.uno", "2026-10"): Decimal("15000")},
            diferencias=[],
        )
        texto = informe.texto()
        self.assertIn("alias.uno 2026-10: $15.000,00", texto)
        self.assertIn("Verificación: 0 diferencias", texto)
