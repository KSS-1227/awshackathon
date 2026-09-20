"""
Audit models — re-exported from audit_service for backward-compat imports.

backend.auth.routes.audit imports AuditLogPage from here.
The actual definitions live in backend.auth.services.audit_service so that
the service can use them directly without a circular import.
"""
from backend.auth.services.audit_service import AuditLogEntry, AuditLogPage  # noqa: F401

__all__ = ["AuditLogEntry", "AuditLogPage"]
