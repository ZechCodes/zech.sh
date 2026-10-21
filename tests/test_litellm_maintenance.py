"""Keep the independently deployed proxy trust repair reproducible."""
import hashlib
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "ops/litellm"


class LiteLLMMaintenanceTests(unittest.TestCase):
    def test_only_existing_proxy_base_is_used(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertIn("ghcr.io/berriai/litellm@sha256:20b5044b619055374061a6d5b7b08754cad75aeabbf82ddf4f69cc0cf80ddaf4", dockerfile)
        self.assertIn("ca-certificates-bundle=20260909-r3", dockerfile)
        self.assertIn("python -S /audit/verify_trust.py", dockerfile)

    def test_offline_certifi_wheel_and_public_fixtures_are_complete(self):
        wheel = ROOT / "certifi-2026.10.19-py3-none-any.whl"
        self.assertEqual(hashlib.sha256(wheel.read_bytes()).hexdigest(), "42d35340cc5b9ba78e818ddbff0c5b1c2b779de448a340fd1b989f5fa8670e1b")
        fixtures = json.loads((ROOT / "impacted-intermediates.json").read_text())
        self.assertEqual(len(fixtures), 16)
        self.assertTrue(all("-----BEGIN CERTIFICATE-----" in fixture["pem"] for fixture in fixtures))


if __name__ == "__main__":
    unittest.main()
