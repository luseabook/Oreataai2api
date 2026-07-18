import unittest
from unittest.mock import patch

from gateway.outlook_mail import (
    MailRouter,
    OutlookMailClient,
    is_shop_api_error_payload,
    normalize_mail_payload,
    parse_outlook_import_line,
    parse_outlook_import_text,
)
from gateway.yyds_mail import YydsClient


class OutlookMailParseTests(unittest.TestCase):
    def test_parse_get_url_line(self):
        line = (
            "http://43.153.39.164:8899/get?key=91amail.com&email="
            "AmandaSmith432322@outlook.com----wqxhmx8570131----"
            "9e5f94bc-e8a4-4e73-b8be-63364c29d753----M.C558_token$$"
        )
        parsed = parse_outlook_import_line(line)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["email"], "AmandaSmith432322@outlook.com")
        self.assertEqual(parsed["password"], "wqxhmx8570131")
        self.assertEqual(parsed["client_id"], "9e5f94bc-e8a4-4e73-b8be-63364c29d753")
        self.assertTrue(parsed["refresh_token"].startswith("M.C558_"))
        self.assertEqual(parsed["detected_base_url"], "http://43.153.39.164:8899")
        self.assertEqual(parsed["detected_api_key"], "91amail.com")

    def test_parse_plain_line(self):
        parsed = parse_outlook_import_line(
            "a@outlook.com----pass123----9e5f94bc-e8a4-4e73-b8be-63364c29d753----refresh.token"
        )
        self.assertEqual(parsed["email"], "a@outlook.com")
        self.assertEqual(parsed["refresh_token"], "refresh.token")

    def test_parse_text_collects_accounts(self):
        text = "\n".join(
            [
                "a@outlook.com----p1----9e5f94bc-e8a4-4e73-b8be-63364c29d753----rt1",
                "bad-line",
                "b@outlook.com----p2----9e5f94bc-e8a4-4e73-b8be-63364c29d753----rt2",
            ]
        )
        parsed = parse_outlook_import_text(text)
        self.assertEqual(len(parsed["accounts"]), 2)
        self.assertEqual(len(parsed["errors"]), 1)

    def test_normalize_mail_payload_extracts_nested(self):
        payload = {"data": {"mail": {"subject": "hi", "text": "code 123456", "code": "123456"}}}
        out = normalize_mail_payload(payload)
        self.assertEqual(out["code"], "123456")
        self.assertEqual(out["subject"], "hi")

    def test_shop_api_error_payload_is_rejected(self):
        payload = {"code": 404, "msg": "账号不存在"}
        self.assertTrue(is_shop_api_error_payload(payload))
        with self.assertRaises(RuntimeError):
            normalize_mail_payload(payload)

    def test_extract_activation_link_with_html_entities_and_aiimage_path(self):
        client = YydsClient(lambda: {})
        body = (
            'Verify: <a href="https://www.oreateai.com/home/vertical/aiImage'
            '?email=a%40outlook.com&amp;tokenID=abc-123">click</a>'
        )
        link = client.extract_verify_link({"html": body, "text": body})
        self.assertIn("tokenID=abc-123", link)
        self.assertNotIn("&amp;", link)


