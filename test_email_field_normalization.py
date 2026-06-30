#!/usr/bin/env python3
"""
Test email field name normalization.
Verifies that the send_email tool accepts various field name aliases.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Add the project root to sys.path
sys.path.insert(0, str(Path(__file__).parent))

from core.agent import VeraAgent
from tools.email_tool import SendEmailTool


def test_agent_normalizes_recipient_field():
    """Test that agent normalizes 'recipient' → 'to'"""
    agent = VeraAgent(llm=None, tools=[], memory=MagicMock())
    
    # Simulate LLM generating "recipient" instead of "to"
    tool_input = {
        "recipient": "test@example.com",
        "subject": "Test Subject",
        "body": "Test Body"
    }
    
    result = agent._prepare_tool_input("send_email", tool_input)
    parsed = json.loads(result)
    
    assert parsed["to"] == "test@example.com", f"Expected 'to' field, got: {parsed}"
    assert parsed["subject"] == "Test Subject"
    assert parsed["body"] == "Test Body"
    print("✅ Agent normalizes 'recipient' → 'to'")


def test_agent_normalizes_message_field():
    """Test that agent normalizes 'message' → 'body'"""
    agent = VeraAgent(llm=None, tools=[], memory=MagicMock())
    
    tool_input = {
        "to": "test@example.com",
        "subject": "Test Subject",
        "message": "Test Message Body"
    }
    
    result = agent._prepare_tool_input("send_email", tool_input)
    parsed = json.loads(result)
    
    assert parsed["body"] == "Test Message Body"
    print("✅ Agent normalizes 'message' → 'body'")


def test_agent_normalizes_from_field():
    """Test that agent normalizes 'from' → 'account'"""
    agent = VeraAgent(llm=None, tools=[], memory=MagicMock())
    
    tool_input = {
        "to": "test@example.com",
        "subject": "Test Subject",
        "body": "Test Body",
        "from": "gmail"
    }
    
    result = agent._prepare_tool_input("send_email", tool_input)
    parsed = json.loads(result)
    
    assert parsed.get("account") == "gmail"
    print("✅ Agent normalizes 'from' → 'account'")


def test_agent_normalizes_recipient_address_field():
    """Test that agent normalizes 'recipient_address' → 'to'"""
    agent = VeraAgent(llm=None, tools=[], memory=MagicMock())
    
    tool_input = {
        "recipient_address": "user@company.com",
        "subject": "Meeting Notes",
        "message_body": "Here are the notes"
    }
    
    result = agent._prepare_tool_input("send_email", tool_input)
    parsed = json.loads(result)
    
    assert parsed["to"] == "user@company.com"
    assert parsed["body"] == "Here are the notes"
    print("✅ Agent normalizes 'recipient_address' → 'to' and 'message_body' → 'body'")


def test_agent_normalizes_json_string_input():
    """Test normalization works with JSON string input"""
    agent = VeraAgent(llm=None, tools=[], memory=MagicMock())
    
    tool_input = json.dumps({
        "recipient": "another@test.com",
        "subject": "JSON Test",
        "message": "Testing JSON string"
    })
    
    result = agent._prepare_tool_input("send_email", tool_input)
    parsed = json.loads(result)
    
    assert parsed["to"] == "another@test.com"
    assert parsed["body"] == "Testing JSON string"
    print("✅ Agent normalizes JSON string input")


def test_send_email_tool_normalizes_fields():
    """Test that SendEmailTool also normalizes fields"""
    tool = SendEmailTool()
    
    # This would normally be async, but we can test the _normalize_field method
    params = {
        "recipient_address": "direct@example.com",
        "subject": "Direct Test",
        "message_body": "Direct message"
    }
    
    to = tool._normalize_field(params, ["to", "recipient", "recipient_address"])
    body = tool._normalize_field(params, ["body", "message", "message_body"])
    
    assert to == "direct@example.com"
    assert body == "Direct message"
    print("✅ SendEmailTool._normalize_field works correctly")


def test_preserves_priority_order():
    """Test that preferred field names take priority"""
    agent = VeraAgent(llm=None, tools=[], memory=MagicMock())
    
    # When both "to" and "recipient" are present, "to" should win
    tool_input = {
        "to": "preferred@example.com",
        "recipient": "fallback@example.com",
        "subject": "Priority Test",
        "body": "Test"
    }
    
    result = agent._prepare_tool_input("send_email", tool_input)
    parsed = json.loads(result)
    
    assert parsed["to"] == "preferred@example.com"
    print("✅ Correct field name takes priority")


def main():
    """Run all tests"""
    print("Testing email field name normalization...\n")
    
    tests = [
        test_agent_normalizes_recipient_field,
        test_agent_normalizes_message_field,
        test_agent_normalizes_from_field,
        test_agent_normalizes_recipient_address_field,
        test_agent_normalizes_json_string_input,
        test_send_email_tool_normalizes_fields,
        test_preserves_priority_order,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"❌ {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"❌ {test.__name__}: {type(e).__name__}: {e}")
            failed += 1
    
    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed")
    print(f"{'='*50}")
    
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
