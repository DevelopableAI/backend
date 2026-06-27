import tempfile
import unittest
from pathlib import Path

from agents.deployment import Deployment


class FakeProvider:
    display_name = "Fake Cloud"

    def __init__(self, detected=None, collected=None, validations=None):
        self._detected = detected
        self._collected = list(collected or [])
        self._validations = list(validations or [])
        self.collect_calls = 0
        self.validate_calls = []

    def detect_credentials(self):
        return self._detected

    def collect_credentials(self):
        self.collect_calls += 1
        if not self._collected:
            raise AssertionError("collect_credentials called too many times")
        return self._collected.pop(0)

    def validate_credentials(self, credentials):
        self.validate_calls.append(credentials)
        if not self._validations:
            raise AssertionError("validate_credentials called too many times")
        return self._validations.pop(0)


class DeploymentCredentialResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.deployment = Deployment(out_dir=Path(self.temp_dir.name), provider="aws")

    def test_uses_valid_detected_credentials_without_prompting(self):
        provider = FakeProvider(
            detected={"token": "cached"},
            validations=[(True, None)],
        )

        resolved = self.deployment._resolve_credentials(provider)

        self.assertEqual(resolved, {"token": "cached"})
        self.assertEqual(provider.collect_calls, 0)
        self.assertEqual(provider.validate_calls, [{"token": "cached"}])

    def test_falls_back_to_manual_credentials_after_invalid_detected_credentials(self):
        provider = FakeProvider(
            detected={"token": "stale"},
            collected=[{"token": "fresh"}],
            validations=[
                (False, "stale token"),
                (True, None),
            ],
        )

        resolved = self.deployment._resolve_credentials(provider)

        self.assertEqual(resolved, {"token": "fresh"})
        self.assertEqual(provider.collect_calls, 1)
        self.assertEqual(
            provider.validate_calls,
            [{"token": "stale"}, {"token": "fresh"}],
        )

    def test_stops_after_two_invalid_manual_attempts(self):
        provider = FakeProvider(
            detected=None,
            collected=[{"token": "first"}, {"token": "second"}],
            validations=[
                (False, "bad first token"),
                (False, "bad second token"),
            ],
        )

        with self.assertRaises(SystemExit) as exc:
            self.deployment._resolve_credentials(provider)

        self.assertEqual(exc.exception.code, 1)
        self.assertEqual(provider.collect_calls, 2)
        self.assertEqual(
            provider.validate_calls,
            [{"token": "first"}, {"token": "second"}],
        )


if __name__ == "__main__":
    unittest.main()
