# Demo script (P4-4)

A walkthrough script for recording the launch demo, mirroring the ClinAIQA
launch pattern: a short screen recording that proves the system works end
to end, cites only measured numbers, and shows the honest edge cases rather
than hiding them.

This file is the script. Recording the actual video is a manual step:
narration and screen capture are not something this session can produce,
only plan.

## Before recording

```bash
docker compose up --build       # Postgres and all services
make cluster-up                 # kind cluster, kubectl apply -f k8s/
cd dashboard && npm install && npm run dev
```

Confirm live, on screen, before recording starts:

```bash
kubectl get pods -n care-ops    # 6/6 Running
```

## Scene 1: the problem (10-15s, talking head or slide)

One sentence, from `docs/PRD-CareOpsCopilot-MVP.md` section 3: documentation
burden plus downstream administrative friction, and no existing system
chains ambient documentation into agent workflows while keeping every AI
decision auditable and drift-monitored.

## Scene 2: intake, live (30-45s)

Submit a PriMock57 transcript (or audio file) to the intake endpoint on
screen. Show the returned structured SOAP JSON. Say what the F1 headline
number is (0.869 on ACI-Bench n=120) and point at `make eval-structuring-replay`
as the one command that reproduces it from a committed artifact, zero API
calls.

## Scene 3: the three agents, live in the cluster (30-45s)

`kubectl get pods -n care-ops` on screen (6/6 Running). POST the structured
note to the orchestrator's `/run`. Show all three artifacts coming back:
prior-auth, care-gap, coding, each a structured object with a confidence
score, never free text.

Then the isolation proof, live: `kubectl scale deployment/agent-coding
--replicas=0`, re-run the same request, show prior-auth and care-gap still
answering and `errors.coding` naming the exact failed URL. Scale back to 1,
show it recover. This is the single most convincing 20 seconds of the demo:
it proves the failure-isolation claim on screen instead of asserting it.

## Scene 4: the coding decision, told honestly (20-30s)

Say the sentence this project keeps insisting on: no held-out set carries
gold billing codes, so this is a verified rate, not coding accuracy. Show
the routing table (Sonnet 5 at xhigh: 96.65 verified, $4.01; Opus 4.8 at
high: 97.35 verified, $3.16) and say the decision was cost, since the
quality delta's confidence interval straddles zero. This is the moment that
distinguishes a portfolio piece with real judgment from one that just
reports a number.

## Scene 5: the dashboard (45-60s)

Open the dashboard. Walk through, in order:
1. Model inventory table, real registry rows.
2. Accuracy trend chart, two ACI-Bench windows as one real line, PriMock57's
   single point standing alone rather than fabricating a connection across
   datasets. Say why: different held-out sets, not two times of one thing.
3. Active drift alerts: currently empty, and say why that is correct, not a
   bug: the real delta between the two windows reads NOT_ATTRIBUTABLE, not
   NO_DRIFT, because the generation config differs and the measuring
   instrument itself moved between windows. A dashboard that hid this and
   showed a clean pass would be worse than no dashboard.
4. Transparency report: scroll to the coding card, show the same honest
   verified-rate language rendered live from the registry, not typed into
   the frontend.

## Scene 6: the numbers that back it (15-20s, on-screen text or slide)

430 tests passing, 96% coverage, 6 Kubernetes services, p95 load latency
240ms at 20 concurrent users (structuring stubbed for cost, said on screen).
Every number links to the script that produces it in `README.md`.

## Close (10s)

One line: built end to end, phase by phase, with evidence for every
merged pull request, nothing on this page unbacked. Link the repo.

## After recording

Publish alongside the README, matching the ClinAIQA launch pattern. Update
`README.md`'s status line with the video link once it exists.
