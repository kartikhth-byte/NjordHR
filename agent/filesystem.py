import os


def normalize_folder(path_value):
    return os.path.abspath(os.path.expanduser(str(path_value or "").strip()))


def ensure_writable_folder(path_value):
    path = normalize_folder(path_value)
    if not path:
        return False, "Folder path is empty", ""
    try:
        os.makedirs(path, exist_ok=True)
    except Exception as exc:
        return False, f"Failed to create folder: {exc}", path

    if not os.path.isdir(path):
        return False, "Path is not a directory", path
    if not os.access(path, os.W_OK):
        return False, "Folder is not writable", path
    return True, "", path

