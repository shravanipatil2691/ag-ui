import asyncio
import json
import uuid
import copy
from typing import AsyncGenerator, Dict, Any, List, Optional
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import jsonpatch

from ag_ui.core import (
    RunAgentInput,
    RunStartedEvent,
    RunFinishedEvent,
    RunFinishedSuccessOutcome,
    RunFinishedCancelledOutcome,
    RunErrorEvent,
    StepStartedEvent,
    StepFinishedEvent,
    ToolCallStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    StateSnapshotEvent,
    StateDeltaEvent,
    TextMessageStartEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
)
from ag_ui.encoder import EventEncoder
from tools import extract_trip_info, get_frontend_tools_declaration
from adapters import get_llm_adapter

app = FastAPI(title="AG-UI Task Copilot")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

encoder = EventEncoder()

def get_last_user_message(messages: List[Any]) -> str:
    for m in reversed(messages):
        role = getattr(m, "role", None) or (m.get("role") if isinstance(m, dict) else None)
        if role == "user":
            content = getattr(m, "content", None) or (m.get("content") if isinstance(m, dict) else "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return " ".join([p.get("text", "") if isinstance(p, dict) else getattr(p, "text", "") for p in content])
    return ""

def get_last_tool_message(messages: List[Any]) -> Optional[Dict[str, Any]]:
    for m in reversed(messages):
        role = getattr(m, "role", None) or (m.get("role") if isinstance(m, dict) else None)
        if role == "tool":
            content = getattr(m, "content", None) or (m.get("content") if isinstance(m, dict) else "")
            call_id = getattr(m, "tool_call_id", None) or (m.get("toolCallId") if isinstance(m, dict) else None)
            try:
                parsed = json.loads(content) if isinstance(content, str) else content
            except Exception:
                parsed = {"raw": content}
            return {"tool_call_id": call_id, "data": parsed}
    return None

async def stream_agent(run_input: RunAgentInput, request: Request) -> AsyncGenerator[str, None]:
    thread_id = run_input.thread_id
    run_id = run_input.run_id
    open_message_id: Optional[str] = None
    open_tool_call_id: Optional[str] = None
    open_step_name: Optional[str] = None

    try:
        adapter = get_llm_adapter()
    except Exception as exc:
        yield encoder.encode(RunErrorEvent(message=str(exc), code="CONFIG_ERROR"))
        return

    try:
        if await request.is_disconnected():
            return

        yield encoder.encode(RunStartedEvent(thread_id=thread_id, run_id=run_id, protocol_version="1.0"))

        current_state = run_input.state if isinstance(run_input.state, dict) and run_input.state else {
            "title": "Trip & Project Itinerary",
            "items": [],
            "budget": 0,
            "progress": 0
        }
        yield encoder.encode(StateSnapshotEvent(snapshot=current_state))
        yield ": keep-alive\n\n"

        last_tool = get_last_tool_message(run_input.messages)
        user_query = get_last_user_message(run_input.messages).strip()

        if last_tool:
            open_step_name = "processing_approval"
            yield encoder.encode(StepStartedEvent(step_name=open_step_name))
            await asyncio.sleep(0.05)

            approved = bool(last_tool["data"].get("approved", False))
            old_state = copy.deepcopy(current_state)
            matching_item = next((i for i in current_state.get("items", []) if i.get("type", "").lower() == "hotel" or i.get("status") in ["planned", "pending_approval"]), None)
            if not matching_item and current_state.get("items"):
                matching_item = current_state["items"][0]
            if matching_item:
                matching_item["status"] = "confirmed" if approved else "declined"

            current_state["budget"] = sum(i.get("cost", 0) for i in current_state.get("items", []) if i.get("status") != "declined")
            patch = jsonpatch.make_patch(old_state, current_state).patch
            if patch:
                yield encoder.encode(StateDeltaEvent(delta=patch))

            yield encoder.encode(StepFinishedEvent(step_name=open_step_name))
            open_step_name = None

            open_step_name = "responding"
            yield encoder.encode(StepStartedEvent(step_name=open_step_name))
            msg_id = f"msg-{uuid.uuid4().hex[:8]}"
            open_message_id = msg_id
            yield encoder.encode(TextMessageStartEvent(message_id=msg_id, role="assistant"))

            item_title = matching_item["title"] if matching_item else "reservation"
            prompt = (
                f"The user has {'approved' if approved else 'declined'} the reservation for {item_title}. "
                "Write a natural 1 to 2 sentence response confirming this update."
            )
            async for chunk in adapter.stream_text(prompt):
                if await request.is_disconnected():
                    return
                yield encoder.encode(TextMessageContentEvent(message_id=msg_id, delta=chunk))
                await asyncio.sleep(0.02)

            yield encoder.encode(TextMessageEndEvent(message_id=msg_id))
            open_message_id = None
            yield encoder.encode(StepFinishedEvent(step_name=open_step_name))
            open_step_name = None

            old_state = copy.deepcopy(current_state)
            current_state["progress"] = 100
            patch = jsonpatch.make_patch(old_state, current_state).patch
            if patch:
                yield encoder.encode(StateDeltaEvent(delta=patch))

            yield encoder.encode(RunFinishedEvent(
                thread_id=thread_id,
                run_id=run_id,
                outcome=RunFinishedSuccessOutcome()
            ))
            return

        open_step_name = "plan_and_search"
        yield encoder.encode(StepStartedEvent(step_name=open_step_name))
        await asyncio.sleep(0.05)

        trip_info = extract_trip_info(user_query, adapter)
        destination = trip_info["destination"]
        clean_title = trip_info["title"]
        places = trip_info["places"]

        tool_id = f"call-{uuid.uuid4().hex[:8]}"
        open_tool_call_id = tool_id
        yield encoder.encode(ToolCallStartEvent(tool_call_id=tool_id, tool_call_name="search_places"))

        search_args = json.dumps({"query": destination})
        for i in range(0, len(search_args), 8):
            if await request.is_disconnected():
                return
            yield encoder.encode(ToolCallArgsEvent(tool_call_id=tool_id, delta=search_args[i:i + 8]))
            await asyncio.sleep(0.02)

        yield encoder.encode(ToolCallEndEvent(tool_call_id=tool_id))
        open_tool_call_id = None

        res_msg_id = f"msg-{uuid.uuid4().hex[:8]}"
        yield encoder.encode(ToolCallResultEvent(
            message_id=res_msg_id,
            tool_call_id=tool_id,
            content=json.dumps(places)
        ))

        old_state = copy.deepcopy(current_state)
        new_items = []
        for p in places:
            new_items.append({
                "id": f"item-{uuid.uuid4().hex[:6]}",
                "title": p["name"],
                "type": p["type"],
                "cost": p["cost"],
                "rating": p.get("rating", 4.5),
                "day": 1 if p.get("type", "").lower() == "hotel" else int(p.get("day", 1)),
                "status": "planned"
            })
        current_state["title"] = clean_title
        current_state["items"] = new_items
        current_state["budget"] = sum(i["cost"] for i in new_items)
        current_state["progress"] = 50

        patch = jsonpatch.make_patch(old_state, current_state).patch
        if patch:
            yield encoder.encode(StateDeltaEvent(delta=patch))

        yield encoder.encode(StepFinishedEvent(step_name=open_step_name))
        open_step_name = None

        needs_approval = any(w in user_query.lower() for w in ["book", "confirm", "reserve", "hotel"]) or any(i.get("type", "").lower() == "hotel" or i.get("cost", 0) > 100 for i in new_items)

        if needs_approval:
            open_step_name = "requesting_approval"
            yield encoder.encode(StepStartedEvent(step_name=open_step_name))

            approval_id = f"call-{uuid.uuid4().hex[:8]}"
            open_tool_call_id = approval_id
            yield encoder.encode(ToolCallStartEvent(tool_call_id=approval_id, tool_call_name="request_approval"))

            target_item = next((i for i in new_items if i.get("type", "").lower() == "hotel" or i.get("cost", 0) > 50), new_items[0])
            approval_args = json.dumps({
                "action": f"Confirm booking for {target_item['title']}",
                "details": f"Reserve booking with estimated charge of ${target_item['cost']} within total budget ${current_state['budget']}.",
                "cost": target_item["cost"]
            })

            for i in range(0, len(approval_args), 10):
                if await request.is_disconnected():
                    return
                yield encoder.encode(ToolCallArgsEvent(tool_call_id=approval_id, delta=approval_args[i:i + 10]))
                await asyncio.sleep(0.02)

            yield encoder.encode(ToolCallEndEvent(tool_call_id=approval_id))
            open_tool_call_id = None

            old_state = copy.deepcopy(current_state)
            patch = jsonpatch.make_patch(old_state, current_state).patch
            if patch:
                yield encoder.encode(StateDeltaEvent(delta=patch))

            yield encoder.encode(StepFinishedEvent(step_name=open_step_name))
            open_step_name = None

            open_step_name = "communicating"
            yield encoder.encode(StepStartedEvent(step_name=open_step_name))
            msg_id = f"msg-{uuid.uuid4().hex[:8]}"
            open_message_id = msg_id
            yield encoder.encode(TextMessageStartEvent(message_id=msg_id, role="assistant"))

            prompt = (
                f"The user wants to travel to {destination}. We built the itinerary '{clean_title}'. "
                f"We selected '{target_item['title']}' (${target_item['cost']}) which requires booking approval. "
                "Write a natural, friendly 2-sentence update explaining the plan and stating that the booking is awaiting approval in the card. "
                "Do not ask the user to reply in text."
            )
            async for chunk in adapter.stream_text(prompt):
                if await request.is_disconnected():
                    return
                yield encoder.encode(TextMessageContentEvent(message_id=msg_id, delta=chunk))
                await asyncio.sleep(0.02)

            yield encoder.encode(TextMessageEndEvent(message_id=msg_id))
            open_message_id = None
            yield encoder.encode(StepFinishedEvent(step_name=open_step_name))
            open_step_name = None

            yield encoder.encode(RunFinishedEvent(
                thread_id=thread_id,
                run_id=run_id,
                outcome=RunFinishedSuccessOutcome(pending_tool_call_ids=[approval_id])
            ))
            return

        open_step_name = "synthesizing_response"
        yield encoder.encode(StepStartedEvent(step_name=open_step_name))

        msg_id = f"msg-{uuid.uuid4().hex[:8]}"
        open_message_id = msg_id
        yield encoder.encode(TextMessageStartEvent(message_id=msg_id, role="assistant"))

        places_summary = ", ".join([f"{item['title']} (${item['cost']})" for item in new_items])
        prompt = (
            f"The user requested: '{user_query}'. We created the plan '{clean_title}' featuring: {places_summary}. "
            "Write a warm, natural 2-to-3 sentence response welcoming the user and presenting their new itinerary."
        )

        async for chunk in adapter.stream_text(prompt):
            if await request.is_disconnected():
                return
            yield encoder.encode(TextMessageContentEvent(message_id=msg_id, delta=chunk))
            await asyncio.sleep(0.02)

        yield encoder.encode(TextMessageEndEvent(message_id=msg_id))
        open_message_id = None

        old_state = copy.deepcopy(current_state)
        current_state["progress"] = 100
        patch = jsonpatch.make_patch(old_state, current_state).patch
        if patch:
            yield encoder.encode(StateDeltaEvent(delta=patch))

        yield encoder.encode(StepFinishedEvent(step_name=open_step_name))
        open_step_name = None

        yield encoder.encode(RunFinishedEvent(
            thread_id=thread_id,
            run_id=run_id,
            outcome=RunFinishedSuccessOutcome()
        ))

    except asyncio.CancelledError:
        if open_tool_call_id:
            yield encoder.encode(ToolCallEndEvent(tool_call_id=open_tool_call_id))
        if open_message_id:
            yield encoder.encode(TextMessageEndEvent(message_id=open_message_id))
        if open_step_name:
            yield encoder.encode(StepFinishedEvent(step_name=open_step_name))
        yield encoder.encode(RunFinishedEvent(
            thread_id=thread_id,
            run_id=run_id,
            outcome=RunFinishedCancelledOutcome()
        ))
    except Exception as exc:
        if open_tool_call_id:
            yield encoder.encode(ToolCallEndEvent(tool_call_id=open_tool_call_id))
        if open_message_id:
            yield encoder.encode(TextMessageEndEvent(message_id=open_message_id))
        if open_step_name:
            yield encoder.encode(StepFinishedEvent(step_name=open_step_name))
        yield encoder.encode(RunErrorEvent(message=str(exc), code="RUN_EXECUTION_ERROR"))

@app.post("/agent")
@app.post("/agent/")
async def run_agent_endpoint(request: Request):
    try:
        body = await request.json()
        run_input = RunAgentInput.model_validate(body)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid RunAgentInput: {exc}")

    return StreamingResponse(
        stream_agent(run_input, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )

@app.get("/agent")
@app.get("/agent/")
async def get_agent_info():
    return {"status": "ok", "message": "AG-UI agent endpoint ready. Send POST with RunAgentInput."}

@app.get("/tools")
async def get_tools_endpoint():
    return get_frontend_tools_declaration()

@app.get("/")
async def root_index():
    return FileResponse("index.html")