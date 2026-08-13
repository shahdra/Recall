# CI/CD

Two workflows.

| Workflow | Trigger | Does |
|---|---|---|
| `test.yaml` | PRs, push to `main` | unit tests (study-mcp, tutor-agent) + integration over real MCP transport |
| `cd.yaml` | push to `dev`/`main` under `services/**`, or manual | builds images, pushes to Docker Hub, bumps the manifest tags |

## One-time setup

`cd.yaml` cannot run without two repository secrets
(**Settings → Secrets and variables → Actions**):

| Secret | Value |
|---|---|
| `DOCKERHUB_USERNAME` | the Docker Hub account owning the `shahdra/recall-*` repos |
| `DOCKERHUB_TOKEN` | a Docker Hub **access token** with Read/Write — *not* the account password |

Create the token at **Docker Hub → Account Settings → Personal access tokens**.
A password works for `docker login` interactively but is the wrong thing to put in CI:
it grants full account access and cannot be scoped or revoked independently.

The four Docker Hub repos already exist and are currently empty.

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

## Notes

- **The reminder manifest is `cronjob.yaml`**, not `reminder.yaml`, so the path is
  special-cased in the workflow. The filename describes what the object is; renaming
  it to fit a formula would be the wrong fix.
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
