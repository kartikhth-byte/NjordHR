import os
import unittest
from unittest.mock import patch

from agent.main import main


class _DummyApp:
    def __init__(self):
        self.run_calls = []

    def run(self, **kwargs):
        self.run_calls.append(kwargs)


class AgentEntrypointTests(unittest.TestCase):
    def setUp(self):
        self._env = {key: os.environ.get(key) for key in [
            "NJORDHR_AGENT_PORT",
            "NJORDHR_AGENT_HOST",
        ]}

    def tearDown(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_main_uses_env_host_and_port(self):
        os.environ["NJORDHR_AGENT_PORT"] = "5059"
        os.environ["NJORDHR_AGENT_HOST"] = "0.0.0.0"
        dummy = _DummyApp()

        with patch("agent.main.create_agent_app", return_value=dummy):
            main()

        self.assertEqual(len(dummy.run_calls), 1)
        self.assertEqual(dummy.run_calls[0]["host"], "0.0.0.0")
        self.assertEqual(dummy.run_calls[0]["port"], 5059)
        self.assertFalse(dummy.run_calls[0]["debug"])
        self.assertTrue(dummy.run_calls[0]["threaded"])


if __name__ == "__main__":
    unittest.main()
