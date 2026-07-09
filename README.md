# eva-k8s

Kubernetes deployment configurations for all EVA (European Variation Archive) Java Spring webservices. This repository is used by GitLab CI/CD to automate deployments to the EBI Kubernetes cluster.

## Repository layout

All manifests live under `k8s-manifests/`. Each service has its own directory following a consistent Kustomize base+overlays pattern:

```
eva-k8s/
└── k8s-manifests/
    └── <service-name>/
        ├── base/                     # shared configuration across all environments
        │   ├── kustomization.yaml
        │   ├── deployment.yaml
        │   ├── service.yaml
        │   └── ingress.yaml
        └── overlays/
            ├── dev/                  # development environment (wwwint.ebi.ac.uk)
            │   ├── kustomization.yaml
            │   ├── namespace.yaml
            │   ├── deployment-patch.yaml
            │   ├── ingress-patch.yaml
            │   └── application.properties  # generated at deploy time — never committed
            ├── local/                # local development (minikube / kind)
            │   ├── kustomization.yaml
            │   ├── namespace.yaml
            │   ├── deployment-patch.yaml
            │   ├── service-patch.yaml
            │   ├── postgres.yaml     # embedded PostgreSQL for local testing
            │   └── application.properties  # generated at deploy time — never committed
            └── staging/                # staging development (wwwdev.ebi.ac.uk)
                ├── kustomization.yaml
                ...
```

Current services:

| Directory | Description                   |
|-----------|-------------------------------|
| [`k8s-manifests/eva-seqcol`](./k8s-manifests/eva-seqcol) | Sequence Collections REST API |
| [`k8s-manifests/contig-alias`](./k8s-manifests/contig-alias) | Contig/chromosome alias resolution REST API |
| [`k8s-manifests/eva-accession-ws`](./k8s-manifests/eva-accession-ws) | Variant Identifiers REST API  |

## How deployment works

### Application configuration

Each service is a Spring Boot application that reads its configuration from `/app/config/application.properties` at startup. This file contains environment-specific values: database URL and credentials, admin credentials, feature flags, etc.

The file is never stored in this repository. Instead, it is generated at deploy time from a Maven `settings.xml` file using the conversion script:

```bash
python scripts/maven-settings-to-properties.py \
  --maven_file /path/to/settings.xml \
  --profile dev \
  --property_set eva-seqcol \
  --output k8s-manifests/eva-seqcol/overlays/dev/application.properties
```

Kustomize picks up the generated file via a `secretGenerator` in the overlay's `kustomization.yaml`, which packages it into a Kubernetes Secret. The deployment then mounts that Secret as a read-only volume at `/app/config/`, making `application.properties` available to the Spring Boot process exactly as if it were a local file.

```
Maven settings.xml  ──[script]──►  application.properties
                                          │
                                   kustomize build
                                          │
                                   Kubernetes Secret (eva-seqcol-config)
                                          │
                                   Volume mount → /app/config/application.properties
                                          │
                                   Spring Boot reads config at startup
```

### GitLab CI pipeline

The GitLab CI pipeline deploys a service whenever changes are merged to `main` or a tag is created in a service's directory. The pipeline:

1. Detects which service directories changed.
2. Resolves the target environment (e.g. `dev` for the `main` branch).
3. Retrieves the Maven `settings.xml` from a GitLab CI/CD secret variable.
4. Runs `scripts/maven-settings-to-properties.py` to generate `application.properties` in the overlay directory.
5. Runs `kubectl apply -k <service>/overlays/<env>` against the EBI cluster.

The image tag to deploy is set via the `images[].newTag` field in the overlay's `kustomization.yaml`. CI updates this value before applying.

### Environments

| Overlay   | Cluster host | Namespace pattern | Replicas         |
|-----------|-------------|-------------------|-------------------|
| `dev`     | wwwint.ebi.ac.uk | `<service>-dev`   | 1            |
| `staging` | wwwdev.ebi.ac.uk | `<service>-stage` | 3 (1 for contig-alias) |
| `local`   | localhost (minikube) | `<service>-local` | 1 + local DB|

Production overlays will be added when services are ready for production.

## Prerequisites

- `kubectl` configured with access to the target cluster
- `kustomize` v4+ (or `kubectl` v1.21+ which bundles it)

## Manual deployment

### Deploy to dev

```bash
# Generate application.properties from your local Maven settings.xml
python scripts/maven-settings-to-properties.py \
  --maven_file settings.xml \
  --profile dev \
  --property_set eva-seqcol \
  --output k8s-manifests/eva-seqcol/overlays/dev/application.properties

# Preview rendered manifests
kubectl kustomize k8s-manifests/eva-seqcol/overlays/dev

# Apply to cluster
kubectl apply -k k8s-manifests/eva-seqcol/overlays/dev
```

### Run locally (minikube / kind)

```bash
# Generate application.properties for local profile
python scripts/maven-settings-to-properties.py \
  --maven_file ~/.m2/settings.xml \
  --profile localhost \
  --property_set eva-seqcol \
  --output k8s-manifests/eva-seqcol/overlays/local/application.properties

# Start a local cluster if needed
minikube start

kubectl apply -k k8s-manifests/eva-seqcol/overlays/local

# Access the service
kubectl port-forward -n eva-seqcol-local svc/eva-seqcol 8081:8081
```

## Manifest validation

All manifests are validated on every push and pull request to `main` via a GitHub Actions. 
The workflow:

- **Kustomize build** — ensures all overlays build without syntax errors
- **Kubeconform** — validates manifests against the Kubernetes API schema
- **Kubesec** — security scanning (resource limits, securityContext, etc.)
- **Kube-score** — best practices linting (readiness probes, labels, etc.)

Each overlay under `k8s-manifests/*/overlays/*` is discovered and validated automatically.

To validate locally before pushing:

```bash
kustomize build k8s-manifests/eva-seqcol/overlays/dev
```

## Secrets management

- `application.properties` files are **never committed** — they are listed in `.gitignore` and generated at deploy time.
- The source of truth for environment-specific values is the Maven `settings.xml`.
- For manual deployments, use your local maven `settings.xml` and generate `application.properties` with the conversion script (see [Deploy to dev](#deploy-to-dev)).
- The generated `application.properties` is packaged into a Kubernetes Secret by `kustomize`.

## Adding a new service

1. Create `k8s-manifests/<service-name>/base/` with `deployment.yaml`, `service.yaml`, `ingress.yaml`, and `kustomization.yaml`. Use an existing service as a reference.
2. Create `k8s-manifests/<service-name>/overlays/dev/` with the environment-specific templates.
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
