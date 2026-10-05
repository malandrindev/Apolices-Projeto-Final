"""Testes das configurações sem credenciais reais ou chamadas de rede."""

import os
import unittest
from unittest.mock import patch

from src.config import ConfigurationError, get_settings


class SettingsTests(unittest.TestCase):
    def test_missing_api_key_has_friendly_message(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("src.config.load_dotenv"),
            self.assertRaisesRegex(
                ConfigurationError, "Defina GROQ_API_KEY no arquivo .env"
            ),
        ):
            get_settings()

    def test_settings_load_models_and_safe_defaults(self) -> None:
        values = {
            "GROQ_API_KEY": "test-key-not-a-real-secret",
            "GROQ_MODEL_FAST": "fast-test-model",
            "GROQ_MODEL_STRONG": "strong-test-model",
            "GROQ_MODEL_VISION": "vision-test-model",
        }
        with patch.dict(os.environ, values, clear=True), patch("src.config.load_dotenv"):
            settings = get_settings()

        self.assertEqual(settings.model_fast, "fast-test-model")
        self.assertEqual(settings.temperature, 0)
        self.assertEqual(settings.max_tokens, 1024)
        self.assertNotIn("test-key-not-a-real-secret", repr(settings))


if __name__ == "__main__":
    unittest.main()
