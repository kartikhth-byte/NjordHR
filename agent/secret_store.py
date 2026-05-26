import json


class SecretStoreError(RuntimeError):
    pass


def _load_keyring():
    try:
        import keyring
    except ImportError as exc:
        raise SecretStoreError(
            "keyring is required for secure Outlook token storage. Install the 'keyring' package."
        ) from exc
    return keyring


class SecretStore:
    CHUNK_SIZE = 1800

    def __init__(self, service_name="NjordHR", backend=None):
        self.service_name = service_name
        self.backend = backend

    def _backend(self):
        return self.backend or _load_keyring()

    def available(self):
        try:
            backend = self._backend()
            probe_key = "__njordhr_secret_store_probe__"
            probe_value = "ok"
            backend.set_password(self.service_name, probe_key, probe_value)
            readable = backend.get_password(self.service_name, probe_key) == probe_value
            try:
                backend.delete_password(self.service_name, probe_key)
            except Exception:
                pass
            return readable
        except Exception:
            return False

    def get(self, key):
        backend = self._backend()
        manifest = backend.get_password(self.service_name, self._manifest_key(key))
        if manifest:
            try:
                payload = json.loads(manifest)
                chunk_count = int(payload.get("chunks", 0))
                if chunk_count > 0:
                    parts = []
                    for index in range(chunk_count):
                        part = backend.get_password(self.service_name, self._chunk_key(key, index))
                        if part is None:
                            return None
                        parts.append(part)
                    return "".join(parts)
            except Exception:
                return None
        return backend.get_password(self.service_name, key)

    def set(self, key, value):
        backend = self._backend()
        text = "" if value is None else str(value)
        self._delete_existing(backend, key)
        if len(text) <= self.CHUNK_SIZE:
            backend.set_password(self.service_name, key, text)
            return
        chunks = [text[index:index + self.CHUNK_SIZE] for index in range(0, len(text), self.CHUNK_SIZE)]
        for index, chunk in enumerate(chunks):
            backend.set_password(self.service_name, self._chunk_key(key, index), chunk)
        backend.set_password(
            self.service_name,
            self._manifest_key(key),
            json.dumps({"version": 1, "chunks": len(chunks)}, separators=(",", ":")),
        )

    def delete(self, key):
        backend = self._backend()
        self._delete_existing(backend, key)

    def _manifest_key(self, key):
        return f"{key}.__chunks__"

    def _chunk_key(self, key, index):
        return f"{key}.__chunk__.{index:04d}"

    def _delete_existing(self, backend, key):
        manifest = None
        try:
            manifest = backend.get_password(self.service_name, self._manifest_key(key))
        except Exception:
            manifest = None
        try:
            backend.delete_password(self.service_name, key)
        except Exception:
            pass
        if manifest:
            try:
                payload = json.loads(manifest)
                chunk_count = int(payload.get("chunks", 0))
            except Exception:
                chunk_count = 0
            for index in range(max(0, chunk_count)):
                try:
                    backend.delete_password(self.service_name, self._chunk_key(key, index))
                except Exception:
                    pass
        try:
            backend.delete_password(self.service_name, self._manifest_key(key))
        except Exception:
            pass
