"""Shared cloud runtime settings and payload helpers for NjordHR."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Dict

from runtime_env import config_bool, config_value, normalized_url


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_value(name: str) -> str:
    return str(os.getenv(name, "") or "").strip()


@dataclass(frozen=True)
class CloudApiSettings:
    service_name: str
    auth_mode: str
    use_supabase_db: bool
    use_local_agent: bool
    use_cloud_export: bool
    supabase_url_configured: bool
    supabase_secret_configured: bool
    supabase_service_role_configured: bool
    api_token_configured: bool

    @property
    def ready(self) -> bool:
        if self.use_supabase_db:
            return self.supabase_url_configured and (
                self.supabase_secret_configured or self.supabase_service_role_configured
            )
        return True

    @property
    def ready_reason(self) -> str:
        if self.ready:
            return "ok"
        return "missing_supabase_credentials"


def load_cloud_api_settings() -> CloudApiSettings:
    supabase_url = config_value("Advanced", "supabase_url", "") or _env_value("SUPABASE_URL")
    supabase_secret_key = config_value("Credentials", "Supabase_Secret_Key", "") or _env_value("SUPABASE_SECRET_KEY")
    supabase_service_role_key = config_value("Credentials", "Supabase_Service_Role_Key", "") or _env_value("SUPABASE_SERVICE_ROLE_KEY")
    api_token = config_value("Advanced", "admin_token", "") or _env_value("NJORDHR_API_TOKEN") or _env_value("NJORDHR_ADMIN_TOKEN")
    auth_mode = config_value("Advanced", "auth_mode", "") or _env_value("NJORDHR_AUTH_MODE") or "cloud"
    return CloudApiSettings(
        service_name="njordhr-cloud-api",
        auth_mode=auth_mode,
        use_supabase_db=config_bool("Advanced", "use_supabase_db", default=_env_bool("USE_SUPABASE_DB", default=False)),
        use_local_agent=config_bool("Advanced", "use_local_agent", default=_env_bool("USE_LOCAL_AGENT", default=False)),
        use_cloud_export=config_bool("Advanced", "use_cloud_export", default=_env_bool("USE_CLOUD_EXPORT", default=False)),
        supabase_url_configured=bool(supabase_url),
        supabase_secret_configured=bool(supabase_secret_key),
        supabase_service_role_configured=bool(supabase_service_role_key),
        api_token_configured=bool(api_token),
    )


def cloud_api_settings_payload(settings: CloudApiSettings) -> Dict[str, Any]:
    return {
        "service_name": settings.service_name,
        "auth_mode": settings.auth_mode,
        "ready": settings.ready,
        "ready_reason": settings.ready_reason,
        "feature_flags": {
            "use_supabase_db": settings.use_supabase_db,
            "use_local_agent": settings.use_local_agent,
            "use_cloud_export": settings.use_cloud_export,
        },
        "credentials": {
            "supabase_url_configured": settings.supabase_url_configured,
            "supabase_secret_configured": settings.supabase_secret_configured,
            "supabase_service_role_configured": settings.supabase_service_role_configured,
            "api_token_configured": settings.api_token_configured,
        },
    }
