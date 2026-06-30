from datetime import date

import pytest

from daily_brief.generator import DailyBriefGenerator
from daily_brief.schema import ItemType


class FakeBriefLLM:
    async def chat(self, messages):
        prompt = messages[0]["content"]
        if "Summarize unread emails from the last 48 hours" in prompt:
            return """
            [
              {
                "subject": "Quarterly update",
                "from": "Jane Doe <jane@example.com>",
                "account": "work",
                "summary": "Jane shared the quarterly update and asked for review before Friday.",
                "why_it_matters": "Needs attention today because review is requested."
              }
            ]
            """
        if "Generate draft replies only for unread emails" in prompt:
            return """
            [
              {
                "uid": "1",
                "recipient_name": "Jane Doe",
                "account": "work",
                "subject": "Re: Quarterly update",
                "body": "Hi Jane, thanks for sending this. I will review it today and get back to you before Friday.",
                "reason": "Jane asked for a review before Friday."
              }
            ]
            """
        if "Generate practical tasks" in prompt:
            return "[]"
        if "Generate only high-value draft messages" in prompt:
            return "[]"
        raise AssertionError(f"Unexpected prompt: {prompt}")


class BrokenDigestLLM(FakeBriefLLM):
    async def chat(self, messages):
        prompt = messages[0]["content"]
        if "Summarize unread emails from the last 48 hours" in prompt:
            return "not-json"
        return await super().chat(messages)


class ExampleEchoDraftLLM(FakeBriefLLM):
    async def chat(self, messages):
        prompt = messages[0]["content"]
        if "Generate draft replies only for unread emails" in prompt:
            return """
            [
              {
                "uid": "123",
                "recipient_name": "Jane Doe",
                "account": "fkoine",
                "subject": "Re: Quarterly update",
                "body": "Hi Jane, thanks for sending this. I will review it today and get back to you before Friday.",
                "reason": "Jane asked for a review before Friday."
              }
            ]
            """
        return await super().chat(messages)


@pytest.mark.asyncio
async def test_daily_brief_generator_creates_email_digest_and_reply_drafts(monkeypatch):
    monkeypatch.setattr(
        "daily_brief.generator.gather_daily_context",
        lambda target_date: {
            "date": target_date.isoformat(),
            "unread_emails": [
                {
                    "uid": "1",
                    "from": "Jane Doe <jane@example.com>",
                    "subject": "Quarterly update",
                    "snippet": "Please review this before Friday.",
                    "body": "Please review this before Friday.",
                    "account": "work",
                    "received_at": "2026-04-01T08:00:00+00:00",
                },
                {
                    "uid": "2",
                    "from": "Ops <ops@example.com>",
                    "subject": "System notice",
                    "snippet": "FYI",
                    "body": "FYI",
                    "account": "gmail",
                    "received_at": "2026-04-01T07:00:00+00:00",
                },
            ],
        },
    )
    monkeypatch.setattr(
        "daily_brief.generator.load_user_preferences",
        lambda: {"user_name": "Frank", "max_daily_outputs": 6},
    )

    brief = await DailyBriefGenerator(FakeBriefLLM()).generate(date(2026, 4, 1))

    assert brief.summary.counts["unread_emails"] == 2
    assert brief.summary.counts["email_digest"] == 1
    assert brief.summary.counts["draft_messages"] == 1
    assert [item.type for item in brief.items] == [ItemType.EMAIL_DIGEST, ItemType.DRAFT_MESSAGE]
    assert brief.items[0].content["from"] == "Jane Doe <jane@example.com>"
    assert "review before Friday" in brief.items[0].content["summary"]
    assert brief.items[1].content["subject"] == "Re: Quarterly update"


@pytest.mark.asyncio
async def test_daily_brief_generator_falls_back_to_local_email_digest_summary(monkeypatch):
    monkeypatch.setattr(
        "daily_brief.generator.gather_daily_context",
        lambda target_date: {
            "date": target_date.isoformat(),
            "unread_emails": [
                {
                    "uid": "1",
                    "from": "Alex <alex@example.com>",
                    "subject": "Invoice reminder",
                    "snippet": "Please send the updated invoice today.",
                    "body": "Please send the updated invoice today.",
                    "account": "work",
                    "received_at": "2026-04-01T09:00:00+00:00",
                }
            ],
        },
    )
    monkeypatch.setattr(
        "daily_brief.generator.load_user_preferences",
        lambda: {"user_name": "Frank", "max_daily_outputs": 6},
    )

    brief = await DailyBriefGenerator(BrokenDigestLLM()).generate(date(2026, 4, 1))

    digest_items = [item for item in brief.items if item.type == ItemType.EMAIL_DIGEST]
    assert len(digest_items) == 1
    assert digest_items[0].content["subject"] == "Invoice reminder"
    assert "updated invoice today" in digest_items[0].content["summary"]


@pytest.mark.asyncio
async def test_daily_brief_generator_filters_out_example_reply_drafts_without_matching_uid(monkeypatch):
    monkeypatch.setattr(
        "daily_brief.generator.gather_daily_context",
        lambda target_date: {
            "date": target_date.isoformat(),
            "unread_emails": [
                {
                    "uid": "real-1",
                    "from": "Alex <alex@example.com>",
                    "subject": "Invoice reminder",
                    "snippet": "Please send the updated invoice today.",
                    "body": "Please send the updated invoice today.",
                    "account": "fkoine",
                    "received_at": "2026-04-01T09:00:00+00:00",
                    "reply_to": "alex@example.com",
                }
            ],
        },
    )
    monkeypatch.setattr(
        "daily_brief.generator.load_user_preferences",
        lambda: {"user_name": "Frank", "max_daily_outputs": 6},
    )

    brief = await DailyBriefGenerator(ExampleEchoDraftLLM()).generate(date(2026, 4, 1))

    draft_items = [item for item in brief.items if item.type == ItemType.DRAFT_MESSAGE]
    assert draft_items == []
