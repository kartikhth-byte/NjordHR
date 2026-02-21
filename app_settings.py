import configparser
import os
from dataclasses import dataclass


def _env_bool(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class FeatureFlags:
    use_supabase_db: bool
    use_dual_write: bool
    use_supabase_reads: bool
    use_local_agent: bool
    use_cloud_export: bool


@dataclass(frozen=True)
class AppSettings:
    config: configparser.ConfigParser
    credentials: configparser.SectionProxy
    settings: configparser.SectionProxy
    feature_flags: FeatureFlags
    server_url: str


def load_app_settings():
    config_path = os.getenv("NJORDHR_CONFIG_PATH", "config.ini")
    parser = configparser.ConfigParser()
    parser.read(config_path)

    if "Credentials" not in parser or "Settings" not in parser:
        raise RuntimeError(
            f"Missing required sections in config file: {config_path}. "
            "Expected [Credentials] and [Settings]."
        )

    flags = FeatureFlags(
        use_supabase_db=_env_bool("USE_SUPABASE_DB", default=False),
        use_dual_write=_env_bool("USE_DUAL_WRITE", default=False),
        use_supabase_reads=_env_bool("USE_SUPABASE_READS", default=False),
        use_local_agent=_env_bool("USE_LOCAL_AGENT", default=False),
        use_cloud_export=_env_bool("USE_CLOUD_EXPORT", default=False),
    )

    server_url = os.getenv("NJORDHR_SERVER_URL", "http://127.0.0.1:5000")
    return AppSettings(
        config=parser,
        credentials=parser["Credentials"],
        settings=parser["Settings"],
        feature_flags=flags,
        server_url=server_url,
    )
