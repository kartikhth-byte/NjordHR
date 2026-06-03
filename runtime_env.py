import configparser
import os
import re


QUOTE_LIKE_CODEPOINTS = re.compile(r"[\u2018\u2019\u201A\u201B\u201C\u201D\u201E\u201F\u00AB\u00BB\u2032\u2033]")


def normalize_env_value(raw_value):
    if raw_value is None:
        return ""

    value = QUOTE_LIKE_CODEPOINTS.sub('"', str(raw_value).strip())
    while len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1].strip()
    return value.strip()


def normalized_url(raw_value):
    return normalize_env_value(raw_value).rstrip("/")


def _load_config_parser(config_path=None):
    parser = configparser.ConfigParser(interpolation=None)
    candidate = normalize_env_value(config_path or os.getenv("NJORDHR_CONFIG_PATH", "config.ini"))
    if candidate:
        parser.read(candidate)
    return parser


def config_value(section, key, default="", config_path=None):
    parser = _load_config_parser(config_path=config_path)
    if parser.has_option(section, key):
        value = normalize_env_value(parser.get(section, key, fallback=""))
        if value:
            return value
    return normalize_env_value(default)


def config_bool(section, key, default=False, config_path=None):
    parser = _load_config_parser(config_path=config_path)
    if parser.has_option(section, key):
        raw = normalize_env_value(parser.get(section, key, fallback=""))
        if raw:
            return raw.lower() in {"1", "true", "yes", "on"}
    return bool(default)