class OutlookMailClientTests(unittest.TestCase):
    def test_create_mailbox_uses_pool_claim(self):
        client = OutlookMailClient(
            lambda: {"base_url": "http://example", "api_key": "k", "api_mode": "get"},
            claim_mailbox=lambda: {
                "id": 7,
                "email": "pool@outlook.com",
                "password": "x",
                "client_id": "cid",
                "refresh_token": "rt",
            },
            resolve_mailbox=lambda token: {},
        )
        mailbox = client.create_mailbox()
        self.assertEqual(mailbox["address"], "pool@outlook.com")
        self.assertEqual(mailbox["token"], "7")
        self.assertEqual(mailbox["provider"], "outlook")

    def test_wait_verification_artifact_reads_link(self):
        account = {
            "id": 1,
            "email": "pool@outlook.com",
            "password": "x",
            "client_id": "cid",
            "refresh_token": "rt",
        }
        client = OutlookMailClient(
            lambda: {"base_url": "http://example", "api_key": "k", "api_mode": "get"},
            claim_mailbox=lambda: account,
            resolve_mailbox=lambda token: account,
        )
        with patch.object(
            client,
            "fetch_candidate_messages",
            return_value=[
                {
                    "subject": "Verify",
                    "text": "open https://www.oreateai.com/passport/confirm?tokenID=abc123",
                    "receivedDateTime": "2099-01-01T00:00:00Z",
                }
            ],
        ):
            artifact = client.wait_verification_artifact("pool@outlook.com", "1", timeout_sec=1)
        self.assertIn("tokenID=abc123", artifact["link"])

    def test_wait_verification_artifact_skips_stale_and_excluded_tokens(self):
        account = {
            "id": 1,
            "email": "pool@outlook.com",
            "password": "x",
            "client_id": "cid",
            "refresh_token": "rt",
        }
        client = OutlookMailClient(
            lambda: {"base_url": "http://example", "api_key": "k", "api_mode": "get"},
            claim_mailbox=lambda: account,
            resolve_mailbox=lambda token: account,
        )
        with patch.object(
            client,
            "fetch_candidate_messages",
            return_value=[
                {
                    "subject": "Old",
                    "text": "https://www.oreateai.com/passport/confirm?tokenID=old-token",
                    "receivedDateTime": "2020-01-01T00:00:00Z",
                },
                {
                    "subject": "Excluded",
                    "text": "https://www.oreateai.com/passport/confirm?tokenID=skip-me",
                    "receivedDateTime": "2099-01-01T00:00:00Z",
                },
                {
                    "subject": "Fresh",
                    "text": "https://www.oreateai.com/passport/confirm?tokenID=fresh-token",
                    "receivedDateTime": "2099-01-01T00:00:01Z",
                },
            ],
        ):
            artifact = client.wait_verification_artifact(
                "pool@outlook.com",
                "1",
                timeout_sec=1,
                not_before=1_700_000_000,
                exclude_token_ids=["skip-me"],
            )
        self.assertIn("tokenID=fresh-token", artifact["link"])

    def test_fetch_latest_message_falls_back_to_graph(self):
        account = {
            "id": 1,
            "email": "pool@outlook.com",
            "password": "x",
            "client_id": "cid",
            "refresh_token": "rt",
        }
        client = OutlookMailClient(
            lambda: {"base_url": "http://example", "api_key": "k", "api_mode": "auto"},
            claim_mailbox=lambda: account,
            resolve_mailbox=lambda token: account,
        )
        with patch.object(client, "_fetch_via_get", side_effect=RuntimeError("账号不存在")), patch.object(
            client, "_fetch_via_msoauth2", side_effect=RuntimeError("404")
        ), patch.object(
            client,
            "_fetch_via_graph",
            return_value=[
                {
                    "id": "m1",
                    "subject": "Welcome to Oreate AI",
                    "html": "https://www.oreateai.com/home/vertical/aiImage?email=a&tokenID=tok1",
                }
            ],
        ):
            message = client.fetch_latest_message(account)
        self.assertEqual(message["id"], "m1")
        self.assertIn("tokenID=tok1", message["html"])

    def test_fetch_candidate_messages_keeps_empty_graph_without_shop_fallback(self):
        account = {
            "id": 1,
            "email": "pool@outlook.com",
            "password": "x",
            "client_id": "cid",
            "refresh_token": "rt",
        }
        client = OutlookMailClient(
            lambda: {"base_url": "http://example", "api_key": "k", "api_mode": "auto"},
            claim_mailbox=lambda: account,
            resolve_mailbox=lambda token: account,
        )
        with patch.object(client, "_fetch_via_graph", return_value=[]) as graph, patch.object(
            client, "_fetch_via_get", side_effect=RuntimeError("账号不存在")
        ) as shop:
            messages = client.fetch_candidate_messages(account)
        self.assertEqual(messages, [])
        graph.assert_called_once()
        shop.assert_not_called()

    def test_wait_timeout_classifies_empty_mailbox(self):
        account = {
            "id": 1,
            "email": "pool@outlook.com",
            "password": "x",
            "client_id": "cid",
            "refresh_token": "rt",
        }
        client = OutlookMailClient(
            lambda: {"base_url": "http://example", "api_key": "k", "api_mode": "auto"},
            claim_mailbox=lambda: account,
            resolve_mailbox=lambda token: account,
        )
        with patch.object(
            client,
            "probe_graph_mailbox",
            return_value={"ok": True, "messages": [], "message_count": 0, "oreate_candidates": 0, "error": ""},
        ), patch.object(client, "fetch_candidate_messages", return_value=[]), patch(
            "gateway.outlook_mail.time.sleep", return_value=None
        ):
            with self.assertRaises(RuntimeError) as ctx:
                client.wait_verification_artifact("pool@outlook.com", "1", timeout_sec=1)
        self.assertIn("mailbox empty", str(ctx.exception))

    def test_wait_timeout_classifies_invalid_graph_credentials(self):
        account = {
            "id": 1,
            "email": "pool@outlook.com",
            "password": "x",
            "client_id": "cid",
            "refresh_token": "rt",
        }
        client = OutlookMailClient(
            lambda: {"base_url": "http://example", "api_key": "k", "api_mode": "graph"},
            claim_mailbox=lambda: account,
            resolve_mailbox=lambda token: account,
        )
        with patch.object(
            client,
            "probe_graph_mailbox",
            return_value={
                "ok": False,
                "messages": [],
                "message_count": 0,
                "oreate_candidates": 0,
                "error": "outlook graph token failed: HTTP 400 invalid_grant",
            },
        ), patch.object(client, "fetch_candidate_messages", return_value=[]), patch(
            "gateway.outlook_mail.time.sleep", return_value=None
        ):
            with self.assertRaises(RuntimeError) as ctx:
                client.wait_verification_artifact("pool@outlook.com", "1", timeout_sec=1)
        self.assertIn("credentials invalid", str(ctx.exception))

    def test_mail_router_switches_provider(self):
        cfg = {"provider": "yyds", "base_url": "http://yyds", "api_key": "k"}
        yyds = YydsClient(lambda: cfg)
        outlook = OutlookMailClient(
            lambda: cfg,
            claim_mailbox=lambda: {
                "id": 1,
                "email": "o@outlook.com",
                "password": "x",
                "client_id": "c",
                "refresh_token": "r",
            },
            resolve_mailbox=lambda token: {},
        )
        router = MailRouter(lambda: cfg, yyds, outlook)
        self.assertIs(router.active(), yyds)
        cfg["provider"] = "outlook"
        self.assertIs(router.active(), outlook)


if __name__ == "__main__":
    unittest.main()
