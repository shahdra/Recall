#!/usr/bin/env bash
#
# bootstrap.sh — turn a freshly `kubeadm init`-ed control plane into a working
# Recall cluster: CNI, storage, namespaces, secrets, ArgoCD, and the two ArgoCD
# Applications that own every workload deploy from then on.
#
# THIS SCRIPT IS THE SECOND HALF OF THE DEPLOY. `terraform apply` gives you EC2
# instances whose nodes report NotReady forever, because nothing has installed a
# CNI. Terraform cannot do this part: it would need API-server credentials that do
# not exist until the control plane has booted and run kubeadm.
#
# Runs ON the control-plane node. Either pipe it in over SSH from your laptop:
#
#   scp -i ~/.ssh/shahd-key.pem services/tutor-agent/.env ubuntu@<cp-ip>:/tmp/recall.env
#   ssh -i ~/.ssh/shahd-key.pem ubuntu@<cp-ip> \
#     "RECALL_ENV_FILE=/tmp/recall.env bash -s" < infra/k8s/bootstrap.sh
#
# ...or run it from a checkout on the node:
#
#   RECALL_ENV_FILE=/tmp/recall.env ./infra/k8s/bootstrap.sh
#
# IDEMPOTENT BY DESIGN — safe to re-run against an already-bootstrapped cluster,
# which matters because the usual reason to run it twice is that something failed
# halfway:
#   * `kubectl apply`, never `kubectl create` (which fails AlreadyExists and, under
#     `set -e`, would kill the whole run on attempt 2)
#   * namespaces and secrets go through `create --dry-run=client | kubectl apply`,
#     the declarative equivalent — this also UPDATES the Secret if the env file
#     changed
#   * every remote URL is version-pinned. "stable"/"latest" in a bootstrap script is
#     a future outage with no code change to blame it on
#   * `kubectl wait` between phases rather than `sleep` — a fixed sleep is either too
#     short (flaky) or too long (slow)
#
# Required environment:
#   RECALL_ENV_FILE  path to an env file (KEY=VALUE lines) holding DEEPGRAM_API_KEY.
#                    Becomes the `recall-secrets` Secret in both dev and prod. Every
#                    workload consumes it via envFrom, so a missing file means pods in
#                    CreateContainerConfigError. See
#                    infra/k8s/secrets-templates/dev-secret.example.yaml.
#
#                    NO AWS CREDENTIALS GO IN HERE. Pods inherit the worker node's IAM
#                    role through the instance metadata service, so there is no access
#                    key to deliver — and an explicit key would SHADOW the role, since
#                    boto3 prefers env vars over the instance profile.
# Optional environment:
#   REPO_DIR         repo checkout on this node (default: $HOME/Recall). Needed for
#                    the ArgoCD Application manifests.
#   REPO_URL         clone source if REPO_DIR is absent.
#   REPO_REF         branch to check out (default: dev).
#   SKIP_EBS_CSI     set to 1 to skip the EBS CSI driver. Nothing in Recall uses a
#                    PersistentVolumeClaim today, so this is safe and saves ~1 min;
#                    the default installs it so that adding Prometheus later needs no
#                    IAM or bootstrap change.

set -euo pipefail

# --- pinned versions -------------------------------------------------------
CALICO_VERSION="v3.28.0"
ARGOCD_VERSION="v2.13.2"
EBS_CSI_VERSION="release-1.31"

# --- config ----------------------------------------------------------------
REPO_DIR="${REPO_DIR:-$HOME/Recall}"
REPO_URL="${REPO_URL:-https://github.com/shahdra/Recall.git}"
REPO_REF="${REPO_REF:-dev}"

export KUBECONFIG="${KUBECONFIG:-/etc/kubernetes/admin.conf}"
# admin.conf is root-owned mode 600; fall back to the ubuntu user's copy when this
# script runs unprivileged (which it does when piped over SSH as `ubuntu`).
if [ ! -r "$KUBECONFIG" ] && [ -r "$HOME/.kube/config" ]; then
  export KUBECONFIG="$HOME/.kube/config"
fi

step() { printf '\n=== %s ===\n' "$*"; }
fail() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

# --- preflight -------------------------------------------------------------
step "0/8 Preflight"

command -v kubectl >/dev/null || fail "kubectl not found. Did control-plane user-data finish?
Check:  sudo tail -50 /var/log/user-data.log"

