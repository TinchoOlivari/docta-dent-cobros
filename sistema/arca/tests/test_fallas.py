from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import override_settings
from django.urls import reverse

from sistema.arca import servicios as arca
from sistema.arca.cliente import SIN_PRODUCCION, Resultado
from sistema.arca.models import ComprobanteFiscal, Emisor
from sistema.arca.tests.fabricas import (
    AHORA,
    ArcaFalso,
    cobro_facturable,
    datos_de_arca,
    emisor_prueba,
    factura_prueba,
    atrapar,
    fugas_en_traceback,
)
from sistema.auditoria.models import Evento
from sistema.auditoria.servicios import historial_de
from sistema.core.tests import SistemaTestCase

RED = Resultado("reintentable", error="No se pudo conectar con ARCA (TimeoutError).")
CORTE = Resultado("dudoso", error="ARCA no respondió a tiempo (TimeoutError).")
MINUTO = timedelta(minutes=1)


class FallasTest(SistemaTestCase):
    def setUp(self):
        super().setUp()
        reloj = mock.patch("django.utils.timezone.now", return_value=AHORA)
        reloj.start()
        self.addCleanup(reloj.stop)
        self.arca = ArcaFalso(self)
        self.emisor = emisor_prueba()

    def factura(self, emisor=None, dni="30111222", **campos):
        cobro = cobro_facturable(dni_paciente=dni)
        return factura_prueba(cobro, emisor=emisor or self.emisor, **campos)

    def recargar(self, factura):
        return ComprobanteFiscal.objects.get(pk=factura.pk)

    def eventos(self, factura):
        return Evento.objects.filter(tipo=Evento.Tipo.EMISION_ARCA, entidad_id=factura.pk)

    # --- reintentos (FR-024) ---

    def test_falla_reintentable_deja_pendiente_con_el_proximo_intento_y_el_cobro_intacto(self):
        self.arca.responder(RED)
        f = self.factura()
        self.assertEqual(arca.emitir(f.pk), "pendiente")
        f = self.recargar(f)
        self.assertEqual((f.intentos, f.proximo_intento), (1, AHORA + MINUTO))
        self.assertIsNone(f.en_curso_desde)
        self.assertIsNone(f.numero_intentado)  # no salió nada: el número queda libre
        f.cobro.refresh_from_db()
        self.assertEqual(f.cobro.estado, "finalizado")

    def test_cinco_fallas_esperan_1_2_4_8_y_16_minutos_y_la_sexta_es_error(self):
        self.arca.responder(*[RED] * 6)
        f = self.factura()
        momento = AHORA
        for intento, espera in enumerate([1, 2, 4, 8, 16], start=1):
            self.assertEqual(arca.emitir(f.pk, ahora=momento), "pendiente")
            f = self.recargar(f)
            self.assertEqual(f.intentos, intento)
            self.assertEqual(f.proximo_intento, momento + timedelta(minutes=espera))
            momento = f.proximo_intento
        self.assertEqual(arca.emitir(f.pk, ahora=momento), "error")
        f = self.recargar(f)
        self.assertEqual(f.intentos, 6)
        self.assertIsNone(f.proximo_intento)
        self.assertIn("ARCA no respondió después de 6 intentos", f.motivo)
        self.assertIn("No se pudo conectar", f.motivo)
        self.assertEqual(self.eventos(f).count(), 6)
        self.assertEqual(arca.emitir(f.pk, ahora=momento + MINUTO), "omitido")

    def test_no_emite_un_segundo_antes_del_proximo_intento_y_si_justo_en_el_proximo_intento(self):
        self.arca.responder(RED)
        f = self.factura()
        arca.emitir(f.pk)
        proximo = AHORA + MINUTO
        self.assertEqual(arca.emitir(f.pk, ahora=proximo - timedelta(seconds=1)), "omitido")
        self.assertEqual(arca.emitir(f.pk, ahora=proximo), "emitido")

    def test_ok_en_el_intento_3_emite(self):
        self.arca.responder(RED, RED)
        f = self.factura()
        arca.emitir(f.pk)
        arca.emitir(f.pk, ahora=AHORA + MINUTO)
        self.assertEqual(arca.emitir(f.pk, ahora=AHORA + 3 * MINUTO), "emitido")
        f = self.recargar(f)
        self.assertEqual((f.intentos, f.numero), (3, 42))

    def test_rechazo_es_error_en_el_intento_1_sin_reintento(self):
        rechazo = Resultado("rechazado", error="10015: Doc. Nro. invalido")
        self.arca.responder(rechazo)
        f = self.factura()
        self.assertEqual(arca.emitir(f.pk), "error")
        f = self.recargar(f)
        self.assertEqual((f.intentos, f.motivo, f.proximo_intento), (1, rechazo.error, None))
        self.assertIsNone(f.numero_intentado)
        self.assertEqual(arca.emitir(f.pk, ahora=AHORA + 60 * MINUTO), "omitido")

    def test_cada_intento_deja_un_evento_con_exito(self):
        self.arca.responder(RED)
        f = self.factura()
        arca.emitir(f.pk)
        arca.emitir(f.pk, ahora=AHORA + MINUTO)
        exitos = list(self.eventos(f).order_by("pk").values_list("exito", flat=True))
        self.assertEqual(exitos, [False, True])
        self.assertIn("Intento 1: reintentable", self.eventos(f).order_by("pk").first().detalle)

    def test_falla_del_ticket_cuenta_como_intento(self):
        self.arca.login.return_value = RED
        f = self.factura()
        self.assertEqual(arca.emitir(f.pk), "pendiente")
        self.assertEqual(self.recargar(f).intentos, 1)
        self.arca.solicitar_cae.assert_not_called()

    def test_token_invalido_borra_el_ticket_guardado(self):
        self.arca.responder(Resultado("reintentable", {"token_invalido": True}, "600: token"))
        f = self.factura()
        arca.emitir(f.pk)
        self.emisor.refresh_from_db()
        self.assertEqual((self.emisor.ta_token, self.emisor.ta_vence), ("", None))

    # --- dudosos (FR-026) ---

    def dudoso(self, **campos):
        """Un intento cortó después de enviar: queda con el número 42 y sin CAE."""
        self.arca.responder(CORTE)
        f = self.factura(**campos)
        self.assertEqual(arca.emitir(f.pk), "pendiente")
        f = self.recargar(f)
        self.assertEqual(f.numero_intentado, 42)
        return f

    def test_dudoso_encontrado_en_arca_se_emite_sin_pedir_otro_numero(self):
        f = self.dudoso()
        self.arca.consultar.return_value = datos_de_arca()
        self.assertEqual(arca.emitir(f.pk, ahora=AHORA + MINUTO), "emitido")
        self.arca.consultar.assert_called_once()
        self.assertEqual(self.arca.consultar.call_args.args[2:], (6, 42))
        self.assertEqual(self.arca.solicitar_cae.call_count, 1)
        self.arca.ultimo_autorizado.assert_called_once()  # solo el del primer intento
        f = self.recargar(f)
        self.assertEqual((f.numero, f.estado, f.numero_intentado), (42, "emitido", None))

    def test_dudoso_no_encontrado_emite_con_el_ultimo_autorizado_mas_uno(self):
        f = self.dudoso()
        self.assertEqual(arca.emitir(f.pk, ahora=AHORA + MINUTO), "emitido")
        self.assertEqual(self.arca.solicitar_cae.call_count, 2)
        self.assertEqual(self.recargar(f).numero, 42)

    def test_dudoso_con_otro_total_es_error_y_no_pide_otro_numero(self):
        f = self.dudoso()
        total = f.total
        self.arca.consultar.return_value = datos_de_arca(total=total + Decimal("0.01"))
        self.assertEqual(arca.emitir(f.pk, ahora=AHORA + MINUTO), "error")
        self.assertEqual(self.arca.solicitar_cae.call_count, 1)
        f = self.recargar(f)
        self.assertEqual(
            f.motivo, "ARCA tiene el comprobante 42 con otros datos. Revisalo en ARCA."
        )
        self.assertIsNone(f.proximo_intento)

    def test_dudoso_con_otro_documento_es_error(self):
        f = self.dudoso()
        self.arca.consultar.return_value = datos_de_arca(doc_numero="30111223")
        self.assertEqual(arca.emitir(f.pk, ahora=AHORA + MINUTO), "error")
        self.assertEqual(self.arca.solicitar_cae.call_count, 1)

    def test_dudoso_con_el_total_exacto_coincide_aunque_arca_lo_escriba_distinto(self):
        f = self.dudoso()
        self.arca.consultar.return_value = datos_de_arca(
            doc_numero="030111222", total=Decimal("65000")
        )
        self.assertEqual(arca.emitir(f.pk, ahora=AHORA + MINUTO), "emitido")

    def test_consulta_sin_red_corta_el_intento_y_conserva_el_numero(self):
        f = self.dudoso()
        self.arca.consultar.return_value = RED
        self.assertEqual(arca.emitir(f.pk, ahora=AHORA + MINUTO), "pendiente")
        self.assertEqual(self.arca.solicitar_cae.call_count, 1)
        f = self.recargar(f)
        self.assertEqual((f.numero_intentado, f.intentos), (42, 2))

    def test_sexto_intento_dudoso_es_error_y_conserva_el_numero_intentado(self):
        self.arca.responder(CORTE)
        f = self.factura(intentos=5, numero_intentado=None)
        self.assertEqual(arca.emitir(f.pk), "error")
        f = self.recargar(f)
        self.assertEqual(f.numero_intentado, 42)
        self.assertIn("ARCA no respondió después de 6 intentos", f.motivo)

    def test_un_dudoso_de_otro_comprobante_se_consulta_antes_de_emitir_este(self):
        a = self.dudoso()
        self.arca.ultimo = 42  # ARCA sí autorizó el 42
        self.arca.consultar.return_value = datos_de_arca()
        b = self.factura()
        self.assertEqual(arca.emitir(b.pk, ahora=AHORA + 2 * MINUTO), "emitido")
        a, b = self.recargar(a), self.recargar(b)
        self.assertEqual((a.estado, a.numero), ("emitido", 42))
        self.assertEqual(b.numero, 43)  # ARCA ya tenía el 42
        self.assertEqual(self.arca.consultar.call_args.args[2:], (6, 42))

    def test_un_dudoso_de_otro_comprobante_con_otros_datos_queda_en_error(self):
        a = self.dudoso()
        self.arca.ultimo = 42  # el 42 existe en ARCA, de otro comprobante
        self.arca.consultar.return_value = datos_de_arca(total=Decimal("1.00"))
        b = self.factura()
        self.assertEqual(arca.emitir(b.pk, ahora=AHORA + 2 * MINUTO), "emitido")
        a = self.recargar(a)
        self.assertEqual(a.estado, "error")
        self.assertIn("con otros datos", a.motivo)

    def test_dudoso_reciente_de_otro_comprobante_deja_omitido_hasta_los_2_minutos(self):
        self.dudoso()
        b = self.factura()
        self.assertEqual(arca.emitir(b.pk, ahora=AHORA + timedelta(seconds=119)), "omitido")
        self.arca.consultar.assert_not_called()
        self.assertEqual(arca.emitir(b.pk, ahora=AHORA + timedelta(minutes=2)), "emitido")
        self.arca.consultar.assert_called_once()

    def test_dudoso_en_curso_de_otro_comprobante_deja_omitido_aunque_sea_viejo(self):
        self.factura(
            en_curso_desde=AHORA - timedelta(minutes=9),
            numero_intentado=42,
            cbte_tipo=6,
            fecha_cambio=AHORA,
        )
        b = self.factura()
        self.assertEqual(arca.emitir(b.pk), "omitido")
        self.arca.login.assert_not_called()

    def test_colgado_de_mas_de_10_minutos_vuelve_a_la_cola_con_su_numero(self):
        f = self.factura(
            en_curso_desde=AHORA - timedelta(minutes=10, seconds=1), numero_intentado=42
        )
        self.assertEqual(arca.liberar_colgados(), 1)
        f = self.recargar(f)
        self.assertIsNone(f.en_curso_desde)
        self.assertEqual((f.estado, f.numero_intentado), ("pendiente", 42))

    def test_colgado_de_justo_10_minutos_todavia_no_se_libera(self):
        f = self.factura(en_curso_desde=AHORA - timedelta(minutes=10))
        self.assertEqual(arca.liberar_colgados(), 0)
        self.assertIsNotNone(self.recargar(f).en_curso_desde)

    def test_colgado_en_el_sexto_intento_es_error(self):
        f = self.factura(en_curso_desde=AHORA - timedelta(minutes=11), intentos=6)
        arca.liberar_colgados()
        f = self.recargar(f)
        self.assertEqual(f.estado, "error")
        self.assertIn("6 intentos", f.motivo)

    # --- reintentar (FR-027) ---

    def admin(self, rol="administrator"):
        return get_user_model().objects.create_user(rol, email=f"{rol}@example.com", role=rol)

    def con_error(self, **campos):
        self.arca.responder(Resultado("rechazado", error="10016: dato inválido"))
        f = self.factura(**campos)
        self.assertEqual(arca.emitir(f.pk), "error")
        return self.recargar(f)

    def test_reintentar_un_error_lo_deja_pendiente_con_cero_intentos_y_el_evento_del_usuario(self):
        f = self.con_error()
        usuario = self.admin()
        with self.captureOnCommitCallbacks(execute=False) as encolados:
            arca.reintentar(f, usuario=usuario)
        self.assertEqual(len(encolados), 1)  # se encola la emisión
        f = self.recargar(f)
        self.assertEqual((f.estado, f.intentos, f.motivo), ("pendiente", 0, ""))
        self.assertIsNone(f.proximo_intento)
        evento = historial_de(f).filter(tipo="cambio").latest("pk")
        self.assertEqual(evento.usuario, usuario)
        self.assertEqual(evento.detalle, "Reintento pedido")

    def test_reintentar_emite_y_el_cobro_sale_del_filtro_con_error(self):
        f = self.con_error()
        arca.reintentar(f, usuario=self.admin())
        self.assertEqual(arca.emitir(f.pk), "emitido")
        self.assertEqual(arca.marcas(self.recargar(f).cobro), [])

    def test_reintentar_un_emitido_un_pendiente_o_un_descartado_es_error(self):
        usuario = self.admin()
        for estado in ("pendiente", "descartado"):
            f = self.factura(estado=estado, vigente=estado == "pendiente")
            with self.assertRaisesMessage(
                ValidationError, "Solo se reintenta un comprobante fiscal con error."
            ):
                arca.reintentar(f, usuario=usuario)
        emitida = self.factura()
        self.assertEqual(arca.emitir(emitida.pk), "emitido")
        with self.assertRaisesMessage(ValidationError, "Solo se reintenta"):
            arca.reintentar(self.recargar(emitida), usuario=usuario)

    def test_reintentar_sin_ser_administrador_no_cambia_nada(self):
        f = self.con_error()
        with self.assertRaises(PermissionDenied):
            arca.reintentar(f, usuario=self.admin("operator"))
        self.assertEqual(self.recargar(f).estado, "error")

    def test_reintentar_un_dudoso_consulta_antes_y_no_emite_otro_numero(self):
        self.arca.responder(CORTE)
        f = self.factura(intentos=5, numero_intentado=None)
        self.assertEqual(arca.emitir(f.pk), "error")  # sexto intento dudoso
        arca.reintentar(self.recargar(f), usuario=self.admin())
        self.assertEqual(self.recargar(f).numero_intentado, 42)  # no se libera
        self.arca.consultar.return_value = datos_de_arca()
        self.assertEqual(arca.emitir(f.pk, ahora=AHORA + 2 * MINUTO), "emitido")
        self.assertEqual(self.arca.solicitar_cae.call_count, 1)  # ARCA ya lo tenía: no pide otro
        f = self.recargar(f)
        self.assertEqual((f.numero, f.numero_intentado), (42, None))

    def test_reintentar_dos_veces_seguidas_la_segunda_es_error(self):
        f = self.con_error()
        usuario = self.admin()
        arca.reintentar(f, usuario=usuario)
        with self.assertRaisesMessage(ValidationError, "Solo se reintenta"):
            arca.reintentar(f, usuario=usuario)

    def test_vista_reintentar_es_post_solo_del_administrador_y_vuelve_al_cobro(self):
        f = self.con_error()
        url = reverse("arca:reintentar", args=[f.pk])
        self.client.force_login(self.admin("operator"))
        self.assertEqual(self.client.post(url).status_code, 403)
        self.assertEqual(self.recargar(f).estado, "error")
        self.client.force_login(self.admin())
        self.assertEqual(self.client.get(url).status_code, 405)
        r = self.client.post(url)
        self.assertRedirects(r, reverse("cobros:detalle", args=[f.cobro_id]))
        self.assertEqual(self.recargar(f).estado, "pendiente")
        self.client.post(url)  # ya no está en error: mensaje, sin cambios
        self.assertEqual(self.recargar(f).estado, "pendiente")

    def test_sc001_cinco_cobros_quedan_con_factura_emitida_o_comprobante_en_error(self):
        rechazo = Resultado("rechazado", error="10016: dato inválido")
        respuestas = iter([None, None, rechazo, rechazo] + [RED] * 6)  # None: ARCA autoriza

        def responder(emisor, ta, pedido):
            return next(respuestas) or self.arca._autorizar(emisor, ta, pedido)

        self.arca.solicitar_cae.side_effect = responder
        facturas = [self.factura(dni=str(30111220 + n)) for n in range(5)]
        for f in facturas[:4]:
            arca.emitir(f.pk)
        momento = AHORA
        for _ in range(6):
            arca.emitir(facturas[4].pk, ahora=momento)
            momento += timedelta(minutes=16)
        estados = sorted(self.recargar(f).estado for f in facturas)
        self.assertEqual(estados, ["emitido", "emitido", "error", "error", "error"])
        self.assertFalse(ComprobanteFiscal.objects.filter(estado="pendiente").exists())

    # --- chequeos locales (R7) ---

    def assert_error_local(self, factura, motivo):
        self.assertEqual(arca.emitir(factura.pk), "error")
        f = self.recargar(factura)
        self.assertEqual((f.motivo, f.proximo_intento), (motivo, None))
        self.assertTrue(self.arca.no_llamo_a_arca())
        self.assertFalse(self.eventos(f).get().exito)

    def test_sin_certificado_o_sin_clave_es_error(self):
        for campo in ("certificado", "clave"):
            with self.subTest(campo=campo):
                emisor = emisor_prueba(cuit="2000000011" + str(len(campo) % 10), **{campo: ""})
                self.assert_error_local(
                    self.factura(emisor=emisor), "el emisor no tiene certificado o clave"
                )

    def test_certificado_vencido_justo_en_la_hora_de_vencimiento_es_error(self):
        self.emisor.certificado_vence = AHORA
        self.emisor.save()
        self.assert_error_local(self.factura(), "certificado vencido")

    def test_certificado_que_vence_un_segundo_despues_si_emite(self):
        self.emisor.certificado_vence = AHORA + timedelta(seconds=1)
        self.emisor.save()
        self.assertEqual(arca.emitir(self.factura().pk), "emitido")

    def test_emisor_inactivo_en_una_factura_es_error(self):
        self.emisor.estado = Emisor.Estado.INACTIVO
        self.emisor.save()
        self.assert_error_local(self.factura(), "emisor inactivo")

    def test_nota_de_credito_de_un_emisor_inactivo_si_se_emite(self):
        factura = self.factura()
        arca.emitir(factura.pk)
        self.emisor.estado = Emisor.Estado.INACTIVO
        self.emisor.save()
        nota = ComprobanteFiscal.objects.create(
            cobro=factura.cobro,
            emisor=self.emisor,
            tipo="nota_credito",
            asociado=self.recargar(factura),
            vigente=False,
            concepto="servicios",
            descripcion="Servicios odontológicos",
        )
        self.assertEqual(arca.emitir(nota.pk), "emitido")

    def test_el_orden_de_los_chequeos_locales_empieza_por_el_certificado(self):
        self.emisor.estado = Emisor.Estado.INACTIVO
        self.emisor.clave = ""
        self.emisor.save()
        self.assert_error_local(self.factura(), "el emisor no tiene certificado o clave")

    def test_produccion_sin_permiso_del_servidor_es_error(self):
        self.emisor.ambiente = Emisor.Ambiente.PRODUCCION
        self.emisor.save()
        self.assert_error_local(self.factura(), SIN_PRODUCCION)

    @override_settings(ARCA_PRODUCCION=True)
    def test_produccion_con_permiso_del_servidor_llama_a_arca(self):
        self.emisor.ambiente = Emisor.Ambiente.PRODUCCION
        self.emisor.save()
        self.assertEqual(arca.emitir(self.factura().pk), "emitido")

    # --- ticket (R4) ---

    def con_ticket(self, vence):
        Emisor.objects.filter(pk=self.emisor.pk).update(
            ta_token="TOKEN-GUARDADO", ta_sign="SIGN-GUARDADO", ta_vence=vence
        )

    def test_ticket_guardado_con_mas_de_10_minutos_no_pide_login(self):
        self.con_ticket(AHORA + timedelta(minutes=10, seconds=1))
        arca.emitir(self.factura().pk)
        self.arca.login.assert_not_called()
        ticket = self.arca.solicitar_cae.call_args.args[1]
        self.assertEqual(ticket["token"], "TOKEN-GUARDADO")

    def test_ticket_con_justo_10_minutos_pide_uno_nuevo(self):
        self.con_ticket(AHORA + timedelta(minutes=10))
        arca.emitir(self.factura().pk)
        self.arca.login.assert_called_once()

    def test_ticket_nuevo_queda_guardado_en_el_emisor(self):
        arca.emitir(self.factura().pk)
        self.emisor.refresh_from_db()
        self.assertEqual(self.emisor.ta_token, "TOKEN-DE-PRUEBA")
        self.assertEqual(self.emisor.ta_vence, AHORA + timedelta(hours=12))

    # --- secretos (FR-004) ---

    def test_ningun_evento_ni_log_lleva_el_ticket_ni_la_clave(self):
        self.arca.responder(RED)
        f = self.factura()
        with self.assertNoLogs("sistema.arca", level="DEBUG"):
            arca.emitir(f.pk)
        texto = " ".join(f"{e.cambios} {e.detalle}" for e in Evento.objects.all())
        for secreto in ("TOKEN-DE-PRUEBA", "SIGN-DE-PRUEBA", "BEGIN", "PRIVATE KEY"):
            self.assertNotIn(secreto, texto)

    def test_un_error_inesperado_no_deja_la_clave_en_el_traceback(self):
        self.arca.solicitar_cae.side_effect = RuntimeError("falla inesperada")
        f = self.factura()
        error = atrapar(RuntimeError, arca.emitir, f.pk)
        self.assertEqual(fugas_en_traceback(error), [])
