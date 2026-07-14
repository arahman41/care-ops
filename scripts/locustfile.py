"""Locust load test for the intake endpoint. Captures p95 and req/s.

Run: locust -f scripts/load_test.py --headless -u 20 -r 5 -t 1m \
     --host http://localhost:8000
"""
from locust import HttpUser, task, between

SAMPLE = ("Doctor: what brings you in today. Patient: I have had a cough "
          "and mild fever for three days. Doctor: any chest pain. Patient: no.")


class IntakeUser(HttpUser):
    wait_time = between(0.5, 2.0)

    @task
    def intake(self):
        self.client.post("/intake", json={"transcript": SAMPLE})