[ -n "${RECALL_ENV_FILE:-}" ] || fail "RECALL_ENV_FILE is required.
It must point at an env file containing DEEPGRAM_API_KEY. Without it the app pods
cannot start. Locally that key lives in services/tutor-agent/.env, so:
  grep '^DEEPGRAM_API_KEY=' services/tutor-agent/.env > /tmp/recall.env
No AWS credentials are needed — pods inherit the worker node's IAM role."
[ -f "$RECALL_ENV_FILE" ] || fail "RECALL_ENV_FILE=$RECALL_ENV_FILE does not exist."
[ -s "$RECALL_ENV_FILE" ] || fail "RECALL_ENV_FILE=$RECALL_ENV_FILE is empty."

# Check the required key is present BEFORE spending 8 minutes on a bootstrap whose
# pods will then fail to start. grep on an anchored key= pattern, so a value that
# happens to contain the string does not count as the key.
#
# DEEPGRAM_API_KEY is the only secret the env file needs. AWS credentials are NOT in
# here: pods inherit the worker node's IAM role through the instance metadata service
# (see infra/terraform/iam.tf), so there is no access key to deliver.
grep -qE "^DEEPGRAM_API_KEY=" "$RECALL_ENV_FILE" \
  || fail "$RECALL_ENV_FILE is missing DEEPGRAM_API_KEY. See infra/k8s/secrets-templates/dev-secret.example.yaml"

# Warn, rather than fail, if AWS keys are still present. They are harmless but
# misleading: boto3's credential chain prefers explicit env vars over the instance
# role, so a stale key here SHADOWS the node role and the pods fail with
# InvalidClientTokenId while the role sitting right there would have worked. That is a
# genuinely confusing failure, so it is worth calling out loudly.
for key in AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY; do
  if grep -qE "^${key}=" "$RECALL_ENV_FILE"; then
    echo "WARNING: $RECALL_ENV_FILE sets $key." >&2
    echo "  Pods now inherit AWS credentials from the worker node's IAM role, so this" >&2
    echo "  is not needed — and boto3 PREFERS an explicit env var over the role, so a" >&2
    echo "  stale value here will shadow a working role. Remove it unless you know why" >&2
    echo "  you want it." >&2
  fi
done

# RECALL_DEMO_MODE must NOT reach prod: the ⏩ control's day offset is process-wide,
# so it would shift time for every user at once. It is set per-namespace in
# infra/k8s/dev/configmap.yaml and deliberately ABSENT (not "false") from prod's.
# Catch it here in case someone added it to the env file, which lands in BOTH
# namespaces.
if grep -qE "^RECALL_DEMO_MODE=" "$RECALL_ENV_FILE"; then
  fail "RECALL_ENV_FILE sets RECALL_DEMO_MODE. Remove it.
This file becomes the Secret in BOTH dev and prod, and demo mode in prod shifts the
review clock for every user. Dev already enables it via infra/k8s/dev/configmap.yaml."
fi

kubectl cluster-info >/dev/null 2>&1 || fail "cannot reach the API server with KUBECONFIG=$KUBECONFIG"
echo "API server reachable; using KUBECONFIG=$KUBECONFIG"

# The ArgoCD Application manifests live in the repo, so we need a checkout on this
# node. Note this clones the PUBLIC repo over https — no deploy key needed, and none
# should be put on this node.
if [ -d "$REPO_DIR/.git" ]; then
  echo "Updating existing checkout at $REPO_DIR"
  git -C "$REPO_DIR" fetch --quiet origin "$REPO_REF"
  git -C "$REPO_DIR" checkout --quiet "$REPO_REF"
  git -C "$REPO_DIR" reset --hard --quiet "origin/$REPO_REF"
else
  echo "Cloning $REPO_URL ($REPO_REF) into $REPO_DIR"
  command -v git >/dev/null || fail "git not found on this node; install it or set REPO_DIR to an existing checkout"
  git clone --quiet --branch "$REPO_REF" "$REPO_URL" "$REPO_DIR" \
    || fail "clone of $REPO_URL branch $REPO_REF failed.
If the branch does not exist yet:  git push origin implementation:dev"
fi
cd "$REPO_DIR"

# --- 1. Calico CNI ---------------------------------------------------------
step "1/8 Calico CNI ($CALICO_VERSION)"
# Nodes stay NotReady until a CNI is installed — this is the step that makes the
# cluster schedulable. server-side apply because the tigera-operator manifest
# contains CRDs whose annotations exceed the client-side apply size limit.
kubectl apply --server-side --force-conflicts \
  -f "https://raw.githubusercontent.com/projectcalico/calico/${CALICO_VERSION}/manifests/tigera-operator.yaml"

