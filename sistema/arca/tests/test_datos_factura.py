from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from sistema.arca import servicios as arca
from sistema.arca.cliente import Resultado
from sistema.arca.models import ComprobanteFiscal
from sistema.arca.servicios import FaltaDecision, editar_datos_factura
from sistema.arca.tests.test_nota_credito import Base
from sistema.auditoria.servicios import historial_de
from sistema.cobros.tests.test_efectivo import AHORA

D = Decimal
PREGUNTA = (
    "ARCA no permite editar una factura emitida. "
    "¿Emitir nota de crédito por $65.000,00 y una factura nueva?"
)


class DatosBase(Base):
    def editar_datos(self, cobro, **kw):
        kw.setdefault("usuario", self.admin)
        return editar_datos_factura(cobro, **kw)

    def pedido(self):
        return self.arca.solicitar_cae.call_args.args[2]

    def factura_con_error(self):
        cobro, factura = self.facturado(emitir=False)
        self.arca.responder(Resultado("rechazado", error="10016: dato inválido"))
        self.assertEqual(arca.emitir(factura.pk), "error")
        factura.refresh_from_db()
        return cobro, factura

    def reintentar(self, factura):
        arca.reintentar(factura, usuario=self.admin)


class SinEmitirTest(DatosBase):
    def test_error_cambia_concepto_y_descripcion_y_el_reintento_los_usa(self):
        cobro, factura = self.factura_con_error()
        self.editar_datos(cobro, concepto="productos_y_servicios", descripcion="Insumos y consulta")
        factura.refresh_from_db()
        self.assertEqual(factura.estado, "error")  # editar no reintenta
        self.reintentar(factura)
        self.assertEqual(arca.emitir(factura.pk), "emitido")
        self.assertEqual(self.pedido()["concepto"], 3)
        factura.refresh_from_db()
        self.assertEqual(factura.descripcion, "Insumos y consulta")

    def test_pendiente_cambia_los_datos_y_deja_un_evento_con_el_usuario(self):
        cobro, factura = self.facturado(emitir=False)
        self.editar_datos(cobro, receptor_nombre="Paciente Inventado", doc_tipo=80,
                          doc_numero="20000000001")  # fmt: skip
        factura.refresh_from_db()
        self.assertEqual(factura.receptor_nombre, "Paciente Inventado")
        evento = historial_de(factura).filter(tipo="cambio").latest("pk")
        self.assertEqual(evento.usuario, self.admin)
        campos = {c["campo"] for c in evento.cambios}
        self.assertEqual(campos, {"receptor_nombre", "doc_tipo", "doc_numero"})

    def test_documento_cargado_a_mano_entra_en_el_pedido(self):
        cobro, factura = self.facturado(emitir=False)
        self.editar_datos(cobro, doc_tipo=80, doc_numero="20000000001")
        self.assertEqual(arca.emitir(factura.pk), "emitido")
        pedido = self.pedido()
        self.assertEqual((pedido["doc_tipo"], pedido["doc_numero"]), (80, "20000000001"))

    def test_fechas_del_servicio_entran_en_el_pedido(self):
        cobro, factura = self.facturado(emitir=False)
        self.editar_datos(
            cobro, servicio_desde=date(2026, 10, 1), servicio_hasta=date(2026, 10, 15)
        )
        arca.emitir(factura.pk)
        self.assertEqual(self.pedido()["servicio_desde"], date(2026, 10, 1))
        self.assertEqual(self.pedido()["servicio_hasta"], date(2026, 10, 15))

    def test_sin_cambios_no_deja_evento(self):
        cobro, factura = self.facturado(emitir=False)
        self.editar_datos(cobro, concepto=factura.concepto)
        self.assertEqual(historial_de(factura).filter(tipo="cambio").count(), 0)

    def test_campo_fuera_de_la_lista_no_se_edita(self):
        cobro, factura = self.facturado(emitir=False)
        for campo in ("total", "estado", "cae"):
            with self.subTest(campo=campo):
                with self.assertRaisesMessage(ValidationError, f"El campo {campo} no se edita."):
                    self.editar_datos(cobro, **{campo: "x"})

    def test_cobro_sin_factura_vigente(self):
        cobro, factura = self.facturado(emitir=False)
        ComprobanteFiscal.objects.filter(pk=factura.pk).update(vigente=False)
        with self.assertRaisesMessage(ValidationError, "El cobro no tiene una factura vigente."):
            self.editar_datos(cobro, concepto="productos")

    def test_solo_el_administrador_edita(self):
        cobro, _ = self.facturado(emitir=False)
        operador = get_user_model().objects.create_user(
            "op", email="op@example.com", role="operator"
        )
        with self.assertRaises(PermissionDenied):
            self.editar_datos(cobro, usuario=operador, concepto="productos")


