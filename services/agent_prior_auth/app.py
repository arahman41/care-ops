from fastapi import FastAPI, HTTPException

from services.agent_prior_auth.agent import PriorAuthError, run
from shared.schemas import AgentInput, PriorAuthOutput

app = FastAPI(title="Care Ops Copilot - Prior Auth Agent")


@app.get("/health")
def health():
    return {"status": "ok", "service": "agent_prior_auth"}


@app.post("/run", response_model=PriorAuthOutput)
def run_endpoint(inp: AgentInput):
    try:
        return run(inp)
    except PriorAuthError as exc:
        raise HTTPException(502, str(exc)) from exc
