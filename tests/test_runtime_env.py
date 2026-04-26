import unittest

from runtime_env import normalize_env_value, normalized_url


class RuntimeEnvTests(unittest.TestCase):
    def test_normalize_env_value_strips_pasted_smart_quotes(self):
        self.assertEqual(
            normalize_env_value("“https://ljmhkweutnnrdglvetjr.supabase.co”"),
            "https://ljmhkweutnnrdglvetjr.supabase.co",
        )

    def test_normalized_url_trims_quotes_and_trailing_slash(self):
        self.assertEqual(
            normalized_url("'“https://ljmhkweutnnrdglvetjr.supabase.co/”'"),
            "https://ljmhkweutnnrdglvetjr.supabase.co",
        )

    def test_normalize_env_value_strips_plain_single_quotes(self):
        self.assertEqual(
            normalize_env_value("'sb_secret_test'"),
            "sb_secret_test",
        )


if __name__ == "__main__":
    unittest.main()
