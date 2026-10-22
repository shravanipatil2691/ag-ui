import json
import pytest
from httpx import AsyncClient, ASGITransport
from server import app

@pytest.mark.asyncio
async def test_agent_endpoint_event_sequencing():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "threadId": "thr-test-1",
            "runId": "run-test-1",
            "protocolVersion": "1.0",
            "messages": [
                {"id": "msg-u-1", "role": "user", "content": "Plan a short visit to Kolhapur"}
            ],
            "tools": [
                {
                    "name": "request_approval",
                    "description": "Request human approval",
                    "parameters": {}
                }
            ],
            "state": {
                "title": "Initial Plan",
                "items": [],
                "budget": 500,
                "progress": 0
            }
        }
        res = await client.post("/agent", json=payload)
        assert res.status_code == 200
        assert "text/event-stream" in res.headers["content-type"]

        events = []
        for line in res.text.split("\n"):
            line = line.strip()
            if line.startswith("data:"):
                raw_data = line[5:].strip()
                if raw_data:
                    events.append(json.loads(raw_data))

        assert len(events) > 0
        assert events[0]["type"] == "RUN_STARTED"
        assert events[0]["threadId"] == "thr-test-1"
        assert events[0]["runId"] == "run-test-1"
        assert events[0]["protocolVersion"] == "1.0"

        types = [e["type"] for e in events]
        assert "STATE_SNAPSHOT" in types
        assert types.index("STATE_SNAPSHOT") < (types.index("STATE_DELTA") if "STATE_DELTA" in types else len(types))

        open_steps = []
        open_tools = []
        open_messages = []

        for e in events:
            t = e["type"]
            if t == "STEP_STARTED":
                open_steps.append(e["stepName"])
            elif t == "STEP_FINISHED":
                assert open_steps, "Unexpected STEP_FINISHED without open step"
                assert e["stepName"] == open_steps[-1]
                open_steps.pop()
            elif t == "TOOL_CALL_START":
                open_tools.append(e["toolCallId"])
            elif t == "TOOL_CALL_END":
                assert open_tools, "Unexpected TOOL_CALL_END without open call"
                assert e["toolCallId"] == open_tools[-1]
                open_tools.pop()
            elif t == "TEXT_MESSAGE_START":
                open_messages.append(e["messageId"])
            elif t == "TEXT_MESSAGE_END":
                assert open_messages, "Unexpected TEXT_MESSAGE_END without open message"
                assert e["messageId"] == open_messages[-1]
                open_messages.pop()

        assert not open_steps, f"Unclosed steps: {open_steps}"
        assert not open_tools, f"Unclosed tools: {open_tools}"
        assert not open_messages, f"Unclosed messages: {open_messages}"

        assert events[-1]["type"] in ("RUN_FINISHED", "RUN_ERROR")

@pytest.mark.asyncio
async def test_agent_approval_resume_flow():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "threadId": "thr-test-2",
            "runId": "run-test-2",
            "protocolVersion": "1.0",
            "messages": [
                {"id": "msg-u-1", "role": "user", "content": "Book a hotel in Paris"},
                {"id": "msg-a-1", "role": "assistant", "content": "Please approve the hotel booking."},
                {"id": "msg-t-1", "role": "tool", "toolCallId": "call-approval-1", "content": json.dumps({"approved": True})}
            ],
            "state": {
                "title": "Paris Plan",
                "items": [{"id": "item-1", "title": "Boutique Hotel Le Marais", "cost": 220, "status": "pending_approval"}],
                "budget": 1000,
                "progress": 50
            }
        }
        res = await client.post("/agent", json=payload)
        assert res.status_code == 200

        events = []
        for line in res.text.split("\n"):
            line = line.strip()
            if line.startswith("data:"):
                raw_data = line[5:].strip()
                if raw_data:
                    events.append(json.loads(raw_data))

        assert events[0]["type"] == "RUN_STARTED"
        assert events[-1]["type"] == "RUN_FINISHED"

        deltas = [e for e in events if e["type"] == "STATE_DELTA"]
        assert len(deltas) > 0
        patch_ops = [op["op"] for d in deltas for op in d["delta"]]
        assert "replace" in patch_ops
