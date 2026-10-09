# Monitoring of the EVA services

What is measured, what raises an alert and at which threshold, where alerts go, and what to do next.

**Status:** the monitoring is shared by all services and lives in `k8s-manifests/eva-monitoring`. The alerts on
pods, resources and ingress traffic cover every service as soon as it is deployed. Application metrics need a
change in each application; it is prepared for the nine Spring Boot services (every service except eva-web, which
has no actuator) and must be released together with the manifests of this repository
(see [Releasing the change](#releasing-the-change)). The cluster facts below were read from
the **staging** cluster; dev, prod and prod-fallback must be checked before their first deployment
(see [Before the first deployment](#before-the-first-deployment)).

## How it fits together

```
 application pod                                   monitoring namespace (cluster team's kube-prometheus-stack)
 ┌──────────────────────────────┐
 │ :8080  API, /livez, /readyz  │◄── ingress-nginx, kubelet probes
 │ :9090  /actuator/prometheus  │◄── Prometheus ── PrometheusRule ──► Alertmanager ──► email
 └──────────────────────────────┘    (basic auth,                     (AlertmanagerConfig
        stdout                        NetworkPolicy)                   in eva-monitoring)
          └──► Fluent Bit (DaemonSet) ──► central Elasticsearch                 Grafana reads Prometheus
```

| Piece | Where it is defined |
|---|---|
| Metrics endpoint, management port, authentication | application repository (`application.properties`, `SecurityConfiguration`) |
| Scrape configuration, alert rules, alert routing | `k8s-manifests/eva-monitoring/` : one `ServiceMonitor`, one `PrometheusRule` and one `AlertmanagerConfig` per cluster, in the `eva-monitoring` namespace. Deployed by its own GitLab pipeline, `.gitlab-ci.yml` at the root of this repository (see [Deploying the shared monitoring](#deploying-the-shared-monitoring)) |
| Which services are scraped, network restriction | `k8s-manifests/eva-monitoring/service-component/`, a Kustomize component a service includes from its dev, staging and prod overlays. It labels the Service `eva-monitoring/scrape: "true"` and adds the `NetworkPolicy` |
| Which namespaces the alerts cover | the list in the recording rule at the top of `eva-monitoring/base/prometheusrule.yaml` |
| Scrape credentials | `eva.actuator.user` / `eva.actuator.password` in the Maven settings; CI turns them into `actuator.auth.*` in `application.properties` and, through `monitoring.env`, into the `actuator-credentials` Secret of `eva-monitoring`. All services share them |
| Alert recipient, sender, mail server | `eva.alerts.email-to`, `eva.alerts.email-from`, `eva.email-server`, `eva.email-port` in the Maven settings; CI writes them to `eva-monitoring/base/monitoring.env` and the base copies them into the `AlertmanagerConfig`. The committed manifest only has placeholders |
| Prometheus, Alertmanager, Grafana, Fluent Bit, ingress-nginx, kube-state-metrics | installed and owned by the cluster team; nothing in this repository changes them |

## Actuator exposure

| Path | Port | Authentication | Reachable from |
|---|---|---|---|
| `<context-path>/livez`, `<context-path>/readyz` | application | none (returns only `{"status":"UP"}`) | anywhere the API is |
| `/actuator/health`, `/actuator/info`, `/actuator/prometheus` | 9090 | HTTP basic, role `ACTUATOR_ADMIN` | the `monitoring` namespace only (NetworkPolicy); never through the ingress |

Everything else under `/actuator` is not exposed. To look at an endpoint by hand:

```bash
kubectl port-forward -n eva-seqcol deploy/eva-seqcol 9090:9090
curl -u "$ACTUATOR_USER:$ACTUATOR_PASSWORD" http://localhost:9090/actuator/prometheus
```

Compared with the security team's recommendations there are two deliberate differences:

- `management.server.address=127.0.0.1` is not set. Prometheus scrapes the pod IP, so a port bound to loopback
  cannot be scraped. The NetworkPolicy and the absence of an ingress route give the same restriction.
- The kubelet cannot authenticate, so the liveness and readiness status is served without authentication on the
  application port.

## Metrics gathered

Every metric follows the same path: a **producer** computes it and publishes it on an HTTP endpoint in the
Prometheus text format, and the cluster's **Prometheus** reads (scrapes) that endpoint every 30 seconds. Prometheus
only scrapes what a `ServiceMonitor` (or `PodMonitor`) tells it to. The Prometheus Operator turns each of these
resources into a scrape job, provided the resource carries the label `release: prometheus`.

### Collected from the application

**Producer:** each Spring Boot service. Spring Boot Actuator registers [Micrometer](https://micrometer.io)
instrumentation automatically for the libraries it finds (Spring MVC, the JVM, HikariCP, Tomcat, Logback…), and the
`micrometer-registry-prometheus` dependency publishes the result at `/actuator/prometheus`.

**Collected through:**

| Step | Where it is configured |
|---|---|
| Publish the metrics | application `pom.xml`: `micrometer-registry-prometheus` dependency |
| Serve them on port 9090 at `/actuator/prometheus`, with the `application` tag | application `src/main/resources/application.properties`: `management.server.port`, `management.endpoints.web.exposure.include`, `management.metrics.tags.application` |
| Require the actuator user | application `SecurityConfiguration` (`ActuatorSecurityConfiguration` in eva-server): `actuatorSecurityFilterChain` |
| Expose port 9090 under the name `management` | `k8s-manifests/<service>/base/deployment.yaml` (container port) and `base/service.yaml` (Service port) |
| Opt the Service in to scraping, and let Prometheus reach port 9090 | `k8s-manifests/eva-monitoring/service-component/`: label `eva-monitoring/scrape: "true"` (`kustomization.yaml`), `networkpolicy.yaml` |
| Tell Prometheus to scrape it: port `management`, path, interval 30s, basic auth | `k8s-manifests/eva-monitoring/base/servicemonitor.yaml` |
| Credentials Prometheus scrapes with | `actuator-credentials` Secret, generated by `k8s-manifests/eva-monitoring/base/kustomization.yaml` from `monitoring.env` |

Every series carries `namespace`, `service` and `pod` (added by Prometheus from the scrape target) and
`application="<service>"` (added by the application). List taken from a scrape of eva-seqcol; services using
MongoDB instead of JDBC expose `mongodb_driver_*` instead of `hikaricp_*` / `jdbc_*`.

| Family | Metrics | Produced by (inside the application) | Answers |
|---|---|---|---|
| HTTP requests | `http_server_requests_seconds_{count,sum,max}` by `uri`, `method`, `status`, `outcome`, `exception`; `http_server_requests_active_seconds_*` | Spring MVC request observation, timing every request the application serves | Which endpoints are called, how often, how slow, how many fail |
| JVM memory | `jvm_memory_used_bytes`, `jvm_memory_committed_bytes`, `jvm_memory_max_bytes` by `area` and `id`; `jvm_buffer_*` | Micrometer JVM memory binder, reading the JVM's memory pools | Heap and non-heap usage against the maximum |
| Garbage collection | `jvm_gc_pause_seconds_*`, `jvm_gc_overhead`, `jvm_gc_live_data_size_bytes`, `jvm_gc_max_data_size_bytes`, `jvm_gc_memory_allocated_bytes_total`, `jvm_gc_memory_promoted_bytes_total`, `jvm_memory_usage_after_gc` | Micrometer JVM GC binder, listening to the JVM's garbage collection notifications | Is the JVM spending its time collecting; is the heap too small |
| Threads and classes | `jvm_threads_{live,daemon,peak}_threads`, `jvm_threads_states_threads`, `jvm_threads_started_threads_total`, `jvm_classes_loaded_classes`, `jvm_classes_unloaded_classes_total`, `jvm_compilation_time_ms_total`, `jvm_info` | Micrometer JVM thread, class loader, compilation and info binders | Thread leaks, blocked threads, JVM version |
| Process and host | `process_cpu_usage`, `process_cpu_time_ns_total`, `process_uptime_seconds`, `process_start_time_seconds`, `process_files_open_files`, `process_files_max_files`, `system_cpu_usage`, `system_cpu_count`, `system_load_average_1m`, `disk_free_bytes`, `disk_total_bytes` | Micrometer system binders (processor, uptime, file descriptors, disk space), as seen from inside the container | CPU used by the JVM, restarts, file descriptor leaks |
| Database pool | `hikaricp_connections{,_active,_idle,_pending,_max,_min}`, `hikaricp_connections_timeout_total`, `hikaricp_connections_{acquire,usage,creation}_seconds_*`, `jdbc_connections_{active,idle,max,min}` | HikariCP, the JDBC connection pool, through its Micrometer integration; Spring Boot's data source pool metrics | Is the pool saturated, how long requests wait for a connection |
| Task executors | `executor_active_threads`, `executor_pool_*_threads`, `executor_queued_tasks`, `executor_queue_remaining_tasks`, `executor_completed_tasks_total` | Spring Boot task executor metrics, for the application's thread pools | Backlog of asynchronous work |
| Tomcat | `tomcat_sessions_*` | Spring Boot Tomcat metrics, from the embedded web server | Session counts (connector thread metrics need `server.tomcat.mbeanregistry.enabled=true`) |
| Logging | `logback_events_total` by `level` | Micrometer Logback binder, counting every log event | Rate of ERROR and WARN log lines |
| Startup | `application_started_time_seconds`, `application_ready_time_seconds` | Spring Boot startup listener, once at startup | How long a pod takes to start |
| Spring Security | `spring_security_*` | Spring Security observation of its filter chains | Filter chain timings; rarely useful |

### Collected by the cluster

These producers and their `ServiceMonitor`s are installed and owned by the cluster team; nothing in this
repository configures them. Their only EVA-specific use is in
`k8s-manifests/eva-monitoring/base/prometheusrule.yaml`.

| Source | Produced by | Collected through (cluster team's `ServiceMonitor`) | Metrics used here | Answers |
|---|---|---|---|---|
| ingress-nginx | The ingress-nginx controller pods (namespace `ingress`), which proxy every request from the public hosts to the services | `ingress-nginx-controller` in namespace `ingress`, port `metrics` (10254) | `nginx_ingress_controller_requests` by `exported_namespace`, `ingress`, `status`, `method`; `nginx_ingress_controller_request_duration_seconds_bucket`; `nginx_ingress_controller_response_duration_seconds_bucket` | Traffic, error rate and latency as seen by users, for every service including eva-web |
| kube-state-metrics | Deployment `prometheus-kube-state-metrics` (namespace `monitoring`), which reads the state of Kubernetes objects (Deployments, Pods) from the API server and publishes it as metrics | `prometheus-kube-state-metrics` in namespace `monitoring` | `kube_deployment_status_replicas_available`, `kube_deployment_spec_replicas`, `kube_pod_status_ready`, `kube_pod_container_status_restarts_total`, `kube_pod_container_status_last_terminated_reason`, `kube_pod_container_resource_limits`, `kube_namespace_status_phase` | Are the pods there, ready and stable; which namespaces exist |
| kubelet / cAdvisor | The kubelet on every node; cAdvisor, built into it, measures each container's CPU and memory from the operating system | `prometheus-kube-prometheus-kubelet` in namespace `monitoring` | `container_memory_working_set_bytes`, `container_cpu_usage_seconds_total`, `container_cpu_cfs_throttled_periods_total`, `container_cpu_cfs_periods_total` | Real memory and CPU use of each container against its limits |
| node-exporter | DaemonSet `prometheus-prometheus-node-exporter` (namespace `monitoring`), one pod per node, reading the node's operating system | `prometheus-prometheus-node-exporter` in namespace `monitoring` | `node_*` | State of the cluster nodes |
| Prometheus | Prometheus itself, for every scrape target: 1 if the last scrape succeeded, 0 if not | none: built in | `up` | Is each scrape target reachable |

Prometheus also computes one series of its own from these: `eva:monitored_namespace:info`, the recording rule at
the top of `prometheusrule.yaml`, which lists the EVA namespaces for the alerts.

Prometheus keeps 10 days of data (`retention` in the cluster team's `Prometheus` resource).

### Not gathered

- eva-web internals (nginx serves static files; only the ingress and pod metrics above cover it).
- Database servers (MongoDB, PostgreSQL, Oracle): only the client side is visible, through the connection pool.
- Business metrics (submissions received, accessions served, export sizes).
- Per-endpoint latency percentiles: `http_server_requests` has count, sum and max but no histogram buckets.

### Logs

Fluent Bit already collects the stdout of every container and sends it to the central Elasticsearch, with the
Kubernetes namespace, pod and container attached. Nothing was changed. Two known gaps: a stack trace arrives as
one document per line, and the Tomcat access log is written to a file inside the pod and is not collected
(the ingress-nginx access log is).

## Alerts

Defined once for all services in `k8s-manifests/eva-monitoring/base/prometheusrule.yaml`. Each alert is
evaluated per service and carries the service name in the `eva_service` label (its `namespace` label is always
`eva-monitoring`, which is what routes it). The thresholds are starting values:
staging currently sits far below all of them (memory around 40% of the limit, no throttling, no 5xx), so they
should be reviewed after a few weeks of production data.

| Alert | Fires when | Threshold | For | Severity | First thing to check |
|---|---|---|---|---|---|
| `ServiceDown` | the Deployment has no available replica | `== 0` | 2m | critical | `kubectl get pods -n <svc>`, then `describe pod` and `logs --previous` |
| `ReplicasMismatch` | fewer replicas available than requested | available `<` desired | 15m | warning | A stuck rollout or a pod that cannot be scheduled: `kubectl get events -n <svc>` |
| `PodNotReady` | a pod fails its readiness probe | not Ready | 10m | warning | `kubectl describe pod`; call `<context-path>/readyz` through a port-forward |
| `PodRestarting` | a container restarts repeatedly | `> 2` restarts in 30m | none | warning | `kubectl logs <pod> --previous` |
| `PodOOMKilled` | a container was killed for exceeding its memory limit | any OOM kill followed by a restart within 10m | none | warning | Raise `limits.memory` in the overlay or look for a leak in the heap metrics |
| `ContainerMemoryNearLimit` | working-set memory close to the container limit | `> 90%` of the limit | 15m | warning | Same as above, before it becomes an OOM kill. Most likely to need tuning: a JVM keeps the heap it has touched |
| `CPUThrottlingHigh` | the container is held back by its CPU limit | `> 25%` of CPU periods throttled | 15m | warning | Raise `limits.cpu`, or find what is consuming CPU |
| `High5xxRate` | the ingress returns server errors for this service | `> 5%` of requests, with at least 0.05 requests/s | 10m | critical | Application logs in Elasticsearch; database reachability |
| `HighLatencyP95` | requests through the ingress are slow | 95th percentile `> 5 s` | 15m | warning | Slow endpoints in `http_server_requests_seconds_max`; database pool; CPU throttling |
| `MetricsTargetDown` | Prometheus cannot scrape a pod | `up == 0` | 5m | warning | Credentials in the `actuator-credentials` Secret; the NetworkPolicy; the pod itself |
| `JvmHeapHigh` | the JVM heap is almost full | `> 90%` of the maximum heap | 15m | warning | GC metrics; raise the memory limit (the heap is 75% of it) |
| `DbPoolExhausted` | requests wait for a database connection | any pending thread | 5m | warning | Slow queries, database availability, pool size (`spring.datasource.hikari.maximum-pool-size`) |

Service-specific notes for the remaining rollouts:

- The first nine alerts use metrics the cluster already collects, so they apply to every listed namespace,
  including eva-web and the services whose application has not been changed yet.
- The last three need the application metrics: they stay silent for a service until it is scraped.
  `DbPoolExhausted` only ever applies to services with a JDBC pool.
- vcf-dumper-ws is excluded from `HighLatencyP95` in the expression (exports stream for minutes by design).
  Other per-service exceptions are written the same way.
- contig-alias runs a single replica, so `ServiceDown` fires on every pod loss; that is intended.

### Where alerts go

The `AlertmanagerConfig` of `eva-monitoring` sends every alert labelled `notification: eva-services-email` by
email. All the rules here carry that label. The label is what chooses the destination: to send some alerts
elsewhere (a chat channel, another address), add a route and receiver for another value in
`alertmanagerconfig.yaml` and set that value on those rules. An alert without a matching `notification` label
is not delivered.


- recipient, sender and mail server are not in this repository: they are injected at deploy time from the Maven
  settings (`eva.alerts.email-to`, `eva.alerts.email-from`, `eva.email-server`, `eva.email-port`)
- subject prefixed with the environment: `[EVA Kubernetes monitoring DEV]`, `… STAGING]`, `… PROD]`,
  `… PROD FALLBACK]`
- grouped by alert name and service; first email 30 seconds after the alert fires, updates at most every 5 minutes, a
  reminder every 12 hours while it keeps firing, and an email when it resolves

Limitations:

- `critical` and `warning` go to the same address.
- The cluster team's global Alertmanager configuration drops everything else, so only alerts defined here are delivered.

To silence an alert during maintenance:

```bash
kubectl port-forward -n monitoring svc/prometheus-kube-prometheus-alertmanager 9093:9093
# then http://localhost:9093 → Silences → New silence, matching eva_service=<svc>
```

## Deploying the shared monitoring

`k8s-manifests/eva-monitoring` belongs to no service, so it is not deployed by the service pipelines. It has its
own pipeline, `.gitlab-ci.yml` at the root of this repository, run by the GitLab mirror. Nothing in it is
automatic: in GitLab, **Build → Pipelines → Run pipeline** on `main`, then start the job of each cluster to update.

| Job | Cluster | Maven profile |
|---|---|---|
| `deploy-monitoring:dev` | dev | `development` |
| `deploy-monitoring:staging` | staging | `production_processing` |
| `deploy-monitoring:prod` | prod | `production` |
| `deploy-monitoring:prod-fallback` | prod-fallback | `production,production-fallback` |

- Run it after merging a change to `k8s-manifests/eva-monitoring/`, and after the actuator credentials, the
  alert addresses or the mail server change in the Maven settings. A merged change does nothing until someone runs it.
- Commits mirrored from GitHub, including the image tag commits pushed by service deployments, do not create a
  pipeline.
- Each job generates `monitoring.env` from its Maven profile, applies the
  `eva-monitoring` namespace, then applies the overlay.
- Jobs left unstarted do not mark the pipeline as blocked or failed.

## Releasing the change

The manifests are read from `main` at deploy time and the new probes only work with the new images, so the
change in this repository and the change in the application repositories go out together:

1. Merge the change in this repository (all nine services at once).
2. Straight after, merge the application changes: eva-ws (eva-server, eva-release, count-stats, dgva-server),
   eva-accession (eva-accession-ws), eva-seqcol, contig-alias, eva-submission-ws and vcf-dumper (vcf-dumper-ws).
   Each merge deploys the new image with the new manifests to dev and staging.
3. Check each service on staging (step 4 below), then tag each application to release it to prod and
   prod-fallback.

Between steps 1 and 2, a deployment of a service whose application change is not merged yet fails without harm
(the new pods never become ready and the old ones keep serving). Production is untouched until the tags.

## Adding monitoring to a service

For a new service, or to redo it for one service. Both repositories change together, as above:

1. Application repository: add `micrometer-registry-prometheus`, the `management.*` properties, the actuator
   security chain and user, and the test credentials (`actuator.auth.*`) in the test properties. eva-seqcol is the
   reference for a service with its own basic-auth users, eva-release for one without Spring Security (its
   `SecurityConfiguration` adds an actuator chain and a chain that keeps the API open, without CSRF, sessions or
   the security response headers). Services whose main chain ends in `authenticated()` or `denyAll()` must permit
   `/livez` and `/readyz`. The shared CI template is unchanged.
2. This repository, with eva-seqcol as the reference:
   - `base/deployment.yaml`: name the container ports `http` and `management` (9090) and point the probes at
     `<context-path>/livez` and `<context-path>/readyz`
   - `base/service.yaml`: add the `management` port
   - `overlays/{dev,staging,prod}/kustomization.yaml`: add `components: [../../../eva-monitoring/service-component]`
   - add `actuator.auth.*` to the service's mapping in `scripts/maven-settings-to-properties.py` and to
     `overlays/local/application.properties`

   No monitoring file is created. A brand new service must also be added to the namespace list at the top of
   `eva-monitoring/base/prometheusrule.yaml`, then the monitoring pipeline run by hand for each cluster.
3. Merge the change in this repository, then immediately merge the application change. Its pipeline deploys
   both to dev and staging.
4. Check on staging:
   - `up{namespace="<svc>", endpoint="management"}` is 1 in Prometheus and `jvm_memory_used_bytes{namespace="<svc>"}` has data
   - `https://wwwdev.ebi.ac.uk/<context-path>/actuator/prometheus` returns 404
   - scale the deployment to 0 for a few minutes and confirm the `ServiceDown` email arrives
5. Tag the application to release to prod and prod-fallback.

Until step 5, production is untouched. Redeploying an older tag after step 3 fails without harm (the rollout
never completes and the old pods keep serving); rolling back after step 5 means reverting the commit of step 2 too.

Public `/actuator/health` and `/actuator/info` (for eva-seqcol and contig-alias, `/health` and `/info`)
disappear with this change, for every service. Anything outside the cluster that polls them must use `<context-path>/readyz`.

## Next steps: Grafana dashboards

1. **Look at what exists.** The stack ships dashboards for pods, workloads and namespaces
   ("Kubernetes / Compute Resources / Namespace (Pods)"), which already show CPU and memory per EVA service.
2. **Import community dashboards** (Dashboards → New → Import, by ID) as a first view of the new metrics:
   - `4701` JVM (Micrometer): heap, GC, threads per pod. It filters on the `application` label the services now set.
   - `19004` Spring Boot 3.x Statistics: HTTP requests, pool, logback events.
   - `9614` NGINX Ingress controller: request rate, error rate and latency per ingress.
3. **Build one "EVA services" overview** with a row per service: request rate, 5xx ratio and p95 latency from
   the ingress, replicas ready, restarts, heap used against max, pool active against max. The alert
   expressions in `prometheusrule.yaml` are a starting point for the queries.
4. **Keep it in git.** Export the dashboard JSON and commit it as a ConfigMap labelled `grafana_dashboard: "1"`;
   the Grafana sidecar loads such ConfigMaps from any namespace, so it can be deployed like the rest. Dashboards
   created only in the UI are lost if the Grafana pod is recreated without persistent storage.
5. **Ask the cluster team** for an ingress or single sign-on for Grafana so that it can be reached without a
   kubeconfig, and for the central Elasticsearch as a Grafana data source, so that logs sit next to the metrics.

## Recommended improvements

Ordered by how much they would help during an incident.

1. **Structured logs.** `logging.structured.format.console=ecs` (Spring Boot 3.4) writes one JSON document per
   event, so a stack trace is one searchable entry in Elasticsearch with level and logger as fields. It
   replaces the pattern in `logback-spring.xml`. On the cluster side, the staging Fluent Bit already merges JSON
   log lines into fields (`Merge_Log On`, under `log_processed`) and honours the `fluentbit.io/parser` pod
   annotations (`K8S-Logging.Parser On`). Ensembl sets `fluentbit.io/parser_stderr: json` on its pods to name the
   parser explicitly; the same annotation on our Deployments would make the intent visible and not depend on
   the cluster default. Check the Fluent Bit configuration of the other clusters first.
2. **Access logs.** Send the Tomcat access log to stdout, or switch it off and rely on the ingress log.
   Today it fills a file in the pod that nobody reads.
3. **Per-endpoint latency.** `management.metrics.distribution.percentiles-histogram.http.server.requests=true`
   adds histogram buckets, which allows percentiles per endpoint and alerts based on an error budget rather
   than fixed thresholds. It multiplies the number of series, so enable it where it matters first.
4. **Database visibility.** Exporters for MongoDB and PostgreSQL, or at least alerts on connection errors
   (`hikaricp_connections_timeout_total`, `mongodb_driver_pool_*`).
5. **Business metrics.** Counters and timers for what the services are for: submissions received, accessions
   returned, export duration and size in vcf-dumper-ws.
6. **Separate routing by severity.** Send `critical` to a chat channel or on-call once the email volume is known:
   give those rules another `notification` value and add the matching route and receiver. Consider not routing
   the dev cluster at all.
7. **Longer history.** 10 days on a 10Gi volume is enough for incidents, not for trends or capacity planning;
   ask the cluster team about long-term storage.
8. **Share the actuator security code.** The same filter chain and user are needed in nine applications; a
   small shared library avoids nine copies drifting apart.
9. **Keep the real namespace on alerts.** Alerts are relabelled to `namespace="eva-monitoring"` only because
   Alertmanager matches routes on the namespace. If the cluster team sets `alertmanagerConfigMatcherStrategy`
   to `None` on the Alertmanager, that override and the `eva_service` label become unnecessary.
10. **Daily report.** A CronJob that queries Prometheus every morning and sends a summary (requests, error
    ratio, p95 latency, restarts per service over the last 24 hours) shows slow trends that never cross an
    alert threshold. Ensembl runs one (`prometheus_daily_report/` in their manifests repository) that posts
    to Slack at 07:00 UTC.
11. **Review the thresholds** after the first weeks in production, `ContainerMemoryNearLimit` and
    `HighLatencyP95` first.
