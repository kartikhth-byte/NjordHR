import os
import unittest
from unittest.mock import patch

from cloud_api.app import create_app
import cloud_api.__main__ as cloud_api_main


class CloudApiScaffoldTests(unittest.TestCase):
    def setUp(self):
        self._env = {key: os.environ.get(key) for key in [
            "USE_SUPABASE_DB",
            "USE_LOCAL_AGENT",
            "USE_CLOUD_EXPORT",
            "NJORDHR_AUTH_MODE",
            "SUPABASE_URL",
            "SUPABASE_SECRET_KEY",
            "SUPABASE_SERVICE_ROLE_KEY",
            "NJORDHR_API_TOKEN",
            "NJORDHR_ADMIN_TOKEN",
        ]}

    def tearDown(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_health_reflects_feature_flags(self):
        os.environ["USE_SUPABASE_DB"] = "true"
        os.environ["USE_LOCAL_AGENT"] = "false"
        os.environ["USE_CLOUD_EXPORT"] = "true"
        os.environ["SUPABASE_URL"] = "https://example.supabase.co"
        os.environ["SUPABASE_SECRET_KEY"] = "sb_secret_test"

        app = create_app()
        with app.test_client() as client:
            resp = client.get("/health")
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["feature_flags"]["use_supabase_db"])
        self.assertFalse(payload["feature_flags"]["use_local_agent"])
        self.assertTrue(payload["feature_flags"]["use_cloud_export"])

    def test_runtime_ready_requires_supabase_credentials_when_enabled(self):
        os.environ["USE_SUPABASE_DB"] = "true"
        os.environ.pop("SUPABASE_URL", None)
        os.environ.pop("SUPABASE_SECRET_KEY", None)
        os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)

        app = create_app()
        with app.test_client() as client:
            resp = client.get("/runtime/ready")
        self.assertEqual(resp.status_code, 503)
        payload = resp.get_json()
        self.assertEqual(payload["status"], "not_ready")
        self.assertEqual(payload["ready_reason"], "missing_supabase_credentials")

    def test_bearer_token_protects_non_health_routes(self):
        os.environ["NJORDHR_API_TOKEN"] = "token-123"
        app = create_app()
        with app.test_client() as client:
            unauthorized = client.get("/v1/ping")
            authorized = client.get("/v1/ping", headers={"Authorization": "Bearer token-123"})
        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(authorized.status_code, 200)
        self.assertEqual(authorized.get_json()["message"], "pong")

    def test_module_entrypoint_uses_cloud_api_host_and_port(self):
        class DummySettings:
            ready = True
            ready_reason = "ok"

        class DummyApp:
            def __init__(self):
                self.config = {"NJORDHR_CLOUD_API_SETTINGS": DummySettings()}
                self.run_calls = []

            def run(self, **kwargs):
                self.run_calls.append(kwargs)

        dummy_app = DummyApp()
        with patch.dict(os.environ, {"NJORDHR_CLOUD_API_HOST": "127.0.0.1", "NJORDHR_CLOUD_API_PORT": "5055"}, clear=False):
            with patch("cloud_api.__main__.create_app", return_value=dummy_app):
                cloud_api_main.main()

        self.assertEqual(dummy_app.run_calls, [{"host": "127.0.0.1", "port": 5055, "debug": False, "threaded": True}])


if __name__ == "__main__":
    unittest.main()
