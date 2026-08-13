# Recall infrastructure runbook

Operational steps for `infra/terraform/`. Everything Terraform *cannot* do is here:
creating the state bucket, minting the app's access keys, and confirming the email
subscription.

> **`terraform apply` needs instructor approval before it is run.** The AWS account
> (228281126655) is shared with the course — see `docs/plan.md:827`. Everything
> below up to and including `plan` is safe and read-only.

---

## 0. What this creates

13 resources, all tagged `Project=recall` and prefixed `shahdra-recall-`:

| Resource | Name |
|---|---|
| DynamoDB table | `shahdra-recall-Cards` (+ `due-index` GSI) |
| DynamoDB table | `shahdra-recall-Decks` |
| DynamoDB table | `shahdra-recall-LearnerProfile` |
| S3 bucket | `shahdra-recall-us-east-1-uploads-228281126655` |
| SNS topic | `shahdra-recall-us-east-1-reminders` |
| IAM user + inline policy | `shahdra-recall-us-east-1-app` |
| S3 config | public-access-block, SSE, versioning, lifecycle |
| Guard | `terraform_data.workspace_region_guard` (no AWS resource) |

Table names carry no region; the bucket and topic do. Tables are already
region-scoped and one set serves both `dev` and `prod`, so the region would be
noise — whereas an S3 bucket name is globally unique and needs every
disambiguator it can get.

---

## 1. One-time: create the state bucket

The backend block in `main.tf` names `shahdra-recall-tfstate-228281126655`.
**This bucket must exist before `terraform init`.** Backend blocks permit no
variables or interpolation, so Terraform cannot create the bucket that holds its
own state — a chicken-and-egg problem solved out-of-band:

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

**Alternative:** reuse the existing `shahdra-polyai-tfstate-228281126655` bucket by
changing only the `bucket` line in `main.tf`'s backend block. The `key` is already
`recall.tfstate`, distinct from the cluster's, so the two states cannot collide.
One less bucket in a shared account, at the cost of coupling two projects' state
lifetimes.

---

## 2. Plan

```bash
cd infra/terraform
terraform init

# The workspace names the REGION, not the environment.
terraform workspace new us-east-1   # first time; else: terraform workspace select us-east-1

cp tfvars/us-east-1.tfvars.example tfvars/us-east-1.tfvars
terraform plan -var-file=tfvars/us-east-1.tfvars
```

Expected: **`Plan: 13 to add, 0 to change, 0 to destroy.`**

The `0 to change, 0 to destroy` half matters more than the 13. In a shared account,
any line that is not `will be created` means this config is about to touch
something that is not Recall's — stop and re-read it before going further.

If the workspace and `region` disagree, the plan fails on a precondition rather
than building a second, misnamed set of tables:

```
Workspace 'us-west-2' does not match region 'us-east-1'.
Run: terraform workspace select us-east-1
```

### Planning without the state bucket

To validate against the live AWS API before creating any bucket, copy the config to
a scratch directory and strip the backend block so state stays local:

```bash
rm -rf /tmp/recall-plan && mkdir -p /tmp/recall-plan
cp infra/terraform/*.tf /tmp/recall-plan/
cp infra/terraform/tfvars/us-east-1.tfvars.example /tmp/recall-plan/plan.tfvars
cd /tmp/recall-plan
python3 -c "
import re
s=open('main.tf').read()
open('main.tf','w').write(re.sub(r'\n  backend \"s3\" \{.*?\n  \}\n','\n',s,flags=re.S))"
terraform init && terraform workspace new us-east-1
terraform plan -var-file=plan.tfvars
```

This is how the 13-resource plan above was verified. It creates nothing.

---

## 3. Apply — **requires instructor approval**

```bash
terraform apply -var-file=tfvars/us-east-1.tfvars
terraform output -json > /tmp/recall-outputs.json
```

---

## 4. One-time: app access keys

Terraform creates the IAM user but **not** its access keys, deliberately.
`aws_iam_access_key` writes the secret key into state in plaintext, and that state
lives in an S3 bucket in a shared account. Mint them with the CLI:

```bash
aws iam create-access-key --user-name shahdra-recall-us-east-1-app
```

