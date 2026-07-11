# Build from repo root: docker build -f services/agent_care_gap/Dockerfile -t care-ops-agent_care_gap .
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1
COPY requirements.txt requirements.txt
COPY services/agent_care_gap/requirements.txt services/agent_care_gap/requirements.txt
RUN pip install --no-cache-dir -r services/agent_care_gap/requirements.txt
COPY shared/ shared/
COPY services/agent_care_gap/ services/agent_care_gap/
EXPOSE 8000
CMD ["uvicorn", "services.agent_care_gap.app:app", "--host", "0.0.0.0", "--port", "8000"]
