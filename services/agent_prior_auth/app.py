from fastapi import FastAPI
from shared.schemas import AgentInput, PriorAuthOutput
from services.agent_prior_auth.agent import run

app = FastAPI(title="Care Ops Copilot - Prior Auth Agent")

@app.get("/health")
def health():
    return {"status": "ok", "service": "agent_prior_auth"}

@app.post("/run", response_model=PriorAuthOutput)
def run_endpoint(inp: AgentInput):
    return run(inp)
