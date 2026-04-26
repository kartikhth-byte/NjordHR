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
