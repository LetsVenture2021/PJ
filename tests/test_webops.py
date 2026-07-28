import json
import unittest
from unittest.mock import patch

import chiefops


class FakeResponse:
    def __init__(self, *, status=200, url, body=b""):
        self.status = status
        self.url = url
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit=-1):
        return self._body

    def geturl(self):
        return self.url


class TestWebOpsHealth(unittest.TestCase):
    def test_access_login_http_200_is_not_application_health(self):
        final_url = (
            "https://aimhi.cloudflareaccess.com/cdn-cgi/access/login/"
            "pj-assistant.ai?kid=sensitive&meta=long-token"
        )
        response = FakeResponse(
            url=final_url,
            body=b"<title>Sign in - Cloudflare Access</title>Send login code",
        )
        with patch.object(
            chiefops.urllib.request, "urlopen", return_value=response
        ):
            result = chiefops.check_website(
                "https://pj-assistant.ai/?token=should-not-return"
            )

        self.assertTrue(result["transport_reachable"])
        self.assertFalse(result["application_healthy"])
        self.assertFalse(result["up"])
        self.assertTrue(result["access_login_required"])
        self.assertEqual(result["status"], "access_login_required")
        rendered = json.dumps(result)
        self.assertNotIn("sensitive", rendered)
        self.assertNotIn("long-token", rendered)
        self.assertNotIn("should-not-return", rendered)

    def test_fetch_url_marks_access_content_unverified_and_sanitizes_urls(self):
        response = FakeResponse(
            url=(
                "https://team.cloudflareaccess.com/cdn-cgi/access/login/app"
                "?meta=secret"
            ),
            body=b"Cloudflare Access Sign in Send login code",
        )
        with patch.object(
            chiefops.urllib.request, "urlopen", return_value=response
        ):
            result = chiefops.fetch_url(
                "https://user:password@pj-assistant.ai/?key=secret"
            )

        self.assertTrue(result["access_login_required"])
        self.assertFalse(result["application_content_verified"])
        self.assertEqual(result["url"], "https://pj-assistant.ai/")
        self.assertNotIn("secret", json.dumps(result))
        self.assertNotIn("password", json.dumps(result))

    def test_public_health_endpoint_can_be_verified_as_healthy(self):
        response = FakeResponse(
            url="https://pj-assistant.ai/health",
            body=b'{"ok":true}',
        )
        with patch.object(
            chiefops.urllib.request, "urlopen", return_value=response
        ):
            result = chiefops.check_website("pj-assistant.ai/health")

        self.assertTrue(result["up"])
        self.assertTrue(result["application_healthy"])
        self.assertFalse(result["access_login_required"])
        self.assertEqual(result["status"], "healthy")
        self.assertEqual(result["final_url"], "https://pj-assistant.ai/health")


if __name__ == "__main__":
    unittest.main()
