from datetime import date, datetime
from decimal import Decimal as D
from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.core import mail
from django.urls import reverse

from payment_instructions.models import User
from sistema.auditoria.models import Evento
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.migracion import servicios
from sistema.migracion.tests import fabricas as f
from sistema.reportes.servicios import items_del_mes
from sistema.usuarios.models import Perfil

CLAVE = "clave-prueba-1"
VINCULO = {"drperez": {"id": 12, "nombre": "Dr. Pérez"}}


class UsuariosBase(SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.comp = f.usuario("completar", role="professional")
        self.fila = f.fila("Jimena", alias="jimena.roble.rana")

    def migrar(self, vinculos=None):
        datos = {"para_completar_pagos": "completar", "tipos": {}, "vinculos": vinculos or {}}
        with self.captureOnCommitCallbacks(execute=True):
            return servicios.migrar(datos, definitiva=True)


class UsuariosTest(UsuariosBase):
    def test_operador_entra_con_la_misma_clave_y_llega_a_su_pantalla(self):
        f.usuario("ana")
        self.migrar()
        self.assertTrue(self.client.login(username="ana", password=CLAVE))
        self.assertEqual(User.objects.get(username="ana").role, "operator")
        self.assertEqual(self.client.get(reverse("usuarios:operador")).status_code, 200)

    def test_usuario_desactivado_sigue_desactivado(self):
        f.usuario("baja", is_active=False)
        self.migrar()
        self.assertFalse(User.objects.get(username="baja").is_active)
        self.assertFalse(self.client.login(username="baja", password=CLAVE))

    def test_superusuario_con_rol_operador_pasa_a_administrador(self):
        f.usuario("root", role="operator", is_superuser=True)
        self.migrar()
        self.assertEqual(User.objects.get(username="root").role, "administrator")

    def test_permiso_extra_no_da_acceso_de_administrador(self):
        luis = f.usuario("luis")
        luis.user_permissions.add(
            Permission.objects.get(codename="view_all_deposit_payments_in_admin")
        )
        self.migrar()
        self.assertEqual(User.objects.get(username="luis").role, "operator")
        self.client.login(username="luis", password=CLAVE)
        self.assertEqual(self.client.get(reverse("usuarios:panel")).status_code, 403)

    def test_profesional_con_vinculo_queda_vinculado_y_entra(self):
        f.usuario("drperez", role="professional")
        self.migrar(VINCULO)
        perfil = Perfil.objects.get(usuario__username="drperez")
        self.assertEqual(
            (perfil.id_profesional_dentalink, perfil.nombre_profesional_dentalink),
            (12, "Dr. Pérez"),
        )
        self.assertTrue(self.client.login(username="drperez", password=CLAVE))

    def test_perfil_previo_sin_vinculo_no_frena_la_definitiva(self):
        ana = f.usuario("ana")
        f.usuario("drperez", role="professional")
        Perfil.objects.create(usuario=ana)
        Perfil.objects.create(usuario=User.objects.get(username="drperez"))
        informe = self.migrar(VINCULO)
        self.assertEqual(Perfil.objects.filter(usuario__username="ana").count(), 1)
        perfil = Perfil.objects.get(usuario__username="drperez")
        self.assertEqual(
            (perfil.id_profesional_dentalink, perfil.nombre_profesional_dentalink),
            (12, "Dr. Pérez"),
        )
        self.assertEqual(Perfil.objects.count(), 2)
        self.assertEqual(informe.diferencias, [])

    def test_perfil_previo_con_vinculo_se_conserva_si_el_json_no_trae_otro(self):
        perez = f.usuario("drperez", role="professional")
        Perfil.objects.create(
            usuario=perez, id_profesional_dentalink=7, nombre_profesional_dentalink="Dr. Previo"
        )
        self.migrar()
        perfil = Perfil.objects.get(usuario=perez)
        self.assertEqual(
            (perfil.id_profesional_dentalink, perfil.nombre_profesional_dentalink),
            (7, "Dr. Previo"),
        )
        self.assertEqual(Perfil.objects.filter(usuario=perez).count(), 1)

    def test_perfil_previo_con_vinculo_toma_el_del_json(self):
        perez = f.usuario("drperez", role="professional")
        Perfil.objects.create(
            usuario=perez, id_profesional_dentalink=7, nombre_profesional_dentalink="Dr. Previo"
        )
        self.migrar(VINCULO)
        perfil = Perfil.objects.get(usuario=perez)
        self.assertEqual(perfil.id_profesional_dentalink, 12)

    def test_profesional_sin_vinculo_queda_sin_id(self):
        f.usuario("otro", role="professional")
        self.migrar()
        perfil = Perfil.objects.get(usuario__username="otro")
        self.assertEqual(
            (perfil.id_profesional_dentalink, perfil.nombre_profesional_dentalink), (None, "")
        )

    def test_especialista_desactivado_pasa_profesional_desactivado(self):
        f.usuario("viejo", role="professional", is_active=False)
        self.migrar()
        u = User.objects.get(username="viejo")
        self.assertEqual((u.role, u.is_active), ("professional", False))

    def test_para_completar_pagos_queda_desactivado_sin_perfil_ni_evento(self):
        self.migrar()
        self.assertFalse(User.objects.get(username="completar").is_active)
        self.assertFalse(Perfil.objects.filter(usuario=self.comp).exists())
        self.assertFalse(
            Evento.objects.filter(entidad="payment_instructions.user", entidad_id=self.comp.pk)
        )

    def test_totales_no_cuentan_a_para_completar_pagos(self):
        f.usuario("ana")
        datos = {"para_completar_pagos": "completar", "tipos": {}, "vinculos": {}}
        self.assertEqual(servicios.revisar(datos).totales["usuarios"], 1)

    def test_no_dispara_avisos(self):
        from sistema.avisos.models import DireccionAviso

        DireccionAviso.objects.create(email="aviso@ejemplo.test")
        f.usuario("ana")
        f.usuario("root", is_superuser=True)
        with (
            patch("sistema.avisos.tasks.enviar_aviso.delay") as aviso,
            self.captureOnCommitCallbacks(execute=True) as callbacks,
        ):
            servicios.migrar(
                {"para_completar_pagos": "completar", "tipos": {}, "vinculos": {}},
                definitiva=True,
            )
        self.assertEqual(callbacks, [])
        self.assertEqual(aviso.call_count, 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_control_un_aviso_llega_con_la_direccion_cargada(self):
        from sistema.avisos import servicios as avisos
        from sistema.avisos.models import DireccionAviso

        DireccionAviso.objects.create(email="aviso@ejemplo.test")
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            avisos._enviar("Asunto", ["Cuerpo"])
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_item_migrado_aparece_en_la_pantalla_del_profesional(self):
        ana = f.usuario("ana")
        perez = f.usuario("drperez", role="professional")
        f.pago(self.fila, 15000, ana, perez, created_at=datetime(2026, 10, 5, 10, 0, tzinfo=ARG))
        self.migrar(VINCULO)
        items, total = items_del_mes(12, date(2026, 10, 1))
        self.assertEqual((items.count(), total), (1, D("15000.00")))
        self.client.login(username="drperez", password=CLAVE)
        r = self.client.get(reverse("usuarios:profesional"), {"mes": "2026-10"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "15.000")
