"""HTTP policy tests."""

from __future__ import annotations

import unittest
from unittest.mock import patch, MagicMock

from tradetalk_mcp.security.http import HttpPolicyError, api_request, fetch_url


class TestHttp(unittest.TestCase):
    def test_rejects_disallowed_host(self) -> None:
        allow = frozenset({"127.0.0.1"})
        with self.assertRaises(HttpPolicyError):
            fetch_url("http://evil.com/x", host_allowlist=allow)

    @patch("tradetalk_mcp.security.http.urllib.request.urlopen")
    def test_api_request_ok(self, mock_open: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b'{"ok":true}'
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_open.return_value = mock_resp

        code, body = api_request(
            "http://127.0.0.1:8000",
            "/knowledge/stats",
            method="GET",
            json_body=None,
            api_key="",
            host_allowlist=frozenset({"127.0.0.1"}),
        )
        self.assertEqual(code, 200)
        self.assertIn("ok", body)


if __name__ == "__main__":
    unittest.main()