class ValidacionTest(DatosBase):
    def assertRechaza(self, cobro, factura, mensaje, **datos):
        antes = ComprobanteFiscal.objects.get(pk=factura.pk)
        with self.assertRaisesMessage(ValidationError, mensaje):
            self.editar_datos(cobro, **datos)
        factura.refresh_from_db()
        self.assertEqual(factura.doc_numero, antes.doc_numero)
        self.assertEqual(factura.doc_tipo, antes.doc_tipo)

    def test_servicio_desde_posterior_a_hasta_falla_y_el_mismo_dia_pasa(self):
        cobro, factura = self.facturado(emitir=False)
        self.assertRechaza(
            cobro,
            factura,
            "El inicio del servicio no puede ser posterior al fin.",
            servicio_desde=date(2026, 10, 16),
            servicio_hasta=date(2026, 10, 15),
        )
        self.editar_datos(
            cobro, servicio_desde=date(2026, 10, 15), servicio_hasta=date(2026, 10, 15)
        )
        factura.refresh_from_db()
        self.assertEqual(factura.servicio_desde, factura.servicio_hasta)

    def test_cuit_de_10_y_12_digitos_falla_y_de_11_pasa(self):
        cobro, factura = self.facturado(emitir=False)
        self.assertRechaza(
            cobro, factura, "El CUIT lleva 11 dígitos.", doc_tipo=80, doc_numero="2000000001"
        )
        with self.assertRaises(ValidationError):  # 12 dígitos: no entra en el campo
            self.editar_datos(cobro, doc_tipo=80, doc_numero="200000000011")
        self.editar_datos(cobro, doc_tipo=80, doc_numero="20000000001")

    def test_dni_en_cero_o_vacio_falla_y_con_un_digito_pasa(self):
        cobro, factura = self.facturado(emitir=False)
        for numero in ("", "0", "000"):
            with self.subTest(numero=numero):
                self.assertRechaza(
                    cobro, factura, "Escribí el número de DNI.", doc_tipo=96, doc_numero=numero
                )
        self.editar_datos(cobro, doc_tipo=96, doc_numero="1")

    def test_documento_con_letras_o_espacios_falla(self):
        cobro, factura = self.facturado(emitir=False)
        for numero in ("3011122A", "30 111 222", "-30111222"):
            with self.subTest(numero=numero):
                self.assertRechaza(
                    cobro, factura, "El número de documento lleva solo dígitos.",
                    doc_tipo=96, doc_numero=numero,
                )  # fmt: skip

    def test_sin_identificar_acepta_vacio_y_cero_y_rechaza_otro_numero(self):
        cobro, factura = self.facturado(emitir=False)
        self.assertRechaza(
            cobro,
            factura,
            "Sin identificar no lleva número de documento.",
            doc_tipo=99,
            doc_numero="5",
        )
        self.editar_datos(cobro, doc_tipo=99, doc_numero="0")
        self.editar_datos(cobro, doc_tipo=99, doc_numero="")

    def test_numero_sin_tipo_y_tipo_desconocido_fallan(self):
        cobro, factura = self.facturado(emitir=False)
        self.assertRechaza(cobro, factura, "Elegí el tipo de documento.", doc_numero="30111222")
        self.assertRechaza(cobro, factura, "El tipo de documento no es válido.", doc_tipo=5)

    def test_nombre_de_200_caracteres_pasa_y_de_201_falla(self):
        cobro, factura = self.facturado(emitir=False)
        self.editar_datos(cobro, receptor_nombre="a" * 200)
        with self.assertRaises(ValidationError):
            self.editar_datos(cobro, receptor_nombre="a" * 201)

    def test_concepto_desconocido_falla(self):
        cobro, factura = self.facturado(emitir=False)
        with self.assertRaises(ValidationError):
            self.editar_datos(cobro, concepto="regalos")

    def test_el_documento_invalido_no_deja_nota_de_credito_ni_factura_nueva(self):
        cobro, factura = self.facturado()
        with self.assertRaisesMessage(ValidationError, "El CUIT lleva 11 dígitos."):
            self.editar_datos(cobro, nota_credito=True, doc_tipo=80, doc_numero="123")
        self.assertEqual(ComprobanteFiscal.objects.count(), 1)
        factura.refresh_from_db()
        self.assertTrue(factura.vigente)


