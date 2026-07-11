# Build from repo root: docker build -f services/orchestrator/Dockerfile -t care-ops-orchestrator .
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1
COPY requirements.txt requirements.txt
COPY services/orchestrator/requirements.txt services/orchestrator/requirements.txt
RUN pip install --no-cache-dir -r services/orchestrator/requirements.txt
COPY shared/ shared/
COPY services/orchestrator/ services/orchestrator/
EXPOSE 8000
CMD ["uvicorn", "services.orchestrator.app:app", "--host", "0.0.0.0", "--port", "8000"]
