# Build from repo root: docker build -f services/intake/Dockerfile -t care-ops-intake .
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1
COPY requirements.txt requirements.txt
COPY services/intake/requirements.txt services/intake/requirements.txt
RUN pip install --no-cache-dir -r services/intake/requirements.txt
COPY shared/ shared/
COPY services/intake/ services/intake/
EXPOSE 8000
CMD ["uvicorn", "services.intake.app:app", "--host", "0.0.0.0", "--port", "8000"]
