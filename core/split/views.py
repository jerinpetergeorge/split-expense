# Create your views here.
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, DeleteView, DetailView, ListView, TemplateView, UpdateView

from .forms import CategoryForm, ExpenseForm, GroupForm, SettlementForm, SimpleSplitForm
from .models import Category, Expense, ExpenseSplit, Group, Settlement


class DashboardView(LoginRequiredMixin, TemplateView):
    """
    Main dashboard showing overview of expenses and balances
    """

    template_name = "expenses/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        # Get user's groups
        context["groups"] = Group.objects.filter(members=user, is_active=True).annotate(
            expense_count=Count("expenses")
        )

        # Get recent personal expenses
        context["recent_personal_expenses"] = Expense.objects.filter(
            user=user, expense_type=Expense.ExpenseType.PERSONAL
        )[:5]

        # Get recent group expenses
        context["recent_group_expenses"] = Expense.objects.filter(
            group__members=user, expense_type=Expense.ExpenseType.GROUP
        ).select_related("group", "paid_by", "category")[:10]

        # Calculate balances (simplified - you may want to optimize this)
        balances = self.calculate_user_balances(user)
        context["balances"] = balances

        return context

    def calculate_user_balances(self, user):
        """
        Calculate how much user owes or is owed in each group
        """
        balances = []
        groups = Group.objects.filter(members=user, is_active=True)

        for group in groups:
            # Amount user paid
            paid = Expense.objects.filter(group=group, paid_by=user, expense_type=Expense.ExpenseType.GROUP).aggregate(
                total=Sum("amount")
            )["total"] or Decimal("0.00")

            # Amount user owes
            owed = ExpenseSplit.objects.filter(expense__group=group, user=user, is_settled=False).aggregate(
                total=Sum("amount")
            )["total"] or Decimal("0.00")

            balance = paid - owed

            if balance != 0:
                balances.append({"group": group, "balance": balance, "status": "owed" if balance > 0 else "owes"})

        return balances


# Group Views
class GroupListView(LoginRequiredMixin, ListView):
    """
    List all groups user is a member of
    """

    model = Group
    template_name = "expenses/group_list.html"
    context_object_name = "groups"
    paginate_by = 10

    def get_queryset(self):
        return (
            Group.objects.filter(members=self.request.user, is_active=True)
            .annotate(expense_count=Count("expenses"), member_count=Count("members"))
            .order_by("-created")
        )


