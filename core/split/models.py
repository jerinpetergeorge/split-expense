from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django_extensions.db.models import TimeStampedModel
from djmoney.models.fields import MoneyField

from accounts.models import User


class Category(models.Model):
    """
    Categories for expenses (Food, Travel, Entertainment, etc.)
    """

    name = models.CharField(max_length=100)
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="categories",
    )

    class Meta:
        verbose_name_plural = "Categories"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Group(TimeStampedModel):
    """
    Groups for splitting expenses (like Splitwise)
    """

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="created_groups")
    members = models.ManyToManyField(User, related_name="expense_groups")
    default_currency = models.CharField(max_length=3, default="INR")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return self.name


class Expense(TimeStampedModel):
    """
    Main expense model - handles both personal and group expenses
    """

    class ExpenseType(models.TextChoices):
        PERSONAL = "personal", "Personal Expense"
        GROUP = "group", "Group Expense"

    description = models.CharField(max_length=150)
    amount = MoneyField(
        max_digits=14,
        decimal_places=2,
        default_currency="INR",
        validators=[
            MinValueValidator(Decimal("0.01")),
        ],
    )
    expense_type = models.CharField(max_length=10, choices=ExpenseType.choices)

    # Personal expense fields
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="personal_expenses")

    # Group expense fields (null for personal expenses)
    group = models.ForeignKey(Group, on_delete=models.CASCADE, null=True, blank=True, related_name="expenses")
    paid_by = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="paid_expenses", help_text="User who paid for this expense"
    )

    # Common fields
    category = models.ForeignKey(Category, on_delete=models.CASCADE)
    date = models.DateField()
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-date", "-created"]

    def __str__(self):
        return f"{self.description} - {self.amount}"


class ExpenseSplit(models.Model):
    """
    How a group expense is split among members
    """

    class SplitType(models.TextChoices):
        EQUAL = "equal", "Split Equally"
        EXACT = "exact", "Exact Amounts"
        PERCENTAGE = "percentage", "By Percentage"
        SHARES = "shares", "By Shares"

    expense = models.ForeignKey(Expense, on_delete=models.CASCADE, related_name="splits")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="expense_splits")
    split_type = models.CharField(max_length=15, choices=SplitType.choices, default=SplitType.EQUAL)

    # Using django-money MoneyField
    amount = MoneyField(
        max_digits=14,
        decimal_places=2,
        default_currency="INR",
        validators=[
            MinValueValidator(Decimal("0.00")),
        ],
    )

    # For percentage splits
    percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[
            MinValueValidator(Decimal("0.00")),
        ],
    )

    # For share-based splits
    shares = models.IntegerField(null=True, blank=True, validators=[MinValueValidator(1)])

    is_settled = models.BooleanField(default=False)

    class Meta:
        ordering = ["user"]

    def __str__(self):
        return f"{self.user.username} owes {self.amount} for {self.expense.description}"


class Settlement(TimeStampedModel):
    """
    Records of settlements between users in a group
    """

    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="settlements")
    paid_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="payments_made")
    paid_to = models.ForeignKey(User, on_delete=models.CASCADE, related_name="payments_received")

    # Using django-money MoneyField
    amount = MoneyField(
        max_digits=14,
        decimal_places=2,
        default_currency="INR",
        validators=[
            MinValueValidator(Decimal("0.01")),
        ],
    )

    settlement_date = models.DateField()
    notes = models.TextField(blank=True)
    payment_method = models.CharField(max_length=50, blank=True)  # Cash, UPI, Bank Transfer, etc.

    # Link to specific expense splits being settled (optional)
    expense_splits = models.ManyToManyField(ExpenseSplit, blank=True, related_name="settlements")

    class Meta:
        ordering = ["-settlement_date", "-created"]

    def __str__(self):
        return f"{self.paid_by.username} paid {self.paid_to.username} {self.amount}"
