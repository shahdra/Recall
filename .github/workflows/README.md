# CI/CD

Three workflows.

| Workflow | Trigger | Does |
|---|---|---|
| `test.yaml` | PRs, push to `main` | unit tests (study-mcp, tutor-agent) + integration over real MCP transport |
| `cd.yaml` | push to `dev`/`main` under `services/**`, or manual | builds images, pushes to Docker Hub, bumps the manifest tags |
| `cluster.yaml` | manual only | `terraform apply` the VPC + kubeadm cluster, then bootstrap it (Calico, ArgoCD, secrets) |

## One-time setup

### For `cd.yaml`

| Secret | Value |
|---|---|
| `DOCKERHUB_USERNAME` | the Docker Hub account owning the `shahdra/recall-*` repos |
| `DOCKERHUB_TOKEN` | a Docker Hub **access token** with Read/Write — *not* the account password |

Create the token at **Docker Hub → Account Settings → Personal access tokens**.
A password works for `docker login` interactively but is the wrong thing to put in CI:
it grants full account access and cannot be scoped or revoked independently.

### For `cluster.yaml`

| Secret | Value |
|---|---|
| `AWS_ACCESS_KEY_ID` | an IAM key that can create VPC/EC2/IAM/DynamoDB/S3/SNS resources |
| `AWS_SECRET_ACCESS_KEY` | its secret |
| `SSH_PRIVATE_KEY` | the **full contents** of `~/.ssh/shahd-key.pem`, including the BEGIN/END lines |
| `RECALL_ENV` | `KEY=VALUE` lines: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `DEEPGRAM_API_KEY` |

`RECALL_ENV` becomes the `recall-secrets` Kubernetes Secret in *both* `dev` and `prod`.
Its AWS pair should be the `shahdra-recall-us-east-1-app` user's keys — not the
provisioning keys above, which are far more privileged. Mint them with:

```bash
aws iam create-access-key --user-name shahdra-recall-us-east-1-app
```

> Do **not** put `RECALL_DEMO_MODE` in `RECALL_ENV`. It lands in both namespaces, and
> the ⏩ day offset is process-wide — demo mode in prod shifts the review clock for
> every user. `bootstrap.sh` rejects it outright. Dev gets it from
> `infra/k8s/dev/configmap.yaml` instead.

Setting `AWS_*` here means GitHub Actions can create billable AWS resources on a
manual dispatch. That is a real decision, not a formality — the alternative is running
`terraform apply` from your laptop per `infra/terraform/RUNBOOK.md`.

## What the tags look like

Full 40-character commit SHAs — `shahdra/recall-frontend:cbbea56...`, never `latest`.

Two reasons. Every build is traceable to exactly one commit, and a rollback is just
pointing a manifest at an older SHA rather than rebuilding. `latest` would also break
Kubernetes rollouts: the tag doesn't change, so the Deployment spec doesn't change, so
nothing restarts.

> The manifests currently carry a hand-written 7-character tag (`6689d49`) that was
> never pushed to Docker Hub. The first `cd.yaml` run overwrites all eight with real
> 40-char SHAs. Until then, pods will `ImagePullBackOff` — that is the expected state,
> not a manifest bug.

## The two branches behave differently

```
push to dev                          push to main
  |                                    |
  build + push image                   build + push image
  |                                    |
  bump infra/k8s/dev/<svc>/            bump infra/k8s/prod/<svc>/
  |                                    |
  commit DIRECTLY to dev               push side branch, open a PR
  |                                    |
  recall-dev auto-syncs (~1 min)       you merge the PR
                                       |
                                       argocd app sync recall-prod
```

Dev is one action away from live; prod takes two — merging the PR, and the manual
ArgoCD sync. `main` is currently unprotected so CI *could* push to it directly, but
the PR path keeps working unchanged the day protection is enabled.

`main` is a trigger at all because a hotfix PR'd straight to main, bypassing dev,
would otherwise get no image build — leaving the prod manifest referencing a tag that
was never pushed.

## First run

The manifests reference an unbuilt tag, so nothing works until images exist. Build all
four:

**Actions → CD → Run workflow**, leaving *Build every service* checked.

A manual dispatch ignores the changed-paths filter, because the previous commit may be
unrelated to any service. Normal pushes build only what changed — the frontend is a
multi-stage Next.js build and rebuilding it for a study-mcp change wastes minutes.

## Provisioning the cluster

**Actions → Provision Cluster → Run workflow.** Two sequential jobs, ~15 minutes:

1. **provision** — `terraform init` / workspace select / plan / apply. The plan is
   printed into the run summary before the apply, so the run log is the record of what
   changed. It applies the *saved* plan, so what runs is exactly what was shown.
2. **bootstrap** — polls for `/var/lib/cloud/control-plane-ready`, stages
   `RECALL_ENV` onto the node, pipes `infra/k8s/bootstrap.sh` in over SSH, then shreds
   the staged credentials from both the runner and the node.

Inputs: `region` (only `us-east-1` — see below), `worker_desired_capacity` (default 1),
and `run_bootstrap` (default true; uncheck to provision bare infrastructure).

**Why this over the manual runbook steps.** `RUNBOOK.md` has you SSH in and
`tail -f /var/log/user-data.log` waiting for a completion line — a human watching a log
as the synchronization primitive. sshd accepts connections *minutes* before
`kubeadm init` finishes, so anything keyed on "can I SSH yet" races ahead and fails
against an API server that isn't up. This polls the sentinel file instead. The runbook
stays valid for hand-driving; this is the reliable path.

**Region choice is deliberately one option.** Only `us-east-1` has a committed
`tfvars/*.example`. Adding a region is a new example file plus one line in the
workflow's `options:` — offering a region with no tfvars would fail at plan time with a
confusing "no such file" error.

**There is no destroy job**, on purpose. Teardown is irreversible — it deletes the
three DynamoDB tables and every card and review history in them, with
`point_in_time_recovery` off and no backup. A one-click destroy button beside a
one-click provision button is one mis-click from that. Teardown stays manual:
`RUNBOOK.md` §7.

> **This workflow spends money.** ~$0.07/hour. The course budget keeper
> (`learning-budget-keeper-schedule`, `cron(0 13,21 * * ? *)` = 16:00 and 00:00 local)
> stops every EC2 instance in the account, and a stopped kubeadm control plane is
> **unrecoverable** — its public IP is baked into the API server cert SANs. So the
> recovery path is re-running this workflow, never starting the instance. Destroy when
> you're done; a stopped cluster still bills for EBS.

## Notes

- **The reminder manifest is `cronjob.yaml`**, not `reminder.yaml`, so the path is
  special-cased in the workflow. The filename describes what the object is; renaming
  it to fit a formula would be the wrong fix.
- **`cluster.yaml` pins `REPO_REF=dev`** for the checkout `bootstrap.sh` makes *on the
  node*, so dispatching from a feature branch can't apply that branch's ArgoCD
  Application manifests. It doesn't constrain which branch each environment deploys
  from — `recall-dev.yaml` pins `targetRevision: dev` and `recall-prod.yaml` pins
  `main`.
- **The tfvars file is gitignored** (`.gitignore:34`), so a CI checkout has only the
  `.example`. `cluster.yaml` copies it before planning; without that the plan fails
  with "no such file". Safe because the example holds no secrets — every value is a
  region, instance type, or CIDR.
- **No build args.** The frontend resolves the agent URL at *runtime*
  (`services/frontend/lib/api.ts` derives it as `frontendPort + 500`), so one image
  serves both dev and prod. A `NEXT_PUBLIC_*` build arg would inline the value at
  build time and force two separate images.
- **Bump commits carry `[skip ci]`** and only touch `infra/k8s/**`, which the paths
  filter excludes — two independent guards against a build loop.
- **The matrix runs `max-parallel: 1`.** Every leg commits to the same branch;
  serialising removes the push race instead of relying on the rebase-retry to survive
  it.
- **`docker/setup-buildx-action` is required**, not optional. The runner's default
  buildx driver is `docker`, which cannot export a layer cache — builds fail with
  *"Cache export is not supported for the docker driver"*.