Load the pair straight into the Kubernetes Secret — see
`infra/k8s/<env>/secret.example.yaml`. Do not write them to a file in the repo.

To rotate: create the second key, update the Secret, restart the pods, confirm
healthy, then delete the old key. IAM allows two keys per user precisely so
rotation needs no downtime.

---

## 5. One-time: subscribe to the reminder topic

There is no `aws_sns_topic_subscription` resource. An email subscription only goes
live when the recipient clicks a confirmation link, which Terraform cannot do — the
resource would sit in `PendingConfirmation` forever while state reported it created.

```bash
aws sns subscribe \
  --topic-arn "$(terraform output -raw reminders_topic_arn)" \
  --protocol email \
  --notification-endpoint you@example.com
```

Then click the link in the confirmation email. Verify:

```bash
aws sns list-subscriptions-by-topic \
  --topic-arn "$(terraform output -raw reminders_topic_arn)"
```

`SubscriptionArn` still reading `PendingConfirmation` means the link was not
clicked, and the daily digest will publish successfully to nobody.

---

## 6. Wire the outputs into Kubernetes

The bucket and topic names are not guessable — they embed the account id and
region. Copy them from Terraform rather than typing them:

```bash
terraform output -json \
  | jq -r '.configmap_env.value | to_entries[] | "\(.key)=\(.value)"'
```

Those six lines are the `data:` block of `infra/k8s/<env>/configmap.yaml`.

---

## Keeping local and production in sync

`scripts/setup-local-dynamodb.sh` creates the same three tables in DynamoDB Local
and says of itself:

> Key schemas here must match `infra/terraform/dynamodb.tf`. If they drift, local
> runs pass while production breaks.

The schema, verified against `services/study-mcp/storage.py`:

| Table | PK | SK | GSI |
|---|---|---|---|
| Cards | `deck_id` (S) | `card_id` (S) | `due-index`: PK `user_id`, SK `due_date`, projection **ALL** |
| Decks | `user_id` (S) | `deck_id` (S) | — |
| LearnerProfile | `user_id` (S) | — | — |

Three details that are load-bearing:

- **`due_date` is a String, not a Number.** `query_due_cards`
  (`storage.py:133`) filters with `.lte()` and relies on ISO dates sorting
  lexicographically.
- **Projection must be `ALL`.** `_normalize_card` (`storage.py:89`) reads
  `ease_factor` / `interval_days` / `repetitions` off each returned item;
  `KEYS_ONLY` would turn one Query into N+1 round trips.
- **The GSI partitions on `user_id`, not `due_date`.** `docs/spec.md:143` reads the
  other way; the code is the authority, and `docs/plan.md:823` agrees.

## Gotchas

- **A Query against a GSI is authorized against the index ARN, not the table's.**
  `iam.tf` includes `${table.arn}/index/*`. Without that one line the app starts
  fine, decks list fine, and every study session fails with
  `AccessDeniedException`. `terraform output cards_due_index_arn` is the value to
  check first.
- **Bedrock's `us.` prefix means a cross-region inference profile.** It needs both
  the profile ARN *and* the foundation-model ARN in every region it may route to
  (`us-east-1`, `us-east-2`, `us-west-2` — all three are in `iam.tf`). Granting only
  the calling region fails *intermittently*, when the profile happens to route
  elsewhere.
- **`bedrock-restrict-developers` carries an explicit deny** for models outside its
  eight-model allowlist. An explicit deny always wins, so no policy here can grant
  around it. Both models in `iam.tf` are on the allowlist.
- **Publishing to the topic needs KMS.** It is encrypted with `alias/aws/sns`, so
  the publisher needs `kms:GenerateDataKey`; without it the failure reads like an
  SNS permission problem.
- **The three tables and the bucket carry `prevent_destroy`.** Removing a learner's
  review history should take a deliberate edit, not a stray `terraform destroy`.
  To delete on purpose: drop the `lifecycle` block, apply, then destroy.
- **`.terraform.lock.hcl` is currently gitignored** (`.gitignore:36`) — flagged, not
  changed. The provider is pinned `~> 6.0` in `main.tf` so an unpinned major bump
  cannot happen silently, but committing the lock file is what pins the exact
  patch. The reference project commits its lock file.
