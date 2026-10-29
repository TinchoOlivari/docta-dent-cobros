from django.test import Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import datetime
from django.db import models

from payment_instructions.models import PaymentRecipient, Payment
from payment_instructions.tests import BaseTestCase
from payment_instructions.utils.utils import format_money_thousand_separator

User = get_user_model()


class PaymentAdminSubtotalTests(BaseTestCase):
    def setUp(self):
        self.client = Client()
        # Users
        self.admin_user = User.objects.create_user(
            username="admin1",
            email="a@example.com",
            password="pass",
            role=User.ADMINISTRATOR,
            is_staff=True,
        )
        self.operator_user = User.objects.create_user(
            username="op1",
            email="o@example.com",
            password="pass",
            role=User.OPERATOR,
            is_staff=True,
        )
        self.operator_user2 = User.objects.create_user(
            username="op2",
            email="o2@example.com",
            password="pass",
            role=User.OPERATOR,
            is_staff=True,
        )
        self.professional_user = User.objects.create_user(
            username="spec1",
            email="s@example.com",
            password="pass",
            role=User.PROFESSIONAL,
            is_staff=True,
        )
        self.professional_user2 = User.objects.create_user(
            username="spec2",
            email="s2@example.com",
            password="pass",
            role=User.PROFESSIONAL,
            is_staff=True,
        )

        # Recipients
        self.rec_a = PaymentRecipient.objects.create(
            name="RA",
            alias="ra",
            cbu="1" * 22,
            max_amount=1_000_000,
            min_threshold=0,
            priority_order=1,
        )
        self.rec_b = PaymentRecipient.objects.create(
            name="RB",
            alias="rb",
            cbu="2" * 22,
            max_amount=1_000_000,
            min_threshold=0,
            priority_order=2,
        )

        # Dates for hierarchy
        now = timezone.now()
        self.y1 = now.year
        self.m1 = now.month
        self.d1 = min(now.day, 28)  # safe
        # Another month/day
        other_month = 1 if self.m1 != 1 else 2
        self.y2 = self.y1
        self.m2 = other_month
        self.d2 = 15

        # Core payments
        tz = timezone.get_current_timezone()
        self.p1 = Payment.objects.create(
            amount=1000,
            payment_recipient=self.rec_a,
            professional=self.professional_user,
            operator_user=self.operator_user,
            patient_name="Juan Perez",
            created_at=timezone.make_aware(datetime(self.y1, self.m1, self.d1), tz),
        )
        self.p2 = Payment.objects.create(
            amount=2000,
            payment_recipient=self.rec_a,
            professional=self.professional_user2,
            operator_user=self.operator_user,
            patient_name="Maria Gomez",
            created_at=timezone.make_aware(datetime(self.y1, self.m1, self.d1), tz),
        )
        self.p3 = Payment.objects.create(
            amount=3000,
            payment_recipient=self.rec_b,
            professional=self.professional_user,
            operator_user=self.operator_user2,
            patient_name="Pedro Alvarez",
            created_at=timezone.make_aware(datetime(self.y2, self.m2, self.d2), tz),
        )
        # Unique patient for search
        self.search_payment = Payment.objects.create(
            amount=7777,
            payment_recipient=self.rec_b,
            professional=self.professional_user,
            operator_user=self.operator_user,
            patient_name="XBusca Unica",
            created_at=timezone.make_aware(datetime(self.y1, self.m1, self.d1), tz),
        )

    def url(self):
        return reverse("admin:payment_instructions_payment_changelist")

    def assert_subtotal(self, user, expected_amount, params=None):
        self.client.logout()
        self.client.force_login(user)
        params = params or {}
        resp = self.client.get(self.url(), params)
        assert resp.status_code == 200
        # context keys
        assert "subtotal_amount" in resp.context
        assert "subtotal_display" in resp.context
        assert resp.context["subtotal_amount"] == expected_amount
        expected_display = f"${format_money_thousand_separator(expected_amount)}"
        assert resp.context["subtotal_display"] == expected_display
        html = resp.content.decode("utf-8")
        assert 'id="payments-subtotal-footer"' in html
        assert f"Subtotal: {expected_display}" in html

    def assert_subtotal_hidden(self, user, params=None):
        self.client.logout()
        self.client.force_login(user)
        params = params or {}
        resp = self.client.get(self.url(), params)
        assert resp.status_code == 200
        assert "subtotal_amount" not in resp.context
        assert "subtotal_display" not in resp.context
        html = resp.content.decode("utf-8")
        assert 'id="payments-subtotal-footer"' not in html
        assert "Subtotal:" not in html

    def filtered_sum(self, base_qs):
        agg = base_qs.aggregate(total_amount=models.Sum("amount"))  # type: ignore
        return agg.get("total_amount") or 0

    def compute_expected(self, viewer, extra_filters=None):
        qs = Payment.objects.all()
        # role restriction
        if viewer.role == User.OPERATOR:
            qs = qs.filter(operator_user=viewer)
        elif viewer.role == User.PROFESSIONAL:
            qs = qs.filter(professional=viewer)
        # apply extra filters
        extra_filters = extra_filters or {}
        if "payment_recipient" in extra_filters:
            qs = qs.filter(payment_recipient_id=extra_filters["payment_recipient"])
        if "operator_user" in extra_filters:
            qs = qs.filter(operator_user_id=extra_filters["operator_user"])
        if "professional" in extra_filters:
            qs = qs.filter(professional_id=extra_filters["professional"])
        if "created_at__year" in extra_filters:
            qs = qs.filter(created_at__year=int(extra_filters["created_at__year"]))
        if "created_at__month" in extra_filters:
            qs = qs.filter(created_at__month=int(extra_filters["created_at__month"]))
        if "created_at__day" in extra_filters:
            qs = qs.filter(created_at__day=int(extra_filters["created_at__day"]))
        if "q" in extra_filters and extra_filters["q"]:
            # minimal search emulation: patient_name icontains
            qs = qs.filter(patient_name__icontains=extra_filters["q"])
        return qs.aggregate(total=models.Sum("amount"))["total"] or 0  # type: ignore

    def test_subtotal_no_filters_admin(self):
        expected = self.compute_expected(self.admin_user)
        self.assert_subtotal(self.admin_user, expected)

    def test_subtotal_no_filters_operator(self):
        self.assert_subtotal_hidden(self.operator_user)

    def test_subtotal_no_filters_professional(self):
        self.assert_subtotal_hidden(self.professional_user)

    def test_filter_by_payment_recipient(self):
        params = {"payment_recipient": self.rec_a.id}
        expected = self.compute_expected(self.admin_user, params)
        self.assert_subtotal(self.admin_user, expected, params)

    def test_filter_by_operator(self):
        params = {"operator_user": self.operator_user.id}
        expected = self.compute_expected(self.admin_user, params)
        self.assert_subtotal(self.admin_user, expected, params)

    def test_filter_by_professional(self):
        params = {"professional": self.professional_user.id}
        expected = self.compute_expected(self.admin_user, params)
        self.assert_subtotal(self.admin_user, expected, params)

    def test_search_by_patient_name(self):
        params = {"q": "Busca Unica"}
        expected = self.compute_expected(self.admin_user, params)
        self.assert_subtotal(self.admin_user, expected, params)

    def test_date_hierarchy_year(self):
        params = {"created_at__year": str(self.y1)}
        expected = self.compute_expected(self.admin_user, params)
        self.assert_subtotal(self.admin_user, expected, params)

    def test_date_hierarchy_year_month(self):
        params = {"created_at__year": str(self.y1), "created_at__month": str(self.m1)}
        expected = self.compute_expected(self.admin_user, params)
        self.assert_subtotal(self.admin_user, expected, params)

    def test_date_hierarchy_year_month_day(self):
        params = {
            "created_at__year": str(self.y1),
            "created_at__month": str(self.m1),
            "created_at__day": str(self.d1),
        }
        expected = self.compute_expected(self.admin_user, params)
        self.assert_subtotal(self.admin_user, expected, params)

    def test_pagination_total_not_page_limited(self):
        tz = timezone.get_current_timezone()
        base_day = timezone.make_aware(datetime(self.y1, self.m1, self.d1), tz)
        # Create many payments for rec_a by operator_user to exceed one page
        bulk = []
        for i in range(120):
            bulk.append(
                Payment(
                    amount=10,
                    payment_recipient=self.rec_a,
                    professional=self.professional_user,
                    operator_user=self.operator_user,
                    patient_name=f"Pag {i}",
                    created_at=base_day,
                )
            )
        Payment.objects.bulk_create(bulk)
        params = {"payment_recipient": self.rec_a.id}
        expected = self.compute_expected(self.admin_user, params)
        # Request page 2 explicitly
        params_with_page = dict(params)
        params_with_page["p"] = "1"
        self.assert_subtotal(self.admin_user, expected, params_with_page)

    def test_permissions_operator_and_professional_scoping(self):
        self.assert_subtotal_hidden(self.operator_user)
        self.assert_subtotal_hidden(self.professional_user)
