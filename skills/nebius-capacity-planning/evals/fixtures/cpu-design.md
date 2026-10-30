# Synthetic application design

This fixture contains synthetic measurements and an arithmetic candidate, not
a real tenant or a claim of current Nebius catalog availability.

Environment: Dev/Test; 30-day data horizon; two developers; remote model API.
Assess CPU-only MK8s with SFS requested for persistent live data and separate
object backup storage. No cloud access is available. Ask no environment
question because it is supplied. Do not turn this fixture's choices into
universal skill defaults.

The architecture includes four API Pods, three database replicas and a daily
backup Job. The API Pod starts a native sidecar (500m CPU, 256Mi memory
requests), then a migration init container (2000m, 1024Mi requests). The steady
app needs 1000m CPU and 1280Mi memory requests; sidecar remains running.
Configured Pod overhead is 100m CPU and 64Mi memory. CPU limits are unset for
app/sidecar; recommend and justify memory limits from provisional budgets
because no measured peaks exist. Migration CPU limit is 3000m and memory
limit 2048Mi. Effective runtime is unmeasured.

During an application rollout, one new API Pod and one terminating API Pod can
overlap the desired four. The design requires normal load after one worker
failure; rollout may be paused during failure.

The proposed arithmetic candidate has four workers, each 4000m CPU/8192Mi
allocatable. Infrastructure Pods consume 500m/512Mi and two Pod slots on each
usable node. There are 20 Pod slots per node. The failed node remains allocated.
The database needs provisional 1000m/2048Mi request per replica, 2000m/4096Mi
limits; each database has an independent 100Gi live data copy. One independent
150Gi object backup budget covers retained backups. Backup Job resources are
unmeasured; propose explicit estimates and assess its overlap.

Network requirements: private workers, one public ingress IP, supported egress
to remote APIs, subnet capacity for workers and upgrade surge. No bandwidth
measurements, actual project quota, verified prices or physical capacity
evidence are supplied. Qualify recommendations accordingly.
