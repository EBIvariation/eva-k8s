# eva-k8s

Kubernetes deployment configurations for all EVA (European Variation Archive) Java Spring webservices. This repository is used by GitLab CI/CD to automate deployments to the EBI Kubernetes cluster.

## Repository layout

All manifests live under `k8s-manifests/`. Each service has its own directory following a consistent Kustomize base+overlays pattern:

```
eva-k8s/
└── k8s-manifests/
    └── <service-name>/
        ├── kustomization.yaml        # root entry point (references base)
        ├── base/                     # shared configuration across all environments
        │   ├── kustomization.yaml
        │   ├── deployment.yaml
        │   ├── service.yaml
        │   ├── configmap.yaml
        │   ├── ingress.yaml
        │   └── secret.yaml.example   # template only — never commit real secrets
        └── overlays/
            ├── dev/                  # development environment (wwwint.ebi.ac.uk)
            │   ├── kustomization.yaml
            │   ├── namespace.yaml
            │   ├── deployment-patch.yaml
            │   ├── ingress-patch.yaml
            │   ├── config.env.example
            │   ├── secrets-db.env.example
            │   └── secrets-admin.env.example
            └── local/                # local development (minikube / kind)
                ├── kustomization.yaml
                ├── namespace.yaml
                ├── deployment-patch.yaml
                ├── service-patch.yaml
                └── postgres.yaml     # embedded PostgreSQL for local testing
```

Current services:

| Directory | Description |
|-----------|-------------|
| [`k8s-manifests/eva-seqcol`](./k8s-manifests/eva-seqcol) | Sequence Collections REST API |

## How deployment works

### GitLab CI pipeline

The GitLab CI pipeline deploys a service whenever changes are merged to `main` or a tag is created in a service's directory. The pipeline:

1. Detects which service directories changed.
2. Resolves the target environment (e.g. `dev` for the `main` branch).
3. Populates the secret env files from GitLab CI/CD variables (never stored in this repo).
4. Runs `kubectl apply -k <service>/overlays/<env>` against the EBI cluster.

The image tag to deploy is set via the `images[].newTag` field in the overlay's `kustomization.yaml`. CI updates this value before applying.

### Environments

| Overlay | Cluster host | Namespace pattern | Replicas |
|---------|-------------|-------------------|----------|
| `dev` | wwwint.ebi.ac.uk | `<service>-dev` | 1 |
| `local` | localhost (minikube) | `<service>-local` | 1 + local DB |

Production overlays will be added when services are ready for production.

## Prerequisites

- `kubectl` configured with access to the target cluster
- `kustomize` v4+ (or `kubectl` v1.21+ which bundles it)

## Manual deployment

### Deploy to dev

```bash
cd k8s-manifests/eva-seqcol/overlays/dev

# Copy and fill in the secret/config env files (one-time setup)
cp config.env.example config.env
cp secrets-db.env.example secrets-db.env
cp secrets-admin.env.example secrets-admin.env
# Edit the .env files with real values

# Preview rendered manifests
kubectl kustomize .

# Apply to cluster
kubectl apply -k .
```

### Run locally (minikube / kind)

```bash
# Start a local cluster if needed
minikube start

kubectl apply -k k8s-manifests/eva-seqcol/overlays/local

# Access the service
kubectl port-forward -n eva-seqcol-local svc/eva-seqcol 8081:8081
```

## Secrets management

- Actual `.env` files (`config.env`, `secrets-db.env`, `secrets-admin.env`) are **never committed** — they should be listed in `.gitignore`.
- In CI, secrets are injected from GitLab CI/CD masked variables before `kubectl apply` runs.
- For manual deployments, create the `.env` files locally and keep them out of version control.

## Adding a new service

1. Create `k8s-manifests/<service-name>/base/` with `deployment.yaml`, `service.yaml`, `configmap.yaml`, `ingress.yaml`, `kustomization.yaml`, and `secret.yaml.example`. Use an existing service as a reference.
2. Create `k8s-manifests/<service-name>/overlays/dev/` with the environment-specific patches and `.env.example` templates.
3. Create `k8s-manifests/<service-name>/overlays/local/` for local development.
4. Add the service to the table in this README.
5. Configure the corresponding GitLab CI/CD variables for secrets.

## Image tagging

Each overlay's `kustomization.yaml` contains an `images` block:

```yaml
images:
- name: ebivariation/<service-name>
  newTag: v1.2.3
```

Update `newTag` to the Docker image tag you want to deploy. In CI this is set automatically from the triggering pipeline's image build step.
