"""
DRF permission class for the Super-Admin space.

Access is strictly limited to authenticated superusers (`is_superuser=True`).
"""
from rest_framework.permissions import BasePermission


class IsSuperAdmin(BasePermission):
    """Only superusers can access the admin API."""
    message = "Accès réservé au super-admin du système."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_superuser
        )