class NoCambiaTest(DatosBase):
    def test_en_curso_no_cambia_de_datos(self):
        cobro, factura = self.facturado(emitir=False)
        ComprobanteFiscal.objects.filter(pk=factura.pk).update(en_curso_desde=AHORA)
        with self.assertRaisesMessage(ValidationError, "se está emitiendo"):
            self.editar_datos(cobro, concepto="productos")
        factura.refresh_from_db()
        self.assertEqual(factura.concepto, "servicios")

    def test_dudoso_pendiente_no_cambia_de_datos(self):
        cobro, factura = self.facturado(emitir=False)
        ComprobanteFiscal.objects.filter(pk=factura.pk).update(numero_intentado=42)
        with self.assertRaisesMessage(ValidationError, "se está emitiendo"):
            self.editar_datos(cobro, concepto="productos")

    def test_dudoso_con_error_pide_reintentar_primero(self):
        cobro, factura = self.factura_con_error()
        ComprobanteFiscal.objects.filter(pk=factura.pk).update(numero_intentado=42)
        with self.assertRaisesMessage(ValidationError, "quedó sin confirmar en ARCA"):
            self.editar_datos(cobro, concepto="productos")
        factura.refresh_from_db()
        self.assertEqual(factura.concepto, "servicios")


class EmitidaTest(DatosBase):
    def test_sin_respuesta_pregunta_y_no_cambia_nada(self):
        cobro, factura = self.facturado()
        with self.assertRaises(FaltaDecision) as c:
            self.editar_datos(cobro, concepto="productos")
        self.assertEqual(c.exception.pregunta, PREGUNTA)
        self.assertEqual(ComprobanteFiscal.objects.count(), 1)
        factura.refresh_from_db()
        self.assertEqual(factura.concepto, "servicios")

    def test_si_emite_la_nota_de_65000_y_la_factura_nueva_con_los_datos_nuevos(self):
        cobro, factura = self.facturado()
        with self.captureOnCommitCallbacks(execute=True):
            nueva = self.editar_datos(
                cobro,
                nota_credito=True,
                concepto="productos",
                doc_tipo=80,
                doc_numero="20000000001",
            )
        factura.refresh_from_db()
        (nota,) = self.notas()
        self.assertFalse(factura.vigente)
        self.assertEqual(factura.concepto, "servicios")  # la emitida no se edita
        self.assertEqual(nota.asociado, factura)
        self.assertEqual(nueva, self.facturas().get(vigente=True))
        self.assertEqual(
            (nueva.concepto, nueva.doc_tipo, nueva.doc_numero), ("productos", 80, "20000000001")
        )
        self.assertEqual(nueva.estado, "pendiente")
        self.assertEqual(self.emitir.call_count, 2)
        self.assertEqual(arca.emitir(nota.pk), "emitido")
        self.assertEqual(arca.emitir(nueva.pk), "emitido")
        nota.refresh_from_db()
        nueva.refresh_from_db()
        self.assertEqual((nota.total, nueva.total), (D("65000.00"), D("65000.00")))
        pedido = self.pedido()
        self.assertEqual((pedido["concepto"], pedido["doc_tipo"]), (1, 80))

    def test_no_deja_todo_igual_y_registra_la_decision(self):
        cobro, factura = self.facturado()
        self.editar_datos(cobro, nota_credito=False, concepto="productos")
        factura.refresh_from_db()
        self.assertTrue(factura.vigente)
        self.assertEqual(factura.concepto, "servicios")
        self.assertEqual(ComprobanteFiscal.objects.count(), 1)
        evento = historial_de(factura).filter(tipo="cambio").latest("pk")
        self.assertEqual(evento.detalle, "Nota de crédito: no")

    def test_sin_cambios_no_pregunta(self):
        cobro, factura = self.facturado()
        self.editar_datos(cobro, concepto=factura.concepto, descripcion=factura.descripcion)
        self.assertEqual(self.notas().count(), 0)


