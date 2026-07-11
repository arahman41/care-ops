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
