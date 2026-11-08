"""Limbo: a zero-dependency Python web framework foundation."""
from .app import Limbo
from .router import RouteGroup, Router
from .http import HTTPError, Request, Response
from .database import Database
from .frontend import DependencyGraph, FrontendCompiler, FrontendDevServer, FrontendError, ModuleResolver
from .frontend_engine import (AdapterCapability, AdapterError, AdapterRegistry, BrowserNativeAdapter,
                              CompilerAdapter, EsbuildAdapter, FrameworkSFCAdapter, TransformResult)
from .security import SessionSigner, hash_password, session_cookie, verify_password
from .sessions import Session, SessionBackend, SessionStore, session_middleware
from .csrf import csrf_protect
from .tasks import BackgroundTasks
from .auth import authenticate, authorize
from .templates import TemplateEngine
from .uploads import UploadFile, UploadLimitError
from .middleware import access_log, cors, gzip_compress, rate_limit, request_id, security_headers

from .migrations import DatabaseURLError, Migration, MigrationError, Migrator, load_directory, open_database
from .build_engine import BuildError, BuildReport, build_project
from .tunnel import TunnelError, TunnelSupervisor, detect_cloudflared, redact_secrets
from .runtime_ssr import LocalProcessRuntime, SsrRuntimeError
from .capabilities import capability_matrix

__version__ = "1.0.0"
__all__ = ["Limbo", "Router", "RouteGroup", "Request", "Response", "HTTPError", "Database", "SessionSigner",
           "Session", "SessionBackend", "SessionStore", "session_middleware", "csrf_protect", "BackgroundTasks", "authenticate", "authorize", "TemplateEngine", "UploadFile", "UploadLimitError", "DependencyGraph",
           "FrontendCompiler", "FrontendDevServer", "FrontendError", "ModuleResolver", "hash_password",
           "verify_password", "session_cookie", "cors", "gzip_compress", "security_headers", "request_id",
           "access_log", "rate_limit", "AdapterCapability", "AdapterError", "AdapterRegistry",
           "BrowserNativeAdapter", "CompilerAdapter", "EsbuildAdapter", "FrameworkSFCAdapter", "TransformResult",
           "Migration", "Migrator", "MigrationError", "DatabaseURLError", "load_directory", "open_database",
           "BuildError", "BuildReport", "build_project", "TunnelError", "TunnelSupervisor", "detect_cloudflared",
           "redact_secrets", "LocalProcessRuntime", "SsrRuntimeError", "capability_matrix", "__version__"]
