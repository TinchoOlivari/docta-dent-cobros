from datetime import datetime, timezone
from decimal import Decimal

from django.urls import reverse

from payment_instructions.models import User
from sistema.auditoria.forms import GRUPOS
from sistema.auditoria.models import Evento
from sistema.auditoria.servicios import ENTIDADES, lineas, lineas_de_pagina
from sistema.core.tests import SistemaTestCase
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario


def cambio(campo, anterior, nuevo):
    return {"campo": campo, "anterior": anterior, "nuevo": nuevo}


class AuditoriaLegible020Test(SistemaTestCase):
    def setUp(self):
        self.ana = User.objects.create_user(
            "ana",
            email="a@example.com",
            password="x",
            role="administrator",
            first_name="Ana",
            last_name="Admin",
        )
        self.dest = crear_destinatario(
            usuario=None, nombre="Destinatario Ejemplo 1", tipo="clinica"
        )
        self.c1 = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=self.dest,
            alias="ejemplo.uno",
            tope_mensual=Decimal("1000.00"),
        )
        self.c2 = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=self.dest,
            alias="ejemplo.dos",
            tope_mensual=Decimal("1000.00"),
        )
        self.client.force_login(self.ana)

    def ev(self, tipo, entidad, cambios, pk=1):
        return Evento(tipo=tipo, entidad=entidad, entidad_id=pk, cambios=cambios)

    def test_alta_de_cobro_muestra_solo_los_campos_con_valor(self):
        campos = {f"c{i}": None for i in range(26)}
        cambios = [cambio(k, None, v) for k, v in campos.items()]
        reales = [
            cambio("estado", None, "pendiente"),
            cambio("metodo", None, "transferencia"),
            cambio("nombre_paciente", None, "Paciente Ejemplo"),
            cambio("dni_paciente", None, "1"),
            cambio("migrado", None, False),
            cambio("revisado", None, False),
            cambio("faltan_items", None, True),
        ]
        e = self.ev("alta", "cobros.cobro", cambios + [cambio("observaciones", None, "")] + reales)
        self.assertEqual(len(lineas(e)), 7)

    def test_cambio_solo_lista_lo_que_cambio(self):
        e = self.ev(
            "cambio",
            "cobros.cobro",
            [cambio("estado", "pendiente", "finalizado"), cambio("monto", "5.00", "5.00")],
        )
        self.assertEqual([x[0] for x in lineas(e)], ["Estado"])

    def test_cuenta_se_muestra_por_nombre_y_no_por_pk(self):
        e = self.ev("cambio", "cobros.cobro", [cambio("cuenta_id", self.c1.pk, self.c2.pk)])
        self.assertEqual(
            lineas(e),
            [
                (
                    "Cuenta",
                    "Destinatario Ejemplo 1 — ejemplo.uno",
                    "Destinatario Ejemplo 1 — ejemplo.dos",
                )
            ],
        )

    def test_usuario_por_nombre_completo_y_vacio_con_guion(self):
        e = self.ev("cambio", "cobros.cobro", [cambio("creado_por_id", None, self.ana.pk)])
        self.assertEqual(lineas(e), [("Creado por", "—", "Ana Admin")])

    def test_usuario_sin_nombre_muestra_el_usuario(self):
        sin = User.objects.create_user("luis", email="l@example.com", password="x")
        e = self.ev("cambio", "cobros.cobro", [cambio("creado_por_id", None, sin.pk)])
        self.assertEqual(lineas(e)[0][2], "luis")

    def test_objeto_inexistente_muestra_numero(self):
        e = self.ev("cambio", "cobros.cobro", [cambio("cuenta_id", 4, self.c1.pk)])
        self.assertEqual(lineas(e)[0][1], "N.º 4")

    def test_monto_fecha_booleano_y_archivo(self):
        e = self.ev(
            "cambio",
            "cobros.cobro",
            [
                cambio("monto", "0.00", "1500.50"),
                cambio("fecha", "2026-10-28T15:30:45+00:00", "2026-10-28T18:00:00+00:00"),
                cambio("migrado", False, True),
                cambio("comprobante", "", "comprobantes/x/y.pdf"),
            ],
        )
        r = {x[0]: x for x in lineas(e)}
        self.assertEqual(r["Monto"][2], "$1.500,50")
        self.assertEqual(r["Fecha"][1], "28/10/2026 12:30")  # hora Argentina, sin segundos
        self.assertEqual((r["Migrado"][1], r["Migrado"][2]), ("No", "Sí"))
        self.assertEqual((r["Comprobante"][1], r["Comprobante"][2]), ("—", "Archivo adjunto"))

    def test_consultas_no_crecen_con_la_cantidad_de_eventos(self):
        def crear(n):
            for _ in range(n):
                Evento.objects.create(
                    tipo="cambio",
                    fecha=datetime(2026, 10, 1, tzinfo=timezone.utc),
                    usuario=self.ana,
                    entidad="cobros.cobro",
                    entidad_id=1,
                    cambios=[
                        cambio("cuenta_id", self.c1.pk, self.c2.pk),
                        cambio("creado_por_id", None, self.ana.pk),
                    ],
                )
            return list(Evento.objects.filter(entidad="cobros.cobro").select_related("usuario"))

        eventos10 = crear(10)
        with self.assertNumQueries(2):
            lineas_de_pagina(eventos10)
        eventos50 = crear(40)
        self.assertEqual(len(eventos50), 50)
        with self.assertNumQueries(2):
            lineas_de_pagina(eventos50)

    def test_cada_entidad_esta_en_un_solo_grupo(self):
        claves = [k for ks in GRUPOS.values() for k in ks]
        self.assertEqual(sorted(claves), sorted(ENTIDADES))
        self.assertEqual(len(claves), len(set(claves)))

    def test_usuario_inexistente_muestra_error_y_cero_eventos(self):
        Evento.objects.create(
            tipo="cambio", fecha=datetime(2026, 10, 1, tzinfo=timezone.utc), entidad="cobros.cobro"
        )
        r = self.client.get(reverse("auditoria:eventos"), {"usuario": "999"})
        self.assertEqual(len(r.context["pagina"]), 0)
