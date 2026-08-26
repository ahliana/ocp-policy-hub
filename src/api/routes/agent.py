"""Agent endpoints - REST and WebSocket for the standalone policy agent."""

import os
import logging
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from ...agent.orchestrator import PolicyAgent
from ...core.models import DEFAULT_ANALYSIS_MODEL
from ..deps import is_loopback_client

# WebSocket close code for an admin-gate rejection (RFC 6455 policy
# violation) - distinguishes a gate rejection from an unrelated close
# (e.g. missing ANTHROPIC_API_KEY) for any client inspecting the code.
_POLICY_VIOLATION = 1008

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["agent"])


class AgentRequest(BaseModel):
    message: str
    model: str = DEFAULT_ANALYSIS_MODEL


class AgentResponse(BaseModel):
    response: str
    iterations: int = 0
    tools_called: list[str] = []
    error: Optional[str] = None


@router.post("/agent/run")
async def run_agent(request: AgentRequest) -> AgentResponse:
    """Run the policy agent with a natural language instruction.

    The agent will use tools to fulfill the request and return
    a plain-language response.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return AgentResponse(
            response="",
            error="ANTHROPIC_API_KEY environment variable is not set",
        )

    agent = PolicyAgent(
        api_key=api_key,
        model=request.model,
        data_dir=os.environ.get("OCP_DATA_DIR", "data"),
    )

    tools_called: list[str] = []
    iterations = 0

    def on_tool_call(name: str, input_data: dict):
        nonlocal iterations
        tools_called.append(name)
        iterations += 1

    try:
        response_text = await agent.run(
            request.message,
            on_tool_call=on_tool_call,
        )
        return AgentResponse(
            response=response_text,
            iterations=iterations,
            tools_called=tools_called,
        )
    except Exception as e:
        logger.error(f"Agent error: {e}")
        return AgentResponse(
            response="",
            error=str(e),
        )
    finally:
        await agent.close()


@router.websocket("/agent/ws")
async def agent_websocket(ws: WebSocket):
    """WebSocket endpoint for streaming agent interactions.

    Send: {"message": "Find policies in Germany"}
    Receive: {"type": "text", "content": "..."}
    Receive: {"type": "tool_call", "name": "list_domains", "input": {...}}
    Receive: {"type": "tool_result", "name": "list_domains", "result": {...}}
    Receive: {"type": "complete", "response": "..."}
    """
    await ws.accept()

    # Admin gate: websockets bypass AdminGateMiddleware, so the chat
    # (which spends API budget) checks admin status itself here - the same
    # admin/non-admin line request_is_admin (src/api/deps.py) uses for HTTP
    # requests. ADMIN_TOKEN set: only a matching ?token= query param counts
    # (browsers can't easily set custom headers on a WebSocket upgrade, so
    # this stays a query param rather than switching to request_is_admin's
    # X-Admin-Token header). ADMIN_TOKEN unset: loopback-only, via the same
    # is_loopback_client fallback request_is_admin uses - previously this
    # branch didn't exist at all (`if admin_token:` skipped straight past
    # the whole check), so an unset token left this endpoint fully open to
    # any remote caller.
    admin_token = os.environ.get("ADMIN_TOKEN")
    if admin_token:
        import hmac
        provided = ws.query_params.get("token", "")
        if not hmac.compare_digest(provided, admin_token):
            await ws.send_json(
                {"type": "error", "content": "Administrator token required"}
            )
            await ws.close(code=_POLICY_VIOLATION)
            return
    elif not is_loopback_client(ws):
        await ws.send_json(
            {
                "type": "error",
                "content": (
                    "This server has no ADMIN_TOKEN configured, so admin "
                    "actions are restricted to local requests."
                ),
            }
        )
        await ws.close(code=_POLICY_VIOLATION)
        return

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        await ws.send_json({"type": "error", "content": "ANTHROPIC_API_KEY not set"})
        await ws.close()
        return

    agent: PolicyAgent | None = None

    try:
        while True:
            data = await ws.receive_json()
            message = data.get("message", "")
            model = data.get("model", DEFAULT_ANALYSIS_MODEL)

            if not message:
                await ws.send_json({"type": "error", "content": "No message provided"})
                continue

            if agent is None:
                agent = PolicyAgent(
                    api_key=api_key,
                    model=model,
                    data_dir=os.environ.get("OCP_DATA_DIR", "data"),
                )

            async def on_text(text: str):
                await ws.send_json({"type": "text", "content": text})

            async def on_tool_call(name: str, input_data: dict):
                await ws.send_json({
                    "type": "tool_call",
                    "name": name,
                    "input": input_data,
                })

            async def on_tool_result(name: str, result: dict):
                await ws.send_json({
                    "type": "tool_result",
                    "name": name,
                    "result": result,
                })

            try:
                response = await agent.run(
                    message,
                    on_text=on_text,
                    on_tool_call=on_tool_call,
                    on_tool_result=on_tool_result,
                )
                await ws.send_json({"type": "complete", "response": response})
            except Exception as e:
                await ws.send_json({"type": "error", "content": str(e)})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"Agent WebSocket error: {e}")
    finally:
        if agent is not None:
            await agent.close()