# Wait for the CRDs to register in the API server's discovery cache before applying
# any custom resource that uses them. Without this the very next apply races the
# cache and fails with:
#   no matches for kind "Installation" in version "operator.tigera.io/v1"
# `kubectl apply` does not wait for CRD establishment, and the failure is timing
# dependent — it can pass by hand and fail in CI, or vice versa.
#
# Each CRD gets its OWN bounded retry loop rather than one multi-arg `kubectl wait`,
# because of a subtle failure mode:
#
#   error: .status.conditions accessor error: <nil> is of the type <nil>,
#          expected []interface{}
#
# `--server-side` apply returns as soon as the object is PERSISTED, but the API
# server populates `.status` a moment later. In that window `.status.conditions` is
# absent (nil), not an empty list — and `kubectl wait` cannot traverse a nil field,
# so it EXITS NON-ZERO IMMEDIATELY rather than waiting. `--timeout` never gets a
# chance to help, and `set -e` then kills the whole bootstrap.
#
# Two consequences shape the loop below:
#   1. The nil-status error must be treated as "not ready yet", not fatal — hence the
#      if/break with 2>/dev/null to keep the transient accessor error out of the log.
#   2. One CRD per `kubectl wait` call. With three names in a single call, a nil
#      status on ANY of them fails the whole command even when the others are already
#      Established.
#
# 24 attempts x 5s = up to 2 minutes per CRD. Establishment normally takes a second
# or two; the generous bound only matters on a cold, loaded API server.
echo "Waiting for Calico CRDs to be established..."
for crd in \
  installations.operator.tigera.io \
  apiservers.operator.tigera.io \
  tigerastatuses.operator.tigera.io
do
  established=0
  for attempt in $(seq 1 24); do
    if kubectl wait --for=condition=Established --timeout=10s "crd/$crd" 2>/dev/null; then
      established=1
      break
    fi
    # Distinguish "not created yet" from "created but status not populated" so a
    # genuinely missing CRD is obvious in the log rather than looking like lag.
    if kubectl get "crd/$crd" >/dev/null 2>&1; then
      echo "  $crd exists but .status is not populated yet (attempt $attempt/24)"
    else
      echo "  $crd not registered yet (attempt $attempt/24)"
    fi
    sleep 5
  done
  [ "$established" -eq 1 ] || fail "CRD $crd never became Established.
The tigera-operator manifest applied but this CRD did not converge. Check:
  kubectl get crd | grep tigera
  kubectl -n tigera-operator get pods
  kubectl -n tigera-operator logs deploy/tigera-operator"
  echo "  $crd Established"
done

# The Installation CR is written inline rather than fetched from upstream's
# custom-resources.yaml, for one important reason: `encapsulation`.
#
# Upstream defaults to VXLANCrossSubnet, which encapsulates pod traffic ONLY when the
# two nodes are in different subnets and sends RAW pod-IP packets when they share
# one. On AWS that raw path is silently dropped:
#   * the VPC route table has no route for 192.168.0.0/16, and
#   * the ENI source/destination check rejects packets whose source is a pod IP.
#
# Our ASG spans two subnets, so whether any given pair of nodes shares one is luck.
# When they do, cross-node pod traffic dies — which surfaces as DNS timeouts and
# CrashLoopBackOff for anything resolving a Service from a different node than
# CoreDNS. For Recall specifically it would break tutor-agent -> study-mcp over MCP,
# and because tutor-agent discovers its tools ONCE at startup with no retry
# (services/tutor-agent/app.py), a pod that boots during such a failure stays
# permanently toolless and every request 503s. It would look like an app bug.
#
# `encapsulation: VXLAN` always tunnels, so the underlay only ever sees node-IP UDP
# traffic that AWS is happy to route. The cost is ~50 bytes of overhead per packet,
# which is the right trade for correctness.
#
# ipPool cidr MUST match `kubeadm init --pod-network-cidr` — 192.168.0.0/16, set by
# var.pod_network_cidr in infra/terraform. Change one, change both.
#
# Retry loop: even after the CRDs report Established, the aggregated discovery cache
# in front of the API server can lag a few seconds. Three attempts at 10s covers it
# without masking a real error (the last failure still surfaces).
for attempt in 1 2 3; do
  if kubectl apply -f - <<'CALICO_INSTALLATION'; then
