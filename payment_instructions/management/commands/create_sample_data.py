from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from payment_instructions.models import PaymentRecipient
from decimal import Decimal

User = get_user_model()


class Command(BaseCommand):
    help = "Create sample data for testing the payment system"

    def handle(self, *args, **options):
        self.stdout.write("Creating sample data...")

        # Create an operator user
        operator, created = User.objects.get_or_create(
            username="operator1",
            defaults={
                "email": "operator1@example.com",
                "role": User.OPERATOR,
                "first_name": "John",
                "last_name": "Operator",
                "is_staff": True,  # Required for Django admin access
            },
        )
        if created:
            operator.set_password("operator123")
            operator.save()
            self.stdout.write(f"Created operator user: {operator.username}")

        # Create an administrator user
        administrator, created = User.objects.get_or_create(
            username="administrator1",
            defaults={
                "email": "admin1@example.com",
                "role": User.ADMINISTRATOR,
                "first_name": "Jane",
                "last_name": "Admin",
                "is_staff": True,  # Required for Django admin access
            },
        )
        if created:
            administrator.set_password("admin123")
            administrator.save()
            self.stdout.write(f"Created administrator user: {administrator.username}")

        # Create sample payment recipients
        recipients_data = [
            {
                "name": "ABC Suppliers Ltd",
                "alias": "ABC_SUPPLIER",
                "cbu": "1234567890123456789012",
                "max_amount": Decimal("50000.00"),
                "is_recurring": True,
                "priority_order": 1,
            },
            {
                "name": "XYZ Services Inc",
                "alias": "XYZ_SERVICES",
                "cbu": "2345678901234567890123",
                "max_amount": Decimal("30000.00"),
                "is_recurring": True,
                "priority_order": 2,
            },
            {
                "name": "Emergency Contractor",
                "alias": "EMERGENCY_CONTRACTOR",
                "cbu": "3456789012345678901234",
                "max_amount": Decimal("15000.00"),
                "is_recurring": False,
                "priority_order": 3,
            },
            {
                "name": "Monthly Utilities",
                "alias": "UTILITIES",
                "cbu": "4567890123456789012345",
                "max_amount": Decimal("8000.00"),
                "is_recurring": True,
                "priority_order": 4,
            },
            {
                "name": "Office Supplies Co",
                "alias": "OFFICE_SUPPLIES",
                "cbu": "5678901234567890123456",
                "max_amount": Decimal("12000.00"),
                "is_recurring": True,
                "priority_order": 5,
            },
        ]

        for recipient_data in recipients_data:
            recipient, created = PaymentRecipient.objects.get_or_create(
                alias=recipient_data["alias"], defaults=recipient_data
            )
            if created:
                self.stdout.write(
                    f"Created payment recipient: {recipient.alias} - {recipient.name}"
                )

        self.stdout.write(self.style.SUCCESS("Sample data created successfully!"))
        self.stdout.write("\n--- Login Credentials ---")
        self.stdout.write("Administrator: administrator1 / admin123")
        self.stdout.write("Operator: operator1 / operator123")
        self.stdout.write("Superuser: admin / [password you set during createsuperuser]")
