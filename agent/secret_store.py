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
    def __init__(self, service_name="NjordHR", backend=None):
        self.service_name = service_name
        self.backend = backend

    def _backend(self):
        return self.backend or _load_keyring()

    def available(self):
        try:
            self._backend()
            return True
        except SecretStoreError:
            return False

    def get(self, key):
        return self._backend().get_password(self.service_name, key)

    def set(self, key, value):
        self._backend().set_password(self.service_name, key, value)

    def delete(self, key):
        backend = self._backend()
        try:
            backend.delete_password(self.service_name, key)
        except Exception:
            return
