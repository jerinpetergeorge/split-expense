from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from djmoney.money import Money

from accounts.models import User

from .models import Category, Expense, ExpenseSplit, Group, Settlement


class GroupForm(forms.ModelForm):
    """
    Form for creating and editing groups
    """

    # Define currency choices (common currencies)
    CURRENCY_CHOICES = [
        ("INR", "INR - Indian Rupee (₹)"),
        ("USD", "USD - US Dollar ($)"),
        ("EUR", "EUR - Euro (€)"),
        ("GBP", "GBP - British Pound (£)"),
        ("JPY", "JPY - Japanese Yen (¥)"),
        ("AUD", "AUD - Australian Dollar (A$)"),
        ("CAD", "CAD - Canadian Dollar (C$)"),
        ("SGD", "SGD - Singapore Dollar (S$)"),
    ]

    default_currency = forms.ChoiceField(
        choices=CURRENCY_CHOICES,
        initial="INR",
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Default Currency",
    )

    members = forms.ModelMultipleChoiceField(
        queryset=User.objects.all(),
        widget=forms.CheckboxSelectMultiple(attrs={"class": "form-check-input"}),
        required=True,
        help_text="Select members to add to this group",
    )

    class Meta:
        model = Group
        fields = ["name", "description", "members", "default_currency"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g., Roommates, Trip to Goa"}),
            "description": forms.Textarea(
                attrs={"class": "form-control", "rows": 3, "placeholder": "Optional description"}
            ),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        # Exclude current user from members selection (will be added automatically)
        if self.user:
            self.fields["members"].queryset = User.objects.exclude(id=self.user.id)

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.user:
            instance.created_by = self.user

        if commit:
            instance.save()
            # Add the creator as a member
            members = list(self.cleaned_data["members"])
            members.append(self.user)
            instance.members.set(members)

        return instance


class ExpenseForm(forms.ModelForm):
    """
    Form for creating expenses (both personal and group)
    """

    # Override the amount field to handle it as a simple decimal
    amount = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "0.00", "step": "0.01"}),
        label="Amount (₹)",
    )

    class Meta:
        model = Expense
        fields = ["description", "amount", "expense_type", "group", "paid_by", "category", "date", "notes"]
        widgets = {
            "description": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "e.g., Dinner at restaurant"}
            ),
            "expense_type": forms.Select(attrs={"class": "form-control"}),
            "group": forms.Select(attrs={"class": "form-control"}),
            "paid_by": forms.Select(attrs={"class": "form-control"}),
            "category": forms.Select(attrs={"class": "form-control"}),
            "date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "notes": forms.Textarea(
                attrs={"class": "form-control", "rows": 2, "placeholder": "Additional notes (optional)"}
            ),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        # Filter groups to only those the user is a member of
        if self.user:
            self.fields["group"].queryset = Group.objects.filter(members=self.user, is_active=True)

            # Set user as default for paid_by
            self.fields["paid_by"].initial = self.user

        # Make group optional initially (required for group expenses via JS or validation)
        self.fields["group"].required = False

        # If editing an existing expense, set the amount value
        if self.instance.pk and self.instance.amount:
            self.initial["amount"] = self.instance.amount.amount

    def clean(self):
        cleaned_data = super().clean()
        expense_type = cleaned_data.get("expense_type")
        group = cleaned_data.get("group")
        paid_by = cleaned_data.get("paid_by")

        # Validate group expenses must have a group
        if expense_type == Expense.ExpenseType.GROUP and not group:
            raise ValidationError({"group": "Group expenses must belong to a group."})

        # Validate personal expenses should not have a group
        if expense_type == Expense.ExpenseType.PERSONAL and group:
            raise ValidationError({"group": "Personal expenses should not belong to a group."})

        # Validate paid_by is a member of the group for group expenses
        if expense_type == Expense.ExpenseType.GROUP and group and paid_by:
            if not group.members.filter(id=paid_by.id).exists():
                raise ValidationError({"paid_by": "Payer must be a member of the group."})

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)

        # Convert the decimal amount to Money object
        amount_value = self.cleaned_data.get("amount")
        if amount_value:
            # Get currency from group or use default INR
            currency = "INR"
            if instance.group and instance.group.default_currency:
                currency = instance.group.default_currency

            instance.amount = Money(amount_value, currency)

        if self.user:
            instance.user = self.user

        if commit:
            instance.save()

        return instance


class ExpenseSplitFormSet(forms.BaseFormSet):
    """
    Custom formset for handling expense splits with validation
    """

    def __init__(self, *args, **kwargs):
        self.expense = kwargs.pop("expense", None)
        self.split_type = kwargs.pop("split_type", ExpenseSplit.SplitType.EQUAL)
        super().__init__(*args, **kwargs)

    def clean(self):
        if any(self.errors):
            return

        total_amount = Decimal("0.00")
        total_percentage = Decimal("0.00")
        total_shares = 0
        users = []

        for form in self.forms:
            if form.cleaned_data and not form.cleaned_data.get("DELETE", False):
                user = form.cleaned_data.get("user")
                amount = form.cleaned_data.get("amount")
                percentage = form.cleaned_data.get("percentage")
                shares = form.cleaned_data.get("shares")

                # Check for duplicate users
                if user in users:
                    raise ValidationError("Each user can only be added once.")
                users.append(user)

                # Validate based on split type
                if self.split_type == ExpenseSplit.SplitType.EXACT:
                    if amount:
                        total_amount += Decimal(str(amount))

                elif self.split_type == ExpenseSplit.SplitType.PERCENTAGE:
                    if percentage:
                        total_percentage += percentage

                elif self.split_type == ExpenseSplit.SplitType.SHARES:
                    if shares:
                        total_shares += shares

        # Validate totals
        if self.expense:
            expense_amount = Decimal(str(self.expense.amount.amount))

            if self.split_type == ExpenseSplit.SplitType.EXACT:
                if abs(total_amount - expense_amount) > Decimal("0.01"):
                    raise ValidationError(
                        f"Split amounts must equal expense amount. Total: ₹{total_amount}, Expected: ₹{expense_amount}"
                    )

            elif self.split_type == ExpenseSplit.SplitType.PERCENTAGE:
                if abs(total_percentage - Decimal("100.00")) > Decimal("0.01"):
                    raise ValidationError(f"Percentages must add up to 100%. Current total: {total_percentage}%")


