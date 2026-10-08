# Run: D:\PkgDev\_sys\data\temp\a2a_venv\Scripts\python.exe -B echo_server.py
# A2A 1.0 JSON-RPC endpoint: http://127.0.0.1:18765/
# Clients must send the HTTP header A2A-Version: 1.0.
# Methods: SendMessage, GetTask and CancelTask; message.role is ROLE_USER.
import sys

sys.dont_write_bytecode = True

import json
from contextlib import asynccontextmanager

import uvicorn
from google.protobuf.json_format import MessageToDict
from starlette.applications import Starlette

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Part,
    Task,
    TaskState,
    TaskStatus,
)


class EchoExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue):
        message = context.message
        if message is None:
            raise ValueError("A message is required")

        contents = []
        hold = False
        for part in message.parts:
            if part.HasField("text"):
                contents.append(part.text)
                hold = hold or part.text == "hold"
            elif part.HasField("data"):
                data = MessageToDict(part.data)
                input_parameters = data.get("input", {})
                if isinstance(input_parameters, dict):
                    hold = hold or input_parameters.get("text") == "hold"
                contents.append(
                    json.dumps(
                        data,
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )

        task = Task(
            id=context.task_id,
            context_id=context.context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
            history=[message],
        )
        await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()
        if hold:
            return
        await updater.add_artifact(
            [Part(text="echo: " + "\n".join(contents))],
            name="echo",
            last_chunk=True,
        )
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        await TaskUpdater(
            event_queue, context.task_id, context.context_id
        ).cancel()


card = AgentCard(
    name="Echo",
    description="Echoes messages; holds input text 'hold' working until canceled.",
    version="1.0.0",
    supported_interfaces=[
        AgentInterface(
            url="http://127.0.0.1:18765/",
            protocol_binding="JSONRPC",
            protocol_version="1.0",
        )
    ],
    capabilities=AgentCapabilities(streaming=True),
    default_input_modes=["text/plain", "application/json"],
    default_output_modes=["text/plain"],
    skills=[
        AgentSkill(
            id="echo",
            name="Echo",
            description="Echo text or JSON data.",
            tags=["echo"],
        )
    ],
)

handler = DefaultRequestHandler(
    agent_executor=EchoExecutor(),
    task_store=InMemoryTaskStore(),
    agent_card=card,
)


@asynccontextmanager
async def lifespan(app):
    try:
        yield
    finally:
        await handler.aclose()


app = Starlette(
    routes=create_jsonrpc_routes(handler, rpc_url="/")
    + create_agent_card_routes(card),
    lifespan=lifespan,
)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=18765)
