# Recall infrastructure runbook

How to build Recall's AWS infrastructure, what it costs, and how to tear it down.

Two layers, one `terraform apply`:

1. **Data plane** — three DynamoDB tables, the uploads bucket, and the reminder SNS
   topic. Nearly free at rest.
2. **Compute** — a VPC and a kubeadm Kubernetes cluster on EC2 (one control plane +
   a worker Auto Scaling Group). **This is what costs money.**

> **Terraform is only half the deploy.** `terraform apply` leaves you with nodes that
> report `NotReady` forever, because nothing has installed a CNI. `infra/k8s/bootstrap.sh`
> is the second half and runs over SSH — see [§5](#5-bootstrap-the-cluster). Terraform
> cannot do it: it would need API-server credentials that do not exist until kubeadm
> has run.

> **Shared AWS account.** 228281126655 is shared with the course — `aws s3 ls` shows
> classmates' state buckets. Every resource here is tagged `Project=recall` and
> prefixed `shahdra-recall-`. Never delete AWS resources by a broad tag filter; the
> destroy procedure in [§7](#7-destroy) is scoped to Recall's own ids.

---

## 0. What gets created

**`Plan: 49 to add, 0 to change, 0 to destroy.`** — verified with
`terraform plan` against the live AWS API on 2026-08-13, provider `hashicorp/aws`
v6.59.0.

Everything carries `Owner=shahdra`, `Project=recall`, `ManagedBy=terraform`,
`TFWorkspace=us-east-1` via the provider's `default_tags`.

### Data plane — 11 resources

| # | Resource | Name |
|---|---|---|
| 3 | DynamoDB tables | `shahdra-recall-Cards` (with `due-index` GSI), `shahdra-recall-Decks`, `shahdra-recall-LearnerProfile` |
| 1 | S3 bucket | `shahdra-recall-us-east-1-uploads-228281126655` |
| 4 | S3 config | public-access-block, SSE-S3, versioning, lifecycle (expire uploads after 90d) |
| 1 | SNS topic | `shahdra-recall-us-east-1-reminders` |
| 1 | SNS topic policy | restricts publish to this account |
| 1 | Guard | `terraform_data.workspace_region_guard` — no AWS resource, just a precondition |

Table names carry no region; the bucket and topic do. Tables are already
region-scoped and one set serves both `dev` and `prod`, so a region in the name would
be noise — whereas an S3 bucket name is globally unique and needs every
disambiguator available.

### VPC — 11 resources

| # | Resource | Detail |
|---|---|---|
| 1 | VPC | `shahdra-recall-us-east-1-vpc`, `10.0.0.0/16`, DNS hostnames + support on |
| 2 | Public subnets | `10.0.101.0/24`, `10.0.102.0/24` — first two AZs, derived with `cidrsubnet()` |
| 1 | Internet gateway | |
| 1 | Route table + 1 route | `0.0.0.0/0` → IGW |
| 2 | Route table associations | one per subnet |
| 3 | Default VPC objects | default security group, route table, and NACL — adopted and locked down by the module, not newly created |

**No private subnets and no NAT gateway.** A NAT would add ~$32/month for no benefit:
every node needs outbound internet (apt, image pulls, Bedrock) *and* inbound NodePort
access, so they belong in public subnets.

### Cluster — 27 resources

| # | Resource | Detail |
|---|---|---|
| 1 | EC2 instance | control plane, `t3.medium`, 30 GiB gp3 encrypted, in the first subnet |
| 1 | Launch template | workers, `t3.medium`, 30 GiB gp3 encrypted, IMDSv2 required |
| 1 | Auto Scaling Group | `shahdra-recall-us-east-1-workers`, desired 1 / min 1 / max 3, spans both subnets |
| 2 | Security groups | one control plane, one worker |
| 7 | Ingress rules | see below |
| 2 | Egress rules | all outbound, both SGs |
| 2 | IAM roles | control plane, worker |
| 6 | Role policy attachments | EBS CSI + ECR + SSM Core on each role |
| 3 | Inline role policies | SSM `PutParameter` (control plane) / `GetParameter` (worker), plus the **app policy** on the worker role — DynamoDB, S3, SNS, Bedrock, all named ARNs ([§4](#4-the-app-env-file--no-aws-keys-needed)) |
| 2 | Instance profiles | one per role |

The **AMI is looked up, not hard-coded** — newest Ubuntu 22.04 LTS from Canonical in
whatever region the provider points at, so the module works in any region unchanged.

**The 7 ingress rules:**

| Port(s) | Source | On |
|---|---|---|
| 22 | `ssh_ingress_cidr` | both SGs (2 rules) |
| 6443 | `ssh_ingress_cidr` | control plane — the API server, for `kubectl` from your laptop |
| all | `10.0.0.0/16` | both SGs (2 rules) — covers kubelet, etcd, and Calico VXLAN without enumerating ports, which is where CNI setups silently break |
| 30300–30800 | `0.0.0.0/0` | workers — dev frontend + tutor-agent |
| 31300–31800 | `0.0.0.0/0` | workers — prod frontend + tutor-agent |

**Why both ports of each pair are open:** the browser calls tutor-agent *directly*,
deriving its URL client-side as `frontendPort + 500`
(`services/frontend/lib/api.ts`). Opening only the frontend port renders a working
page whose every API request fails. **study-mcp is deliberately not exposed** — it is
ClusterIP, reached only by tutor-agent inside the cluster, and publishing it would
expose an unauthenticated tool API.

### Created OUTSIDE Terraform

These exist after a full deploy but Terraform does not manage them, which matters at
destroy time:

| Thing | Created by | Destroyed by Terraform? |
|---|---|---|
| SSM parameter `/recall/shahdra-recall-us-east-1/join-command` | control-plane user-data | **No** — delete by hand ([§7](#7-destroy)) |
| `recall-secrets` Kubernetes Secret | `bootstrap.sh` | N/A (dies with the cluster) |
| Any EBS volume from a PVC | EBS CSI controller | **No** — orphaned, keeps billing ([§7](#7-destroy)) |
| Calico / ArgoCD / app workloads | `bootstrap.sh` + ArgoCD | N/A (dies with the cluster) |

---

## 1. Cost, and the two things that will bite you

**Roughly $0.07/hour ≈ $1.70/day ≈ $50/month** if left running: two `t3.medium`
instances (~$0.0416/hr each in us-east-1) plus 60 GiB of gp3 (~$4.80/month). The data
plane is on-demand DynamoDB and a nearly-empty bucket — cents.

**A stopped control plane is unrecoverable.** `kubeadm init` bakes the instance's
public IP into the API server certificate's SANs. Stop the instance and it comes back
with a *different* public IP, which fails TLS verification on every `kubectl` call
and on every worker's join. There is no fix short of `kubeadm reset` and re-init.

**The course budget keeper will stop your instances.** A Lambda
(`aws-learning-budget-keeper-function`) stops **every** EC2 instance in this account
at **16:00 and 00:00 daily**. Combined with the point above: if you leave the cluster
up past 16:00, expect to destroy and re-apply rather than start it again.

Practical consequences:

- **Do not "pause overnight" by stopping instances.** `terraform destroy` and
  re-apply. A fresh cluster is ~15 minutes end to end; recovering a stopped one is
  not possible.
- **Apply the same day you demo**, ideally within a few hours of it.
- To stop only the *worker* billing while keeping the control plane's certificate
  valid: `terraform apply -var worker_desired_capacity=0`. The control plane still
  bills and the budget keeper still stops it, so this is a partial measure at best.

---

## 2. One-time: prerequisites

### 2a. The state bucket

`main.tf`'s backend names `shahdra-recall-tfstate-228281126655`. **It must exist
before `terraform init`** — backend blocks permit no variables or interpolation, so
Terraform cannot create the bucket that holds its own state.

```bash
aws s3 mb s3://shahdra-recall-tfstate-228281126655 --region us-east-1

# Versioning is what makes a corrupted or truncated state recoverable.
aws s3api put-bucket-versioning \
  --bucket shahdra-recall-tfstate-228281126655 \
  --versioning-configuration Status=Enabled

# State contains resource ids and ARNs; never let it be public.
aws s3api put-public-access-block \
  --bucket shahdra-recall-tfstate-228281126655 \
  --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

### 2b. The SSH key pair

`ssh_key_name` names an EC2 key pair that must **already exist**. Terraform does not
create it: the private half would land in state, and state is not a secret store.

```bash
aws ec2 create-key-pair --key-name shahd-key \
  --query KeyMaterial --output text > ~/.ssh/shahd-key.pem
chmod 400 ~/.ssh/shahd-key.pem
```

If you already have the `.pem`, **verify it actually matches AWS's record** before
applying — a mismatch is only discovered when SSH fails, by which point the cluster
is built and unbootstrappable:

```bash
ssh-keygen -y -f ~/.ssh/shahd-key.pem
aws ec2 describe-key-pairs --key-names shahd-key --include-public-key \
  --query 'KeyPairs[0].PublicKey' --output text
```

The two must print the same `ssh-rsa AAAA...` string.

---

## 3. Apply

```bash
cd infra/terraform
terraform init

# The workspace names the REGION, not the environment.
terraform workspace select us-east-1   # first time: terraform workspace new us-east-1

cp tfvars/us-east-1.tfvars.example tfvars/us-east-1.tfvars
# Edit it if your ssh_key_name differs from "shahd-key".

terraform plan -var-file=tfvars/us-east-1.tfvars
```

Expected: **`Plan: 49 to add, 0 to change, 0 to destroy.`**

**The `0 to change, 0 to destroy` half matters more than the 49.** In a shared
account, any line that is not `will be created` means this config is about to touch
something that is not Recall's — stop and read it before going further.

If the workspace and `region` disagree, the plan fails on a precondition rather than
building a second, misnamed set of resources:

```
Workspace 'us-west-2' does not match region 'us-east-1'.
Run: terraform workspace select us-east-1
```

Then:

```bash
terraform apply -var-file=tfvars/us-east-1.tfvars
```

**~3 minutes.** The EC2 instance returns as soon as it is *running*, not when it is
*ready* — user-data then spends ~6–8 minutes installing cri-o, kubeadm, and running
`kubeadm init`. Watch it:

```bash
ssh -i ~/.ssh/shahd-key.pem ubuntu@$(terraform output -raw control_plane_public_ip) \
  'sudo tail -f /var/log/user-data.log'
```

Wait for `=== control-plane bootstrap COMPLETE ===`. Terraform's useful outputs:

```bash
terraform output ssh_command               # paste-ready SSH
terraform output fetch_kubeconfig_command  # paste-ready scp
terraform output recall_urls               # where the app will be
```

---

## 4. The app env file — no AWS keys needed

**There are no access keys to mint.** Pods inherit the worker node's IAM role through
the instance metadata service; boto3's default credential chain finds it with no
configuration, and nothing in `services/` reads `AWS_ACCESS_KEY_ID`. The policy is in
`iam.tf`, attached to the node role in `modules/k8s-cluster/main.tf`.

So the env file that becomes the Kubernetes Secret is one line:

```bash
cd /Users/saed/shahd/Recall
grep '^DEEPGRAM_API_KEY=' services/tutor-agent/.env > /tmp/recall.env
```

Check what the pods are permitted to do:

```bash
ROLE=$(terraform output -raw app_iam_role)
aws iam get-role-policy --role-name "$ROLE" --policy-name "$ROLE-app"
```

### Why a node role rather than an IAM user with static keys

The earlier design created a dedicated `-app` IAM user, minted its keys with the CLI,
and loaded them into `recall-secrets`. That scopes access per-workload instead of
per-node, which is genuinely better — but `terraform destroy` deletes the user, so the
keys died on every teardown. Given this project's cost strategy *is*
destroy-and-reapply (§1), that meant re-minting and re-pasting credentials every
cycle, into a GitHub secret and a local env file. Credentials that must be re-copied on
every apply get copied wrong, and a stale key fails at runtime looking like an
application bug.

The node role has nothing to mint, store, or rotate, and AWS issues short-lived
credentials rather than permanent ones.

**What it gives up:** every pod on a worker gets these permissions, not just Recall's
four. Pod-level scoping is IRSA's job, and IRSA needs an OIDC provider that a kubeadm
cluster does not have. On a single-tenant cluster destroyed nightly that costs nothing.

> **Do not put `AWS_ACCESS_KEY_ID` in the env file.** boto3 *prefers* an explicit
> environment variable over the instance role, so a stale key silently shadows a
> working node role and the pods fail with `InvalidClientTokenId` — with the role
> sitting right there. `bootstrap.sh` warns if it finds one.

---

## 5. Bootstrap the cluster

Without this, `kubectl get nodes` shows `NotReady` and nothing schedules.

```bash
CP=$(terraform output -raw control_plane_public_ip)

scp -i ~/.ssh/shahd-key.pem /tmp/recall.env ubuntu@$CP:/tmp/recall.env
ssh -i ~/.ssh/shahd-key.pem ubuntu@$CP \
  "RECALL_ENV_FILE=/tmp/recall.env bash -s" < ../k8s/bootstrap.sh
```

**~5 minutes.** It installs Calico (VXLAN), the EBS CSI driver, the `dev`/`prod`/`argocd`
namespaces, `recall-secrets` in both app namespaces, ArgoCD, and the two ArgoCD
Applications. It is idempotent — safe to re-run if it fails halfway.

Then get a kubeconfig on your laptop:

```bash
eval "$(terraform output -raw fetch_kubeconfig_command)"
export KUBECONFIG=~/.kube/config-recall
kubectl get nodes
```

It writes `~/.kube/config-recall`, not `~/.kube/config`, so it cannot clobber an
existing context.

**The worker takes ~6–8 minutes from launch to appear** (cri-o install, then
`kubeadm join` after polling SSM for the join command). `kubectl get nodes -w` until
it shows `Ready`.

### The dev branch must exist

`recall-dev.yaml` tracks the `dev` branch. Until it exists ArgoCD reports
`ComparisonError` — that is the missing branch, not a broken manifest:

```bash
git push origin implementation:dev
```

`recall-prod` is **manual-sync by design** — the promotion gate. It shows `OutOfSync`
until you run `argocd app sync recall-prod`.

---

## 6. One-time: subscribe to the reminder topic

There is no `aws_sns_topic_subscription` resource. An email subscription only goes
live when the recipient clicks a confirmation link, which Terraform cannot do — the
resource would sit in `PendingConfirmation` forever while state reported it created.

```bash
aws sns subscribe \
  --topic-arn "$(terraform output -raw reminders_topic_arn)" \
  --protocol email \
  --notification-endpoint you@example.com
```

Click the link in the confirmation email, then verify:

```bash
aws sns list-subscriptions-by-topic \
  --topic-arn "$(terraform output -raw reminders_topic_arn)"
```

`SubscriptionArn` still reading `PendingConfirmation` means the link was not clicked,
and the daily digest will publish successfully to nobody.

---

## 7. Destroy

**Order matters.** Doing this out of order leaves orphaned EBS volumes that keep
billing and that `terraform destroy` cannot see.

### 7a. Delete PVCs first — only if you created any

Volumes provisioned by the EBS CSI driver are created by the *controller*, not by
Terraform, so they are invisible to `terraform destroy` and survive it.

Recall's four workloads are stateless (state is in DynamoDB), so there is normally
nothing here. Check anyway:

```bash
kubectl get pvc --all-namespaces
# If any exist:
kubectl delete pvc --all -n <namespace>
```

Wait for the corresponding volumes to disappear from
`aws ec2 describe-volumes --filters Name=status,Values=available` before continuing.

### 7b. Destroy

```bash
cd infra/terraform
terraform destroy -var-file=tfvars/us-east-1.tfvars
```

**~5 minutes.** Read the plan before confirming.

**This deletes all data.** The three DynamoDB tables and every card and review
history in them, plus the uploads bucket and its contents. There is no backup:
`point_in_time_recovery` defaults to `false`.

`prevent_destroy` is **off** on the tables and the bucket, and `force_destroy` is on
for the bucket, specifically so this command works. That means the plan no longer
stops you — **read it.** A `replace` or `destroy` on any of the three tables means
data loss, and a typo in `owner` or `table_name_prefix` produces exactly that plan
(names derive from those variables, and DynamoDB cannot rename in place).

`force_destroy` is required, not cosmetic: versioning is enabled on the uploads
bucket, so even after deleting every object the bucket still holds old versions and
delete markers, and `BucketNotEmpty` would fail the destroy at the very last step.

### 7c. Clean up what Terraform cannot

**The SSM join-command parameter** — written by the control plane at boot, so
Terraform never knew about it:

```bash
aws ssm delete-parameter \
  --name "$(terraform output -raw join_command_ssm_parameter)" \
  --region us-east-1
```

Run this *before* `terraform destroy` if you want the output to still resolve;
otherwise the path is `/recall/shahdra-recall-us-east-1/join-command`.

Harmless if left behind (a SecureString parameter is free at this scale), but it is
stale data in a shared account, and a stale join command naming a dead IP is exactly
what the worker user-data's liveness probe exists to survive.

**Verify nothing of Recall's is left**, scoped to our own tag so a classmate's
resources are never in view:

```bash
aws ec2 describe-instances \
  --filters "Name=tag:Project,Values=recall" "Name=instance-state-name,Values=running,stopped" \
  --query 'Reservations[].Instances[].[InstanceId,State.Name,Tags[?Key==`Name`].Value|[0]]' \
  --output table

aws ec2 describe-volumes \
  --filters "Name=status,Values=available" "Name=tag:Project,Values=recall" \
  --query 'Volumes[].[VolumeId,Size,CreateTime]' --output table
```

> **Never bulk-delete EBS volumes in this account.** It holds 60+ `available` volumes
> belonging to other students. Delete only by the specific ids the
> `Project=recall`-filtered query above returns.

### 7d. What destroy does NOT touch

- **The state bucket** `shahdra-recall-tfstate-228281126655` — it holds the state
  doing the destroying. Delete it manually if you are done for good (`aws s3 rb
  --force`).
- **The `shahd-key` EC2 key pair** — created out-of-band, reusable.
- **The IAM access keys** — deleted along with the user.

---

## Keeping local and production in sync

`scripts/setup-local-dynamodb.sh` creates the same three tables in DynamoDB Local and
says of itself:

> Key schemas here must match `infra/terraform/dynamodb.tf`. If they drift, local runs
> pass while production breaks.

The schema, verified against `services/study-mcp/storage.py`:

| Table | PK | SK | GSI |
|---|---|---|---|
| Cards | `deck_id` (S) | `card_id` (S) | `due-index`: PK `user_id`, SK `due_date`, projection **ALL** |
| Decks | `user_id` (S) | `deck_id` (S) | — |
| LearnerProfile | `user_id` (S) | — | — |

Three load-bearing details:

- **`due_date` is a String, not a Number.** `query_due_cards` (`storage.py:133`)
  filters with `.lte()` and relies on ISO dates sorting lexicographically.
- **Projection must be `ALL`.** `_normalize_card` (`storage.py:89`) reads
  `ease_factor` / `interval_days` / `repetitions` off each returned item; `KEYS_ONLY`
  would turn one Query into N+1 round trips.
- **The GSI partitions on `user_id`, not `due_date`.** `docs/spec.md:143` reads the
  other way; the code is the authority, and `docs/plan.md:823` agrees.

---

## Gotchas

### Data plane

- **A Query against a GSI is authorized against the index ARN, not the table's.**
  `iam.tf` includes `${table.arn}/index/*`. Without that one line the app starts fine,
  decks list fine, and every study session fails with `AccessDeniedException`.
  `terraform output cards_due_index_arn` is the value to check first.
- **Bedrock's `us.` prefix means a cross-region inference profile.** It needs both the
  profile ARN *and* the foundation-model ARN in every region it may route to
  (`us-east-1`, `us-east-2`, `us-west-2` — all three are in `iam.tf`). Granting only
  the calling region fails *intermittently*, when the profile happens to route
  elsewhere.
- **`bedrock-restrict-developers` carries an explicit deny** for models outside its
  eight-model allowlist. An explicit deny always wins, so no policy here can grant
  around it. Both models in `iam.tf` are on the allowlist.
- **Publishing to the topic needs KMS.** It is encrypted with `alias/aws/sns`, so the
  publisher needs `kms:GenerateDataKey`; without it the failure reads like an SNS
  permission problem.

### Cluster

- **`prevent_destroy` cannot be driven by a variable.** Terraform rejects any
  expression in a `lifecycle` block — `Variables may not be used here`. So a
  `-var allow_destroy=true` switch is not possible; the guard is on or off in
  committed code. It is currently **off** on all four data resources, with the
  reasoning in `dynamodb.tf`. `force_destroy` is an ordinary argument and *can* be
  variable-driven if the guard is ever wanted back.
- **Editing a user-data template replaces the instance.** `user_data` runs only on
  first boot, so `user_data_replace_on_change = true` is set. Without it, editing the
  script yields a successful apply that changes nothing on the box. For the control
  plane, replacement means a **new cluster** — every worker must rejoin.
- **`kubernetes_version` must be a MINOR version** (`v1.30`, not `v1.30.4`). It
  selects the `pkgs.k8s.io` apt repository, which is published per minor line; a patch
  string makes the apt source 404 and the boot fails. A validation block catches it at
  plan time.
- **`vpc_cidr` must not overlap `pod_network_cidr`.** Node IPs and pod IPs in the same
  range break routing in ways that look like random pod-to-pod timeouts.
- **Calico must use `encapsulation: VXLAN`, not upstream's `VXLANCrossSubnet`.**
  Cross-subnet sends raw pod-IP packets when two nodes share a subnet, and AWS drops
  those (no VPC route for the pod CIDR, and the ENI source/dest check rejects them).
  `bootstrap.sh` applies the Installation CR inline for exactly this reason.
- **Provider-level `default_tags` do not reach ASG-launched instances.** The ASG
  repeats `Owner`, `Project`, and `Cluster` in `tag { propagate_at_launch = true }`
  blocks, and the launch template repeats `Name`/`Role` in `tag_specifications`. Both
  are needed; neither is redundant.
- **Scaling down does not remove the Node object.** After the ASG terminates a worker,
  `kubectl delete node <name>` is manual. A terminating lifecycle hook plus a Lambda
  would automate it, at the cost of machinery harder to explain than the manual step.
- **`.terraform.lock.hcl` is currently gitignored** (`.gitignore:37`) — flagged, not
  changed. The provider is pinned `~> 6.0` in `main.tf` so an unpinned major bump
  cannot happen silently, but committing the lock file is what pins the exact patch.
  `.gitignore:34` also ignores `*.tfvars` (with an `!*.tfvars.example` exception on
  line 35), which is why the committed file is `tfvars/us-east-1.tfvars.example`.

### Troubleshooting a cluster that does not come up

| Symptom | Check |
|---|---|
| SSH times out | Instance still booting (~1 min), or `ssh_ingress_cidr` does not include your address: `curl -s https://checkip.amazonaws.com` |
| `kubectl` refused on 6443 | `kubeadm init` has not finished. `sudo tail -50 /var/log/user-data.log` on the control plane |
| Nodes `NotReady` | `bootstrap.sh` has not run, or Calico failed: `kubectl -n tigera-operator logs deploy/tigera-operator` |
| Worker never appears | `aws ssm get-parameter --name /recall/shahdra-recall-us-east-1/join-command --with-decryption` — then `sudo tail -50 /var/log/user-data.log` on the worker |
| Pods `CreateContainerConfigError` | `recall-secrets` missing: `kubectl -n dev get secret recall-secrets`. Re-run `bootstrap.sh` |
| Pods `ImagePullBackOff` | Images not pushed yet, or the manifest's tag does not exist. `kubectl -n dev describe pod <name>` |
| ArgoCD `ComparisonError` | The `dev` branch does not exist: `git push origin implementation:dev` |
| App loads, every request fails | tutor-agent's NodePort unreachable. Both ports of the pair must be open — the browser calls it directly at `frontendPort + 500` |
| tutor-agent reports `mcp_tools: 0` | It discovers tools once at startup with no retry. study-mcp was down when it booted: `kubectl -n dev rollout restart deploy/tutor-agent` |