class ExpenseSplitForm(forms.ModelForm):
    """
    Form for individual expense splits
    """

    class Meta:
        model = ExpenseSplit
        fields = ["user", "split_type", "amount", "percentage", "shares"]
        widgets = {
            "user": forms.Select(attrs={"class": "form-control"}),
            "split_type": forms.Select(attrs={"class": "form-control"}),
            "amount": forms.NumberInput(attrs={"class": "form-control", "placeholder": "0.00", "step": "0.01"}),
            "percentage": forms.NumberInput(
                attrs={"class": "form-control", "placeholder": "0.00", "step": "0.01", "min": "0", "max": "100"}
            ),
            "shares": forms.NumberInput(attrs={"class": "form-control", "placeholder": "1", "min": "1"}),
        }

    def __init__(self, *args, **kwargs):
        self.group = kwargs.pop("group", None)
        super().__init__(*args, **kwargs)

        # Filter users to only group members
        if self.group:
            self.fields["user"].queryset = self.group.members.all()


# Create the formset
ExpenseSplitFormSetFactory = forms.formset_factory(
    ExpenseSplitForm, formset=ExpenseSplitFormSet, extra=1, can_delete=True
)


class SimpleSplitForm(forms.Form):
    """
    Simplified form for quick equal splits
    """

    split_type = forms.ChoiceField(
        choices=ExpenseSplit.SplitType.choices,
        initial=ExpenseSplit.SplitType.EQUAL,
        widget=forms.RadioSelect(attrs={"class": "split-type-radio"}),
    )

    members = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        help_text="Select who should be included in the split",
    )

    def __init__(self, *args, **kwargs):
        self.group = kwargs.pop("group", None)
        self.expense = kwargs.pop("expense", None)
        super().__init__(*args, **kwargs)

        if self.group:
            self.fields["members"].queryset = self.group.members.all()
            # Pre-select all members by default
            self.fields["members"].initial = self.group.members.all()

    def save(self):
        """
        Create expense splits based on the form data
        """
        if not self.expense:
            return []

        split_type = self.cleaned_data["split_type"]
        members = self.cleaned_data["members"]

        # Delete existing splits
        self.expense.splits.all().delete()

        splits = []
        expense_amount = Decimal(str(self.expense.amount.amount))
        num_members = members.count()

        if split_type == ExpenseSplit.SplitType.EQUAL:
            # Split equally among selected members
            per_person = expense_amount / num_members

            for member in members:
                split = ExpenseSplit.objects.create(
                    expense=self.expense,
                    user=member,
                    split_type=split_type,
                    amount=Money(per_person, self.expense.amount.currency),
                )
                splits.append(split)

        return splits


class SettlementForm(forms.ModelForm):
    """
    Form for recording settlements between users
    """

    class Meta:
        model = Settlement
        fields = ["paid_by", "paid_to", "amount", "settlement_date", "payment_method", "notes"]
        widgets = {
            "paid_by": forms.Select(attrs={"class": "form-control"}),
            "paid_to": forms.Select(attrs={"class": "form-control"}),
            "amount": forms.NumberInput(attrs={"class": "form-control", "placeholder": "0.00", "step": "0.01"}),
            "settlement_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "payment_method": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "e.g., UPI, Cash, Bank Transfer"}
            ),
            "notes": forms.Textarea(
                attrs={"class": "form-control", "rows": 2, "placeholder": "Additional notes (optional)"}
            ),
        }

    def __init__(self, *args, **kwargs):
        self.group = kwargs.pop("group", None)
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        if self.group:
            # Filter to only group members
            members = self.group.members.all()
            self.fields["paid_by"].queryset = members
            self.fields["paid_to"].queryset = members

            # Set default paid_by to current user
            if self.user:
                self.fields["paid_by"].initial = self.user

    def clean(self):
        cleaned_data = super().clean()
        paid_by = cleaned_data.get("paid_by")
        paid_to = cleaned_data.get("paid_to")

        if paid_by and paid_to and paid_by == paid_to:
            raise ValidationError("You cannot settle with yourself.")

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.group:
            instance.group = self.group

        if commit:
            instance.save()

        return instance


class CategoryForm(forms.ModelForm):
    """
    Form for creating custom categories
    """

    class Meta:
        model = Category
        fields = ["name"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g., Groceries, Entertainment"}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.user:
            instance.created_by = self.user

        if commit:
            instance.save()

        return instance
