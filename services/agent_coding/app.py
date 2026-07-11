from fastapi import FastAPI
from shared.schemas import AgentInput, CodingOutput
from services.agent_coding.agent import run

app = FastAPI(title="Care Ops Copilot - Coding Agent")

@app.get("/health")
def health():
    return {"status": "ok", "service": "agent_coding"}

@app.post("/run", response_model=CodingOutput)
def run_endpoint(inp: AgentInput):
    return run(inp)
