import os
import sys
import types
import unittest
from unittest.mock import MagicMock


ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS_DIR = os.path.join(ROOT_DIR, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

# Allow tests to run even when optional runtime dependency is not installed.
try:
    import websockets.sync.client  # type: ignore  # noqa: F401
except ModuleNotFoundError:
    fake_websockets = types.ModuleType("websockets")
    fake_sync = types.ModuleType("websockets.sync")
    fake_client = types.ModuleType("websockets.sync.client")
    fake_client.connect = lambda *args, **kwargs: None
    fake_sync.client = fake_client
    fake_websockets.sync = fake_sync
    sys.modules["websockets"] = fake_websockets
    sys.modules["websockets.sync"] = fake_sync
    sys.modules["websockets.sync.client"] = fake_client

from cdp_publish import CDPError, XiaohongshuPublisher  # noqa: E402


class ReplyToCommentFlowTests(unittest.TestCase):
    def _new_publisher(self) -> XiaohongshuPublisher:
        publisher = XiaohongshuPublisher(timing_jitter=0.0)
        publisher.ws = object()
        return publisher

    def _stub_common_navigation(self, publisher: XiaohongshuPublisher) -> None:
        publisher._navigate = MagicMock()
        publisher._sleep = MagicMock()
        publisher._schedule_click_notification_mentions_tab = MagicMock(return_value="评论和@")
        publisher._check_feed_page_accessible = MagicMock()
        publisher._verify_reply_visible_in_feed_page = MagicMock(
            return_value={"ok": True, "reason": "reply_found_in_feed_dom"}
        )

    def test_direct_reply_uses_original_content_without_marker(self) -> None:
        publisher = self._new_publisher()
        self._stub_common_navigation(publisher)

        captured: dict[str, str] = {}

        def _fake_direct_reply(**kwargs):
            captured.update(kwargs)
            return {
                "success": True,
                "mode": "notification_direct_reply",
                "content_length": len(kwargs["reply_content"]),
            }

        publisher._reply_directly_in_notification = MagicMock(side_effect=_fake_direct_reply)
        publisher.get_feed_detail = MagicMock(
            return_value={"comments": [{"content": "收到～感谢补充，我去试试！"}]}
        )

        result = publisher.reply_to_comment_in_feed(
            feed_id="67abc1234def567890123456",
            xsec_token="token123",
            anchor_comment_id="67aaa111bbb222ccc333ddd",
            content="收到～感谢补充，我去试试！",
            target_comment_content="目标评论原文",
        )

        self.assertEqual(captured["reply_content"], "收到～感谢补充，我去试试！")
        self.assertNotIn("__xhsv", captured["reply_content"])
        self.assertEqual(captured["anchor_comment_id"], "67aaa111bbb222ccc333ddd")
        self.assertEqual(result["delivery_check"], "feed_detail_api")

    def test_target_comment_content_can_be_empty_when_anchor_exists(self) -> None:
        publisher = self._new_publisher()
        self._stub_common_navigation(publisher)

        publisher._reply_directly_in_notification = MagicMock(
            return_value={
                "success": True,
                "mode": "notification_direct_reply",
                "content_length": 4,
            }
        )
        publisher.get_feed_detail = MagicMock(return_value={})

        result = publisher.reply_to_comment_in_feed(
            feed_id="67abc1234def567890123456",
            xsec_token="token123",
            anchor_comment_id="67aaa111bbb222ccc333ddd",
            content="收到啦",
            target_comment_content="",
        )

        self.assertEqual(result["delivery_check"], "feed_page_dom")
        call_kwargs = publisher._reply_directly_in_notification.call_args.kwargs
        self.assertEqual(call_kwargs["target_comment_content"], "")
        self.assertEqual(call_kwargs["anchor_comment_id"], "67aaa111bbb222ccc333ddd")

    def test_fallback_path_uses_clean_content(self) -> None:
        publisher = self._new_publisher()
        self._stub_common_navigation(publisher)

        publisher._reply_directly_in_notification = MagicMock(
            side_effect=CDPError("notification_reply_failed")
        )
        publisher._focus_reply_target_for_anchor = MagicMock(
            return_value={"ok": True, "target_preview": "目标评论"}
        )
        publisher._fill_comment_content = MagicMock(return_value=3)
        publisher._submit_reply_in_current_context = MagicMock()
        publisher.get_feed_detail = MagicMock(return_value={})

        result = publisher.reply_to_comment_in_feed(
            feed_id="67abc1234def567890123456",
            xsec_token="token123",
            anchor_comment_id="67aaa111bbb222ccc333ddd",
            content="谢谢",
            target_comment_content="目标评论原文",
        )

        publisher._fill_comment_content.assert_called_once_with("谢谢")
        self.assertEqual(result["mode"], "feed_anchor_fallback")
        self.assertEqual(result["fallback_from"], "notification_direct_reply")
        self.assertEqual(result["delivery_check"], "feed_page_dom")

    def test_direct_reply_requires_target_or_anchor(self) -> None:
        publisher = self._new_publisher()
        with self.assertRaises(CDPError):
            publisher._reply_directly_in_notification(
                target_comment_content="",
                anchor_comment_id="",
                reply_content="收到",
            )

    def test_unverified_direct_reply_retries_feed_fallback(self) -> None:
        publisher = self._new_publisher()
        self._stub_common_navigation(publisher)

        publisher._reply_directly_in_notification = MagicMock(
            return_value={
                "success": True,
                "mode": "notification_direct_reply",
                "api_success": None,
                "api_probe_reason": "network_event_not_found",
            }
        )
        publisher._reply_via_feed_anchor_fallback = MagicMock(
            return_value={
                "success": True,
                "mode": "feed_anchor_fallback",
                "api_success": True,
                "api_probe_reason": "network_success_true",
            }
        )
        publisher._verify_reply_delivery = MagicMock(
            side_effect=[
                {
                    "delivery_verified": False,
                    "delivery_check": "feed_page_dom",
                    "delivery_probe_reason": "reply_not_found_in_feed_dom",
                    "dom_reason": "reply_not_found_in_feed_dom",
                },
                {
                    "delivery_verified": True,
                    "delivery_check": "comment_post_api",
                    "delivery_probe_reason": "network_success_true",
                    "dom_reason": "not_checked",
                },
            ]
        )

        result = publisher.reply_to_comment_in_feed(
            feed_id="67abc1234def567890123456",
            xsec_token="token123",
            anchor_comment_id="67aaa111bbb222ccc333ddd",
            content="🙂",
            target_comment_content="目标评论原文",
        )

        self.assertEqual(result["mode"], "feed_anchor_fallback")
        self.assertTrue(result["delivery_verified"])
        fallback_kwargs = publisher._reply_via_feed_anchor_fallback.call_args.kwargs
        self.assertEqual(fallback_kwargs["fallback_from"], "notification_direct_unverified")
        self.assertEqual(fallback_kwargs["fallback_reason"], "reply_not_found_in_feed_dom")


if __name__ == "__main__":
    unittest.main()