class PantallaTest(DatosBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def url(self, cobro):
        return reverse("arca:datos_factura", args=[cobro.pk])

    def datos(self, **extra):
        return {"concepto": "servicios", "descripcion": "Servicios odontológicos"} | extra

    def test_guarda_por_post_y_vuelve_al_cobro(self):
        cobro, factura = self.facturado(emitir=False)
        r = self.client.post(
            self.url(cobro),
            self.datos(concepto="productos", descripcion="Insumos", doc_tipo="80",
                       doc_numero="20000000001", servicio_desde="2026-10-01",
                       servicio_hasta="2026-10-15"),
        )  # fmt: skip
        self.assertRedirects(r, reverse("cobros:detalle", args=[cobro.pk]))
        factura.refresh_from_db()
        self.assertEqual((factura.concepto, factura.descripcion), ("productos", "Insumos"))
        self.assertEqual((factura.doc_tipo, factura.doc_numero), (80, "20000000001"))
        self.assertEqual(factura.servicio_hasta, date(2026, 10, 15))

    def test_dato_invalido_muestra_el_error_en_espanol_y_no_guarda(self):
        cobro, factura = self.facturado(emitir=False)
        r = self.client.post(self.url(cobro), self.datos(doc_tipo="80", doc_numero="123"))
        self.assertContains(r, "El CUIT no es válido: revisá el último dígito.")
        r = self.client.post(
            self.url(cobro), self.datos(servicio_desde="2026-10-16", servicio_hasta="2026-10-15")
        )
        self.assertContains(r, "El inicio del servicio no puede ser posterior al fin.")
        factura.refresh_from_db()
        self.assertIsNone(factura.doc_tipo)
        self.assertIsNone(factura.servicio_desde)

    def test_emitida_sin_respuesta_repite_la_pregunta_y_no_cambia_nada(self):
        cobro, factura = self.facturado()
        r = self.client.post(self.url(cobro), self.datos(concepto="productos"))
        self.assertContains(r, PREGUNTA)
        self.assertEqual(ComprobanteFiscal.objects.count(), 1)
        factura.refresh_from_db()
        self.assertEqual(factura.concepto, "servicios")

    def test_emitida_con_si_crea_la_nota_y_la_factura_nueva(self):
        cobro, factura = self.facturado()
        r = self.client.post(self.url(cobro), self.datos(concepto="productos", nota_credito="si"))
        self.assertRedirects(r, reverse("cobros:detalle", args=[cobro.pk]))
        self.assertEqual(self.notas().count(), 1)
        self.assertEqual(self.facturas().get(vigente=True).concepto, "productos")

    def test_cobro_sin_factura_da_404_y_el_operador_403(self):
        cobro, factura = self.facturado(emitir=False)
        ComprobanteFiscal.objects.filter(pk=factura.pk).update(vigente=False)
        self.assertEqual(self.client.get(self.url(cobro)).status_code, 404)
        ComprobanteFiscal.objects.filter(pk=factura.pk).update(vigente=True)
        get_user_model().objects.create_user("op", email="op@example.com", role="operator")
        self.client.force_login(get_user_model().objects.get(username="op"))
        self.assertEqual(self.client.get(self.url(cobro)).status_code, 403)
        self.assertEqual(self.client.post(self.url(cobro), self.datos()).status_code, 403)
