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
        │   ├── ingress.yaml
        │   └── namespace.yaml        # namespace = service name, identical in every cluster
        └── overlays/
            ├── dev/                  # development environment (wwwint.ebi.ac.uk)
            │   ├── kustomization.yaml
            │   ├── deployment-patch.yaml
            │   ├── ingress-patch.yaml
            │   └── application.properties  # generated at deploy time — never committed
            ├── local/                # local development (minikube / kind)
            │   ├── kustomization.yaml
            │   ├── deployment-patch.yaml
            │   ├── service-patch.yaml
            │   ├── postgres.yaml     # embedded DB for local testing (or mongodb.yaml / oracle.yaml)
            │   └── application.properties  # committed: points at the local DB only
            ├── staging/                # staging environment (wwwdev.ebi.ac.uk)
            │   ├── kustomization.yaml
            │   ...
            └── prod/                  # production environment (www.ebi.ac.uk), deployed on git tags
                ├── kustomization.yaml
                ...
```

Current services:

| Directory | Description | Source repo | Ingress path |
|-----------|-------------|-------------|--------------|
| [`k8s-manifests/eva-server`](./k8s-manifests/eva-server) | Main EVA REST API (variants, studies, files) | eva-ws | `/eva/webservices/rest` |
| [`k8s-manifests/eva-release`](./k8s-manifests/eva-release) | RS release REST API | eva-ws | `/eva/webservices/release` |
| [`k8s-manifests/count-stats`](./k8s-manifests/count-stats) | Count statistics REST API | eva-ws | `/eva/webservices/count-stats` |
| [`k8s-manifests/dgva-server`](./k8s-manifests/dgva-server) | DGVA REST API (no dev deployment: no DGVA dev database) | eva-ws | `/dgva/webservices/rest` |
| [`k8s-manifests/eva-accession-ws`](./k8s-manifests/eva-accession-ws) | Variant Identifiers REST API | eva-accession | `/eva/webservices/identifiers` |
| [`k8s-manifests/eva-seqcol`](./k8s-manifests/eva-seqcol) | Sequence Collections REST API | eva-seqcol | `/eva/webservices/seqcol` |
| [`k8s-manifests/contig-alias`](./k8s-manifests/contig-alias) | Contig/chromosome alias resolution REST API | contig-alias | `/eva/webservices/contig-alias` |
| [`k8s-manifests/eva-submission-ws`](./k8s-manifests/eva-submission-ws) | Submission REST API | eva-submission-ws | `/eva/webservices/submission-ws` |
| [`k8s-manifests/eva-web`](./k8s-manifests/eva-web) | Static frontend (nginx) | eva-web | `/eva` |

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

Deployments are driven by the GitLab CI pipelines of the application repositories (e.g. eva-ws), which include the
shared template [`gitlab-ci/webservice.yml`](./gitlab-ci/webservice.yml) pinned to a tag of this repository and
extend its `.build_docker` and `.update_and_deploy` jobs (eva-web uses a similar pipeline of its own).
A merge to the `main` branch deploys to `dev` and `staging`; a git tag deploys to `prod` and `prod-fallback`.

`.build_docker` builds and pushes the image (or re-tags an existing image for the same service and commit).
`.update_and_deploy` then:

1. Clones this repository and downloads the target cluster's kubeconfig from the private `EBIvariation/configuration` repository.
2. Applies `base/namespace.yaml` and creates/updates the `regcred` image pull secret from `DOCKER_AUTH_CONFIG`.
3. Downloads the Maven `settings.xml` and runs `scripts/maven-settings-to-properties.py` to generate `application.properties`.
4. Runs `scripts/update-image-tag.py` to set `images[].newTag` in the overlay's `kustomization.yaml`, then commits and pushes that change to `main`.
5. Runs `kubectl apply -k <overlay>` followed by `kubectl rollout status`.

The git history of this repository is therefore a record of what was deployed where.

### Environments

Each environment is a separate cluster, and the namespace is always the service name (e.g. `eva-seqcol`).

| Overlay   | Cluster host | Maven profile |  Replicas |
|-----------|--------------|---------------|-----------|----------|
| `dev`     | wwwint.ebi.ac.uk | `development` |  1 |
| `staging` | wwwdev.ebi.ac.uk | `production_processing` | 3 |
| `prod`    | www.ebi.ac.uk | `production,$ACTIVE_EVAPRO` | 3 |
| `prod` (fallback cluster) | www.ebi.ac.uk (fallback) | `production,production-fallback,${ACTIVE_EVAPRO}-fallback` | 3 |
| `local`   | localhost (minikube / kind / Rancher Desktop) | committed `application.properties` | 1 + local DB |


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
kubectl port-forward -n eva-seqcol svc/eva-seqcol 8081:8081
```

### eva-web 

`eva-web` has no `application.properties`/database because its config is baked into the
build at image build time. To test it against a local cluster:

```bash
# Build the image locally, tagged to match overlays/local/kustomization.yaml
cd /path/to/eva-web && docker build --build-arg ENVIRONMENT_NAME=dev -t eva-web:local .

# Apply the local overlay
kubectl apply -k k8s-manifests/eva-web/overlays/local

# The overlay exposes eva-web as a LoadBalancer Service find it with:
kubectl get svc -n eva-web eva-web

# then browse http://<EXTERNAL-IP>:8090/eva/, or simply:
kubectl port-forward -n eva-web svc/eva-web 8090:8090
```

eva-web serves its own `/eva` prefix directly (its Dockerfile copies the build into
`eva/` and nginx.conf falls back to `eva/index.html`), 
so this already works end-to-end, even hitting the Service directly, no ingress rewrite
needed.

Note for Macs: Rancher Desktop ships Traefik by default, which doesn't support the
`nginx.ingress.kubernetes.io/*` annotations, so we need to install ingress-nginx :

```bash
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm install ingress-nginx ingress-nginx/ingress-nginx \
  --namespace ingress-nginx --create-namespace \
  --set controller.service.ports.http=18080 \
  --set controller.service.ports.https=18443
  # custom ports avoid colliding with Traefik's own LoadBalancer on 80/443
```

Once ingress-nginx is up:

```bash
kubectl apply -k k8s-manifests/eva-web/overlays/local

# Forward the nginx Service's port 18080 to localhost:28080
kubectl port-forward -n ingress-nginx svc/ingress-nginx-controller 28080:18080
# Recommended: use localhost instead of nginx external IP. 
# The Service's external IP is reachable directly from
# the host in the common case, but the VPN client intercepts browser 
# traffic and only localhost is guaranteed to bypass it

# browse http://localhost:28080/eva/

# Alternative, if the VPN is not on: browse the Service's own external IP
kubectl get svc -n ingress-nginx ingress-nginx-controller   # find the external IP
# then browse http://<EXTERNAL-IP>:18080/eva/
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
