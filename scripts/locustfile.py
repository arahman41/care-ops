"""Locust load test for the intake endpoint. Captures p95 and req/s.

P4-3: the target intake service must be running with FAKE_STRUCTURING=true
(see shared/config.py), or every simulated request makes a real, paid Claude
call and the measured latency is dominated by vendor response-time variance
rather than this service's own concurrency handling. scripts/run_load_test.py
starts the service that way and drives this file; do not point this at a
normally-configured intake service.

Run directly (service already up in fake-structuring mode):
    locust -f scripts/locustfile.py --headless -u 20 -r 5 -t 1m \
        --host http://localhost:8000

Prefer `make load-test`, which runs scripts/run_load_test.py: it starts the
service itself, runs this file, captures the results to a committed
artifact, and cleans up the rows this generates.
"""
from locust import HttpUser, task, between

SAMPLE = ("Doctor: what brings you in today. Patient: I have had a cough "
          "and mild fever for three days. Doctor: any chest pain. Patient: no.")

# Tags every row this load test writes, so cleanup can find them by name
# rather than by guessing which encounters are load-test noise. See
# scripts/run_load_test.py::_cleanup.
EXTERNAL_REF = "p4-3-load-test"


class IntakeUser(HttpUser):
    wait_time = between(0.5, 2.0)

    @task
    def intake(self):
        self.client.post("/intake", json={"transcript": SAMPLE,
                                          "external_ref": EXTERNAL_REF})