apiVersion: operator.tigera.io/v1
kind: Installation
metadata:
  name: default
spec:
  calicoNetwork:
    ipPools:
      - name: default-ipv4-ippool
        blockSize: 26
        cidr: 192.168.0.0/16
        encapsulation: VXLAN
        natOutgoing: Enabled
        nodeSelector: all()
---
apiVersion: operator.tigera.io/v1
kind: APIServer
metadata:
  name: default
spec: {}
CALICO_INSTALLATION
    break
  fi
  [ "$attempt" -eq 3 ] && fail "could not apply the Calico Installation after 3 attempts"
  echo "  discovery cache still catching up; retrying in 10s (attempt $attempt/3)"
  sleep 10
done

# --- 2. Wait for nodes Ready ----------------------------------------------
step "2/8 Waiting for all nodes to become Ready"
# Everything below needs schedulable nodes. The operator takes a moment to create the
# calico-node DaemonSet, so give the condition a generous window.
#
# NOTE this waits only on nodes that have ALREADY JOINED. A worker still installing
# cri-o (~6-8 min from launch) is not yet a Node object, so this can pass with just
# the control plane. That is fine — the pods will schedule once the worker joins.
kubectl wait --for=condition=Ready nodes --all --timeout=420s
kubectl get nodes -o wide

# --- 3. EBS CSI driver ----------------------------------------------------
if [ "${SKIP_EBS_CSI:-0}" = "1" ]; then
  step "3/8 EBS CSI driver — SKIPPED (SKIP_EBS_CSI=1)"
else
  step "3/8 EBS CSI driver ($EBS_CSI_VERSION)"
  # Nothing in Recall uses a PersistentVolumeClaim today — all four workloads are
  # stateless with state in DynamoDB — so this is installed for the future
  # (Prometheus/Grafana in Phase 7) rather than for a current need.
  #
  # IAM (AmazonEBSCSIDriverPolicy) is already attached to both node roles by
  # infra/terraform/modules/k8s-cluster/main.tf, so no credential is needed here.
  #
  # WARNING for teardown: volumes this driver provisions are created by the
  # CONTROLLER, not by Terraform, so `terraform destroy` cannot see them and they
  # survive as orphaned EBS volumes that keep billing. Delete PVCs before destroying.
  # RUNBOOK.md §Destroy has the cleanup command.
  kubectl apply -k "github.com/kubernetes-sigs/aws-ebs-csi-driver/deploy/kubernetes/overlays/stable/?ref=${EBS_CSI_VERSION}"
fi

# --- 4. Namespaces --------------------------------------------------------
step "4/8 Namespaces (dev, prod, argocd)"
# ONE cluster serves both environments, separated by namespace. This is also why
# there is one set of DynamoDB tables and one bucket: the split is at the Kubernetes
# layer, not the AWS layer.
#
# ArgoCD's CreateNamespace=true would make dev and prod anyway, but they are created
# here because step 5 puts a Secret in each — and that must happen BEFORE the first
# sync, or the pods' first start hits CreateContainerConfigError.
for ns in dev prod argocd; do
  kubectl create namespace "$ns" --dry-run=client -o yaml | kubectl apply -f -
done

# --- 5. recall-secrets ----------------------------------------------------
step "5/8 recall-secrets in dev and prod"
# Deliberately NOT in git. `create --dry-run | apply` makes this both idempotent and
# updating: change the env file, re-run, and the Secret is patched.
#
# Output is suppressed because kubectl echoes the resource, and on some versions a
# diff can surface data keys — this script's output ends up in terminal scrollback and
# CI logs.
#
# Same keys in both namespaces: dev and prod share one AWS account, one set of tables
# and one bucket, so there is nothing to differentiate. Namespace isolation is what
# separates the environments, not credentials.
for ns in dev prod; do
  kubectl -n "$ns" create secret generic recall-secrets \
    --from-env-file="$RECALL_ENV_FILE" \
    --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  echo "  recall-secrets applied in $ns ($(grep -cE '^[A-Za-z_][A-Za-z0-9_]*=' "$RECALL_ENV_FILE") keys)"
done

# --- 6. ArgoCD ------------------------------------------------------------
step "6/8 ArgoCD ($ARGOCD_VERSION)"
kubectl apply -n argocd --server-side --force-conflicts \
  -f "https://raw.githubusercontent.com/argoproj/argo-cd/${ARGOCD_VERSION}/manifests/install.yaml"

