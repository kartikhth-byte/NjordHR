import configparser
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app_settings import FeatureFlags, load_app_settings
import backend_server
from cloud_api.app import _require_bearer_token
from cloud_api.runtime import load_cloud_api_settings
from repositories.supabase_feedback_repo import SupabaseFeedbackStore
from repositories.supabase_registry_repo import SupabaseFileRegistry


class SettingsPrecedenceTests(unittest.TestCase):
    def setUp(self):
        self._env = {key: os.environ.get(key) for key in [
            "NJORDHR_CONFIG_PATH",
            "NJORDHR_ADMIN_TOKEN",
            "USE_SUPABASE_DB",
            "USE_DUAL_WRITE",
            "USE_SUPABASE_READS",
            "USE_LOCAL_AGENT",
            "USE_CLOUD_EXPORT",
            "SEAJOB_USERNAME",
            "SEAJOB_PASSWORD",
            "GEMINI_API_KEY",
            "PINECONE_API_KEY",
            "SUPABASE_URL",
            "SUPABASE_SECRET_KEY",
            "SUPABASE_SERVICE_ROLE_KEY",
        ]}

    def tearDown(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _write_config(self, directory: str, advanced_lines: list[str]) -> str:
        config_path = os.path.join(directory, "config.ini")
        with open(config_path, "w", encoding="utf-8") as fh:
            fh.write(
                "[Credentials]\n"
                "Gemini_API_Key = cfg-gemini\n"
                "Pinecone_API_Key = cfg-pinecone\n"
                "Supabase_Secret_Key = cfg-secret\n"
                "Supabase_Service_Role_Key = cfg-role\n"
                "\n"
                "[Settings]\n"
                "Default_Download_Folder = Downloads\n"
                "LLM_Promotion_Stage = 1\n"
                "\n"
                "[Advanced]\n"
                "reasoning_model_name = gemini-3.1-flash-lite\n"
                "admin_token = cfg-token\n"
                "supabase_url = https://cfg.supabase.co\n"
                + "\n".join(advanced_lines)
                + "\n"
            )
        return config_path

    def test_load_app_settings_prefers_config_over_env_for_feature_flags(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = self._write_config(
                tmp_dir,
                [
                    "use_supabase_db = false",
                    "use_dual_write = true",
                    "use_supabase_reads = false",
                    "use_local_agent = true",
                    "use_cloud_export = false",
                ],
            )
            with patch.dict(
                os.environ,
                {
                    "NJORDHR_CONFIG_PATH": config_path,
                    "USE_SUPABASE_DB": "true",
                    "USE_DUAL_WRITE": "false",
                    "USE_SUPABASE_READS": "true",
                    "USE_LOCAL_AGENT": "false",
                    "USE_CLOUD_EXPORT": "true",
                },
                clear=False,
            ):
                settings = load_app_settings()

        self.assertFalse(settings.feature_flags.use_supabase_db)
        self.assertTrue(settings.feature_flags.use_dual_write)
        self.assertFalse(settings.feature_flags.use_supabase_reads)
        self.assertTrue(settings.feature_flags.use_local_agent)
        self.assertFalse(settings.feature_flags.use_cloud_export)

    def test_load_app_settings_falls_back_to_env_when_flag_missing_from_config(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = self._write_config(
                tmp_dir,
                [
                    "use_supabase_db = false",
                    "use_supabase_reads = false",
                    "use_local_agent = false",
                    "use_cloud_export = false",
                ],
            )
            with patch.dict(
                os.environ,
                {
                    "NJORDHR_CONFIG_PATH": config_path,
                    "USE_DUAL_WRITE": "true",
                },
                clear=False,
            ):
                settings = load_app_settings()

        self.assertTrue(settings.feature_flags.use_dual_write)

    def test_refresh_runtime_managers_uses_reloaded_settings_not_env(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            fake_settings = SimpleNamespace(
                config=configparser.ConfigParser(),
                credentials=configparser.ConfigParser(),
                settings=configparser.ConfigParser(),
                feature_flags=FeatureFlags(False, True, False, True, False),
                server_url="http://127.0.0.1:5000",
            )
            fake_settings.config.add_section("Credentials")
            fake_settings.config.add_section("Settings")
            fake_settings.config.add_section("Advanced")
            fake_settings.config.set(
                "Advanced",
                "search_scope_db_path",
                os.path.join(tmp_dir, "scope.db"),
            )
            fake_settings.credentials = fake_settings.config["Credentials"]
            fake_settings.settings = fake_settings.config["Settings"]

            sentinel_repo = Mock(name="candidate_event_repo")
            with patch.dict(
                os.environ,
                {
                    "USE_SUPABASE_DB": "true",
                    "USE_DUAL_WRITE": "false",
                    "USE_SUPABASE_READS": "true",
                    "USE_LOCAL_AGENT": "false",
                    "USE_CLOUD_EXPORT": "true",
                },
                clear=False,
            ):
                with patch("backend_server.load_app_settings", return_value=fake_settings):
                    with patch("backend_server._load_runtime_secrets_from_cloud", autospec=True) as load_cloud:
                        with patch("backend_server._resolve_verified_resumes_dir", return_value="/tmp/njordhr-verified"):
                            with patch("backend_server.os.makedirs", autospec=True) as makedirs:
                                with patch("backend_server.build_candidate_event_repo", return_value=sentinel_repo) as build_repo:
                                    backend_server._refresh_runtime_managers()

        self.assertFalse(backend_server.feature_flags.use_supabase_db)
        self.assertTrue(backend_server.feature_flags.use_dual_write)
        self.assertFalse(backend_server.feature_flags.use_supabase_reads)
        self.assertTrue(backend_server.feature_flags.use_local_agent)
        self.assertFalse(backend_server.feature_flags.use_cloud_export)
        build_repo.assert_called_once()
        load_cloud.assert_called_once()
        makedirs.assert_any_call("/tmp/njordhr-verified", exist_ok=True)
        self.assertIs(backend_server.csv_manager, sentinel_repo)

    def test_cloud_api_runtime_prefers_config_auth_mode_over_env(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = self._write_config(
                tmp_dir,
                [
                    "use_supabase_db = true",
                    "use_local_agent = true",
                    "use_cloud_export = false",
                    "auth_mode = cloud",
                ],
            )
            with patch.dict(
                os.environ,
                {
                    "NJORDHR_CONFIG_PATH": config_path,
                    "NJORDHR_AUTH_MODE": "local",
                },
                clear=False,
            ):
                settings = load_cloud_api_settings()

        self.assertEqual(settings.auth_mode, "cloud")

    def test_cloud_api_bearer_token_prefers_config_over_env(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = self._write_config(
                tmp_dir,
                [
                    "use_supabase_db = false",
                    "use_dual_write = false",
                    "use_supabase_reads = false",
                    "use_local_agent = false",
                    "use_cloud_export = false",
                ],
            )
            with patch.dict(
                os.environ,
                {
                    "NJORDHR_CONFIG_PATH": config_path,
                    "NJORDHR_API_TOKEN": "env-token",
                    "NJORDHR_ADMIN_TOKEN": "env-admin-token",
                },
                clear=False,
            ):
                with backend_server.app.test_request_context(
                    "/v1/ping",
                    headers={"Authorization": "Bearer cfg-token"},
                ):
                    self.assertIsNone(_require_bearer_token(load_cloud_api_settings()))
                with backend_server.app.test_request_context(
                    "/v1/ping",
                    headers={"Authorization": "Bearer env-token"},
                ):
                    self.assertIsNotNone(_require_bearer_token(load_cloud_api_settings()))

    def test_backend_auth_mode_preference_prefers_config_over_env(self):
        parser = configparser.ConfigParser()
        parser.add_section("Advanced")
        parser.set("Advanced", "auth_mode", "cloud")
        with patch.object(backend_server, "config", parser):
            with patch.dict(os.environ, {"NJORDHR_AUTH_MODE": "local"}, clear=False):
                self.assertEqual(backend_server._auth_mode_preference(), "cloud")

    def test_supabase_stores_use_config_supabase_url_when_ctor_args_missing(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = self._write_config(
                tmp_dir,
                [
                    "use_supabase_db = true",
                    "use_local_agent = true",
                    "use_cloud_export = false",
                ],
            )
            with patch.dict(
                os.environ,
                {
                    "NJORDHR_CONFIG_PATH": config_path,
                    "SUPABASE_URL": "https://env.supabase.co",
                },
                clear=False,
            ):
                registry = SupabaseFileRegistry()
                feedback = SupabaseFeedbackStore()

        self.assertEqual(registry.supabase_url, "https://cfg.supabase.co")
        self.assertEqual(feedback.supabase_url, "https://cfg.supabase.co")

    def test_load_runtime_secrets_from_cloud_clears_stale_env_values(self):
        with patch.object(backend_server, "feature_flags", SimpleNamespace(use_supabase_db=True)):
            with patch.object(
                backend_server,
                "_supabase_runtime_config_get",
                return_value={
                    "seajob_username": "",
                    "seajob_password": "",
                    "gemini_api_key": "cloud-gemini",
                },
            ):
                with patch.dict(
                    os.environ,
                    {
                        "SEAJOB_USERNAME": "stale-user",
                        "SEAJOB_PASSWORD": "stale-pass",
                        "GEMINI_API_KEY": "stale-gemini",
                        "PINECONE_API_KEY": "stale-pinecone",
                    },
                    clear=False,
                ):
                    backend_server._load_runtime_secrets_from_cloud()
                    self.assertNotIn("SEAJOB_USERNAME", os.environ)
                    self.assertNotIn("SEAJOB_PASSWORD", os.environ)
                    self.assertEqual(os.environ.get("GEMINI_API_KEY"), "cloud-gemini")
                    self.assertNotIn("PINECONE_API_KEY", os.environ)

    def test_admin_settings_persists_email_intake_fields_in_config(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = self._write_config(
                tmp_dir,
                [
                    "use_supabase_db = false",
                    "use_dual_write = false",
                    "use_supabase_reads = false",
                    "use_local_agent = false",
                    "use_cloud_export = false",
                ],
            )
            parser = configparser.ConfigParser()
            parser.read(config_path)
            fake_settings = SimpleNamespace(
                config=parser,
                credentials=parser["Credentials"],
                settings=parser["Settings"],
                feature_flags=FeatureFlags(False, False, False, False, False),
                server_url="http://127.0.0.1:5000",
            )

            with patch.dict(os.environ, {"NJORDHR_CONFIG_PATH": config_path}, clear=False):
                with patch.object(backend_server, "app_settings", fake_settings):
                    with patch.object(backend_server, "config", parser):
                        with patch.object(backend_server, "creds", parser["Credentials"]):
                            with patch.object(backend_server, "settings", parser["Settings"]):
                                with patch.object(backend_server, "feature_flags", fake_settings.feature_flags):
                                    with patch.object(backend_server, "_require_admin", return_value=(True, "")):
                                        with patch.object(backend_server, "_refresh_runtime_managers"):
                                            with backend_server.app.test_request_context(
                                                "/admin/settings",
                                                method="POST",
                                                json={
                                                    "settings": {
                                                        "email_intake_enabled": True,
                                                        "email_intake_mailbox": "recruitment@example.com",
                                                        "outlook_client_id": "client-123",
                                                        "outlook_tenant_id": "organizations",
                                                    }
                                                },
                                            ):
                                                response = backend_server.save_admin_settings()

            self.assertEqual(response.status_code, 200)
            reread = configparser.ConfigParser()
            reread.read(config_path)
            self.assertEqual(reread.get("Advanced", "email_intake_enabled"), "true")
            self.assertEqual(reread.get("Advanced", "email_intake_mailbox"), "recruitment@example.com")
            self.assertEqual(reread.get("Advanced", "outlook_client_id"), "client-123")
            self.assertEqual(reread.get("Advanced", "outlook_tenant_id"), "organizations")

            with patch.object(backend_server, "config", reread):
                with patch.object(backend_server, "settings", reread["Settings"]):
                    with patch.object(backend_server, "feature_flags", fake_settings.feature_flags):
                        payload = backend_server._settings_payload()

            self.assertEqual(payload["non_secret"]["email_intake_mailbox"], "recruitment@example.com")
            self.assertEqual(payload["non_secret"]["outlook_client_id"], "client-123")
            self.assertEqual(payload["non_secret"]["outlook_tenant_id"], "organizations")
            self.assertTrue(payload["non_secret"]["email_intake_enabled"])

    def test_settings_payload_preserves_zero_poll_interval_from_local_agent(self):
        parser = configparser.ConfigParser()
        parser.read_dict({
            "Settings": {"Default_Download_Folder": "", "Additional_Local_Folder": "Verified_Resumes"},
            "Advanced": {},
            "Credentials": {},
        })
        fake_feature_flags = FeatureFlags(False, False, False, True, False)
        agent_settings = {
            "email_intake_enabled": True,
            "email_intake_mailbox": "recruitment@example.com",
            "email_intake_poll_interval_seconds": 0,
        }

        class _Resp:
            status_code = 200

            @staticmethod
            def json():
                return {"settings": agent_settings}

        with patch.object(backend_server, "config", parser):
            with patch.object(backend_server, "settings", parser["Settings"]):
                with patch.object(backend_server, "feature_flags", fake_feature_flags):
                    with patch.object(backend_server, "_agent_request", return_value=_Resp()):
                        payload = backend_server._settings_payload()

        self.assertEqual(payload["non_secret"]["email_intake_poll_interval_seconds"], 0)

    def test_settings_payload_preserves_blank_outlook_tenant_id_from_local_agent(self):
        parser = configparser.ConfigParser()
        parser.read_dict({
            "Settings": {"Default_Download_Folder": "", "Additional_Local_Folder": "Verified_Resumes"},
            "Advanced": {},
            "Credentials": {},
        })
        fake_feature_flags = FeatureFlags(False, False, False, True, False)
        agent_settings = {
            "email_intake_enabled": True,
            "email_intake_mailbox": "recruitment@example.com",
            "outlook_client_id": "",
            "outlook_tenant_id": "",
        }

        class _Resp:
            status_code = 200

            @staticmethod
            def json():
                return {"settings": agent_settings}

        with patch.object(backend_server, "config", parser):
            with patch.object(backend_server, "settings", parser["Settings"]):
                with patch.object(backend_server, "feature_flags", fake_feature_flags):
                    with patch.object(backend_server, "_agent_request", return_value=_Resp()):
                        payload = backend_server._settings_payload()

        self.assertEqual(payload["non_secret"]["outlook_tenant_id"], "")

    def test_save_admin_settings_clears_empty_values_and_reloads(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = self._write_config(
                tmp_dir,
                [
                    "use_supabase_db = false",
                    "use_dual_write = false",
                    "use_supabase_reads = false",
                    "use_local_agent = false",
                    "use_cloud_export = false",
                ],
            )
            parser = configparser.ConfigParser()
            parser.read(config_path)
            fake_settings = SimpleNamespace(
                config=parser,
                credentials=parser["Credentials"],
                settings=parser["Settings"],
                feature_flags=FeatureFlags(False, False, False, False, False),
                server_url="http://127.0.0.1:5000",
            )

            def _load_settings_from_disk():
                reread = configparser.ConfigParser()
                reread.read(config_path)
                return SimpleNamespace(
                    config=reread,
                    credentials=reread["Credentials"],
                    settings=reread["Settings"],
                    feature_flags=FeatureFlags(False, False, False, False, False),
                    server_url="http://127.0.0.1:5000",
                )

            with patch.dict(os.environ, {"NJORDHR_CONFIG_PATH": config_path, "GEMINI_API_KEY": "env-gemini"}, clear=False):
                with patch.object(backend_server, "app_settings", fake_settings):
                    with patch.object(backend_server, "config", parser):
                        with patch.object(backend_server, "creds", parser["Credentials"]):
                            with patch.object(backend_server, "settings", parser["Settings"]):
                                with patch.object(backend_server, "feature_flags", fake_settings.feature_flags):
                                    with patch.object(backend_server, "_require_admin", return_value=(True, "")):
                                        with patch.object(backend_server, "_refresh_runtime_managers") as refresh:
                                            with patch.object(backend_server, "load_app_settings", side_effect=_load_settings_from_disk):
                                                with backend_server.app.test_request_context(
                                                    "/admin/settings",
                                                    method="POST",
                                                    json={"settings": {"gemini_api_key": ""}},
                                                ):
                                                    response = backend_server.save_admin_settings()

            self.assertEqual(response.status_code, 200)
            self.assertFalse(parser.has_option("Credentials", "Gemini_API_Key"))
            self.assertNotIn("GEMINI_API_KEY", os.environ)
            refresh.assert_called_once()
            reread = configparser.ConfigParser()
            reread.read(config_path)
            self.assertFalse(reread.has_option("Credentials", "Gemini_API_Key"))

    def test_change_admin_password_refreshes_runtime_without_env_writes(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = self._write_config(
                tmp_dir,
                [
                    "use_supabase_db = false",
                    "use_dual_write = false",
                    "use_supabase_reads = false",
                    "use_local_agent = false",
                    "use_cloud_export = false",
                ],
            )
            parser = configparser.ConfigParser()
            parser.read(config_path)
            fake_settings = SimpleNamespace(
                config=parser,
                credentials=parser["Credentials"],
                settings=parser["Settings"],
                feature_flags=FeatureFlags(False, False, False, False, False),
                server_url="http://127.0.0.1:5000",
            )

            def _load_settings_from_disk():
                reread = configparser.ConfigParser()
                reread.read(config_path)
                return SimpleNamespace(
                    config=reread,
                    credentials=reread["Credentials"],
                    settings=reread["Settings"],
                    feature_flags=FeatureFlags(False, False, False, False, False),
                    server_url="http://127.0.0.1:5000",
                )

            with patch.dict(os.environ, {"NJORDHR_CONFIG_PATH": config_path, "NJORDHR_ADMIN_TOKEN": "old-token"}, clear=False):
                with patch.object(backend_server, "app_settings", fake_settings):
                    with patch.object(backend_server, "config", parser):
                        with patch.object(backend_server, "creds", parser["Credentials"]):
                            with patch.object(backend_server, "settings", parser["Settings"]):
                                with patch.object(backend_server, "_require_admin", return_value=(True, "")):
                                    with patch.object(backend_server, "_refresh_runtime_managers") as refresh:
                                        with patch.object(backend_server, "load_app_settings", side_effect=_load_settings_from_disk):
                                            with backend_server.app.test_request_context(
                                                "/admin/settings/change_password",
                                                method="POST",
                                                json={
                                                    "new_admin_password": "new-settings-token",
                                                    "confirm_admin_password": "new-settings-token",
                                                },
                                            ):
                                                response = backend_server.change_admin_password()

                self.assertEqual(response.status_code, 200)
                self.assertEqual(os.environ.get("NJORDHR_ADMIN_TOKEN"), "old-token")
                self.assertEqual(parser.get("Advanced", "admin_token"), "new-settings-token")
                refresh.assert_called_once()
                reread = configparser.ConfigParser()
                reread.read(config_path)
                self.assertEqual(reread.get("Advanced", "admin_token"), "new-settings-token")


if __name__ == "__main__":
    unittest.main()
