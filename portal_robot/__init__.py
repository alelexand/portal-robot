"""Web robot (RPA) that signs in to a portal, downloads reports and registers invoices."""

from .config import Settings
from .robot import InvoiceRequest, LoginError, PortalRobot, ReportJob, TaskResult

__all__ = ["InvoiceRequest", "LoginError", "PortalRobot", "ReportJob", "Settings", "TaskResult"]
__version__ = "1.0.0"
