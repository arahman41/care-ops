# Build from repo root: docker build -f services/agent_prior_auth/Dockerfile -t care-ops-agent_prior_auth .
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1
COPY requirements.txt requirements.txt
COPY services/agent_prior_auth/requirements.txt services/agent_prior_auth/requirements.txt
RUN pip install --no-cache-dir -r services/agent_prior_auth/requirements.txt
COPY shared/ shared/
COPY services/agent_prior_auth/ services/agent_prior_auth/
EXPOSE 8000
CMD ["uvicorn", "services.agent_prior_auth.app:app", "--host", "0.0.0.0", "--port", "8000"]
