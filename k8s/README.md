# Kubernetes (local)

These manifests target a local cluster (kind or minikube). Steps:

1. Create the cluster: `kind create cluster --name care-ops`
2. Build images and load them into kind:
   ```
   docker build -f services/intake/Dockerfile -t care-ops-intake:latest .
   kind load docker-image care-ops-intake:latest --name care-ops
   ```
   Repeat for orchestrator and each agent image.
3. Create the API key secret:
   ```
   kubectl create secret generic care-ops-secrets \
     --namespace care-ops \
     --from-literal=ANTHROPIC_API_KEY=sk-ant-xxxx
   ```
4. Apply everything: `kubectl apply -f k8s/`
5. Verify: `kubectl get pods -n care-ops` shows db, intake, orchestrator,
   and the three agent services running with passing readiness probes.
6. Load the schema: `kubectl exec -n care-ops -i deploy/postgres -- psql -U care_ops -d care_ops -f - < db/schema.sql`

## Database persistence

Postgres stores its data in a 5Gi PVC (`postgres-data`) on kind's default
`standard` StorageClass, so the database survives pod restarts and rescheduling.
Before this was added, every container restart silently destroyed the schema
and the entire audit trail.

`kind delete cluster` still destroys the volume: the StorageClass reclaim
policy is `Delete` and the data lives on the kind node. The PVC protects
against pod churn, not against tearing down the cluster.