step "6b/8 Waiting for ArgoCD to be ready"
# The application controller must be up before the Applications are applied, or they
# sit unprocessed with no status.
kubectl -n argocd rollout status deploy/argocd-server      --timeout=300s
kubectl -n argocd rollout status deploy/argocd-repo-server --timeout=300s
kubectl -n argocd rollout status statefulset/argocd-application-controller --timeout=300s

# --- 7. ArgoCD Applications ----------------------------------------------
step "7/8 ArgoCD Applications"
# Two Applications, one per environment — not the app-of-apps fan-out the reference
# project uses. Recall's four workloads release together, so per-service Applications
# would add indirection without buying independent sync.
#
# Each Application's targetRevision pins which branch it tracks (dev -> dev branch,
# prod -> main), so applying both from a single checkout is correct: the Application
# manifests themselves are branch-independent.
#
# dev  = auto-sync (prune + selfHeal) -> deploys on every push to dev
# prod = manual sync                  -> the promotion gate. It will show OutOfSync
#                                        until you run `argocd app sync recall-prod`.
#                                        That is the gate, not an error.
kubectl apply -f infra/argo/recall-dev.yaml
kubectl apply -f infra/argo/recall-prod.yaml

# --- 8. Summary ----------------------------------------------------------
step "8/8 Summary"

echo "ArgoCD admin password:"
# Once you rotate the password and delete this Secret, the get returns non-zero.
# Guard it so `set -e` does not fail an otherwise-successful bootstrap.
if kubectl -n argocd get secret argocd-initial-admin-secret >/dev/null 2>&1; then
  kubectl -n argocd get secret argocd-initial-admin-secret \
    -o jsonpath='{.data.password}' | base64 -d
  echo
else
  echo "  (initial secret is gone — the password was already changed)"
fi

# Recall's NodePorts answer on EVERY node, control plane included: kube-proxy
# programs the same rules cluster-wide. So the control plane's own public IP is the
# URL to use — and it is the better choice here, because worker IPs change whenever
# the ASG replaces an instance while this one does not.
#
# Read it from IMDS rather than from the Node object: kubelet only reports an
# ExternalIP when it runs with an AWS cloud provider, and this is a plain kubeadm
# cluster, so Node.status.addresses holds InternalIP and Hostname only.
PUBLIC_IP=""
IMDS_TOKEN="$(curl -fsSL -X PUT "http://169.254.169.254/latest/api/token" \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 60" 2>/dev/null || true)"
if [ -n "$IMDS_TOKEN" ]; then
  PUBLIC_IP="$(curl -fsSL -H "X-aws-ec2-metadata-token: ${IMDS_TOKEN}" \
    "http://169.254.169.254/latest/meta-data/public-ipv4" 2>/dev/null || true)"
fi

echo
if [ -n "$PUBLIC_IP" ]; then
  echo "App URLs (NodePort, reachable on any node — this is the control plane's IP):"
  echo "  dev  frontend     http://${PUBLIC_IP}:30300"
  echo "  dev  tutor-agent  http://${PUBLIC_IP}:30800"
  echo "  prod frontend     http://${PUBLIC_IP}:31300   (after argocd app sync)"
  echo "  prod tutor-agent  http://${PUBLIC_IP}:31800"
  echo
  echo "Both ports of each pair must be open: the BROWSER calls tutor-agent directly,"
  echo "deriving its URL as frontendPort+500 (services/frontend/lib/api.ts). A"
  echo "reachable frontend with an unreachable agent renders a page whose every"
  echo "request fails."
else
  echo "Could not read this node's public IP from IMDS."
  echo "  Get it with:  terraform output control_plane_public_ip"
fi

echo
echo "Nodes:"
kubectl get nodes
echo
echo "Applications:"
kubectl -n argocd get applications 2>/dev/null || echo "  (none yet — the controller may still be reconciling)"

step "Bootstrap complete"
echo "Next:"
echo "  1. Workers take ~6-8 min from launch to join (cri-o install + kubeadm join)."
echo "     Watch with:  kubectl get nodes -w"
echo "  2. recall-dev syncs automatically IF the dev branch exists:"
echo "       git push origin implementation:dev"
echo "     Until then it reports ComparisonError — that is the missing branch, not a"
echo "     broken manifest."
echo "  3. prod is manual-sync by design. Promote with:"
echo "       argocd app sync recall-prod"
