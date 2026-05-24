import os
import tempfile


def normalize_folder(path_value):
    raw = str(path_value or "").strip()
    if not raw:
        return ""
    return os.path.abspath(os.path.expanduser(raw))


def ensure_writable_folder(path_value):
    path = normalize_folder(path_value)
    if not path:
        return False, "Folder path is empty", ""
    if os.path.exists(path) and not os.path.isdir(path):
        return False, "Path is not a directory", path
    try:
        os.makedirs(path, exist_ok=True)
    except Exception as exc:
        return False, f"Failed to create folder: {exc}", path

    if not os.path.isdir(path):
        return False, "Path is not a directory", path

    probe_fd = None
    probe_path = None
    try:
        probe_fd, probe_path = tempfile.mkstemp(prefix=".njordhr_write_probe_", dir=path)
        os.write(probe_fd, b"")
    except Exception as exc:
        return False, f"Folder is not writable: {exc}", path
    finally:
        if probe_fd is not None:
            try:
                os.close(probe_fd)
            except Exception:
                pass
        if probe_path and os.path.exists(probe_path):
            try:
                os.unlink(probe_path)
            except Exception:
                pass

    return True, "", path
