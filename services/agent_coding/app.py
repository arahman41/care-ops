from fastapi import FastAPI, HTTPException
from shared.schemas import AgentInput, CodingOutput
from services.agent_coding.agent import CodingError, run

app = FastAPI(title="Care Ops Copilot - Coding Agent")


@app.get("/health")
def health():
    return {"status": "ok", "service": "agent_coding"}


@app.post("/run", response_model=CodingOutput)
def run_endpoint(inp: AgentInput):
    try:
        return run(inp)
    except CodingError as exc:
        # 502: the model or the pipeline broke, not this service.
        raise HTTPException(502, str(exc)) from exc