class GroupDetailView(LoginRequiredMixin, DetailView):
    """
    Detailed view of a group with expenses and balances
    """

    model = Group
    template_name = "expenses/group_detail.html"
    context_object_name = "group"

    def get_queryset(self):
        return Group.objects.filter(members=self.request.user, is_active=True)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        group = self.object

        # Get group expenses
        context["expenses"] = (
            Expense.objects.filter(group=group, expense_type=Expense.ExpenseType.GROUP)
            .select_related("paid_by", "category")
            .order_by("-date")
        )

        # Calculate balances between members
        context["balances"] = self.calculate_group_balances(group)

        # Get recent settlements
        context["settlements"] = (
            Settlement.objects.filter(group=group)
            .select_related("paid_by", "paid_to")
            .order_by("-settlement_date")[:5]
        )

        return context

    def calculate_group_balances(self, group):
        """
        Calculate balances between all group members
        """
        members = group.members.all()
        balances = {}

        for member in members:
            # Amount member paid
            paid = Expense.objects.filter(
                group=group, paid_by=member, expense_type=Expense.ExpenseType.GROUP
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

            # Amount member owes
            owed = ExpenseSplit.objects.filter(expense__group=group, user=member).aggregate(total=Sum("amount"))[
                "total"
            ] or Decimal("0.00")

            balances[member] = {"paid": paid, "owed": owed, "balance": paid - owed}

        return balances


class GroupCreateView(LoginRequiredMixin, CreateView):
    """
    Create a new group
    """

    model = Group
    form_class = GroupForm
    template_name = "expenses/group_form.html"
    success_url = reverse_lazy("group_list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        messages.success(self.request, f'Group "{form.instance.name}" created successfully!')
        return super().form_valid(form)


class GroupUpdateView(LoginRequiredMixin, UpdateView):
    """
    Update an existing group
    """

    model = Group
    form_class = GroupForm
    template_name = "expenses/group_form.html"

    def get_queryset(self):
        # Only group creator can edit
        return Group.objects.filter(created_by=self.request.user, is_active=True)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_success_url(self):
        return reverse("group_detail", kwargs={"pk": self.object.pk})

    def form_valid(self, form):
        messages.success(self.request, "Group updated successfully!")
        return super().form_valid(form)


class GroupDeleteView(LoginRequiredMixin, DeleteView):
    """
    Soft delete a group (set is_active=False)
    """

    model = Group
    template_name = "expenses/group_confirm_delete.html"
    success_url = reverse_lazy("group_list")

    def get_queryset(self):
        # Only group creator can delete
        return Group.objects.filter(created_by=self.request.user, is_active=True)

    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()
        self.object.is_active = False
        self.object.save()
        messages.success(request, f'Group "{self.object.name}" deleted successfully!')
        return redirect(self.success_url)


# Expense Views
class ExpenseListView(LoginRequiredMixin, ListView):
    """
    List all expenses (personal and group)
    """

    model = Expense
    template_name = "expenses/expense_list.html"
    context_object_name = "expenses"
    paginate_by = 20

    def get_queryset(self):
        user = self.request.user
        expense_type = self.request.GET.get("type", "all")

        queryset = (
            Expense.objects.filter(Q(user=user) | Q(group__members=user))
            .select_related("user", "paid_by", "group", "category")
            .order_by("-date", "-created")
        )

        if expense_type == "personal":
            queryset = queryset.filter(expense_type=Expense.ExpenseType.PERSONAL)
        elif expense_type == "group":
            queryset = queryset.filter(expense_type=Expense.ExpenseType.GROUP)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["expense_type"] = self.request.GET.get("type", "all")
        return context


class ExpenseDetailView(LoginRequiredMixin, DetailView):
    """
    Detailed view of an expense with splits
    """

    model = Expense
    template_name = "expenses/expense_detail.html"
    context_object_name = "expense"

    def get_queryset(self):
        return Expense.objects.filter(Q(user=self.request.user) | Q(group__members=self.request.user)).select_related(
            "user", "paid_by", "group", "category"
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        if self.object.expense_type == Expense.ExpenseType.GROUP:
            context["splits"] = (
                ExpenseSplit.objects.filter(expense=self.object).select_related("user").order_by("user__username")
            )

        return context


class ExpenseCreateView(LoginRequiredMixin, CreateView):
    """
    Create a new expense (personal or group)
    """

    model = Expense
    form_class = ExpenseForm
    template_name = "expenses/expense_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # If creating group expense, show split form
        if self.request.GET.get("group_id"):
            group = get_object_or_404(Group, id=self.request.GET.get("group_id"))
            context["group"] = group
            context["split_form"] = SimpleSplitForm(group=group)

        return context

    def form_valid(self, form):
        response = super().form_valid(form)

        # If group expense, handle splits
        if self.object.expense_type == Expense.ExpenseType.GROUP:
            split_form = SimpleSplitForm(self.request.POST, group=self.object.group, expense=self.object)

            if split_form.is_valid():
                split_form.save()
                messages.success(self.request, f'Expense "{self.object.description}" created and split among members!')
            else:
                messages.error(self.request, "Error creating splits. Please try again.")
        else:
            messages.success(self.request, f'Personal expense "{self.object.description}" added!')

        return response

    def get_success_url(self):
        if self.object.expense_type == Expense.ExpenseType.GROUP:
            return reverse("group_detail", kwargs={"pk": self.object.group.pk})
        return reverse("expense_list")


class ExpenseUpdateView(LoginRequiredMixin, UpdateView):
    """
    Update an existing expense
    """

    model = Expense
    form_class = ExpenseForm
    template_name = "expenses/expense_form.html"

    def get_queryset(self):
        # Only expense creator can edit
        return Expense.objects.filter(user=self.request.user)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_success_url(self):
        return reverse("expense_detail", kwargs={"pk": self.object.pk})

    def form_valid(self, form):
        messages.success(self.request, "Expense updated successfully!")
        return super().form_valid(form)


class ExpenseDeleteView(LoginRequiredMixin, DeleteView):
    """
    Delete an expense
    """

    model = Expense
    template_name = "expenses/expense_confirm_delete.html"
    success_url = reverse_lazy("expense_list")

    def get_queryset(self):
        # Only expense creator can delete
        return Expense.objects.filter(user=self.request.user)

    def delete(self, request, *args, **kwargs):
        expense = self.get_object()
        messages.success(request, f'Expense "{expense.description}" deleted successfully!')
        return super().delete(request, *args, **kwargs)


# Settlement Views
class SettlementCreateView(LoginRequiredMixin, CreateView):
    """
    Record a settlement between users
    """

    model = Settlement
    form_class = SettlementForm
    template_name = "expenses/settlement_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user

        if self.kwargs.get("group_pk"):
            kwargs["group"] = get_object_or_404(Group, pk=self.kwargs["group_pk"], members=self.request.user)

        return kwargs

    def get_success_url(self):
        return reverse("group_detail", kwargs={"pk": self.object.group.pk})

    def form_valid(self, form):
        messages.success(self.request, "Settlement recorded successfully!")
        return super().form_valid(form)


class SettlementListView(LoginRequiredMixin, ListView):
    """
    List all settlements for a group
    """

    model = Settlement
    template_name = "expenses/settlement_list.html"
    context_object_name = "settlements"
    paginate_by = 20

    def get_queryset(self):
        group_pk = self.kwargs.get("group_pk")
        return (
            Settlement.objects.filter(group__pk=group_pk, group__members=self.request.user)
            .select_related("paid_by", "paid_to")
            .order_by("-settlement_date")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["group"] = get_object_or_404(Group, pk=self.kwargs["group_pk"], members=self.request.user)
        return context


# Category Views
class CategoryListView(LoginRequiredMixin, ListView):
    """
    List all categories (system and user-created)
    """

    model = Category
    template_name = "expenses/category_list.html"
    context_object_name = "categories"

    def get_queryset(self):
        return Category.objects.filter(Q(created_by=None) | Q(created_by=self.request.user)).order_by("name")


class CategoryCreateView(LoginRequiredMixin, CreateView):
    """
    Create a custom category
    """

    model = Category
    form_class = CategoryForm
    template_name = "expenses/category_form.html"
    success_url = reverse_lazy("category_list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        messages.success(self.request, f'Category "{form.instance.name}" created!')
        return super().form_valid(form)
