from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Category, Expense
from .serializers import (
    BulkExpenseSyncSerializer,
    CategorySerializer,
    ExpenseSerializer,
)


class CategoryListView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        qs = Category.objects.all()
        serializer = CategorySerializer(qs, many=True)
        return Response(serializer.data)


class ExpenseListView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        qs = Expense.objects.all().order_by('-date')
        serializer = ExpenseSerializer(qs, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = ExpenseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class BulkExpenseSyncView(APIView):
    """Bulk sync endpoint for mobile devices.

    Accepts a list of expenses and uses get_or_create with the offline UUID
    to prevent duplicates when the device reconnects to the internet.
    """
    permission_classes = (IsAuthenticated,)

    def post(self, request):
        serializer = BulkExpenseSyncSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        results = serializer.save()
        return Response({'results': results}, status=status.HTTP_200_OK)
