---
name: adwf-local-git-mirror
description: Restore a full local Git workspace from an exact GitHub SHA through the GitHub Connector and a short-lived GitHub Actions transport when direct git/DNS/egress is unavailable.
---

# ADWF Local Git Mirror

Use this skill when an AI execution environment can access a repository through a GitHub connector but ordinary `git clone`, `git fetch`, or DNS/HTTPS egress to `github.com` is unavailable.

## Goal

Materialize a real local `.git` repository for an exact provider-side source SHA without weakening ADWF governance. The local workspace is for analysis, editing, generators, tests, `git diff`, `merge-base`, and `worktree`. GitHub remains the source of truth and remote mutations continue through an authorized connector when direct Git transport is unavailable.

## Required inputs

- repository in `owner/name` form;
- source branch/ref to materialize;
- exact source commit SHA read back from GitHub immediately before bootstrap;
- a GitHub connector capable of branch/file writes, Actions run/artifact reads, and artifact download;
- local shell with `git` and Python 3.

## Invariants

1. Never replace an unknown source SHA with `latest`, `HEAD`, or an inferred value.
2. Never put PATs, OAuth tokens, connector credentials, or secrets in a branch, workflow, issue, prompt, artifact, or log.
3. Prefer direct Git when it works. Do not spend an Actions run merely for convenience.
4. The transport branch is disposable and must never become the canonical product branch.
5. The workflow must use read-only repository contents permission in the preferred artifact lane.
6. Artifact bytes are untrusted until SHA-256, manifest, bundle verification, and exact source SHA checks pass.
7. Local test PASS is not a substitute for provider-side exact-head CI or owner gates.
8. If any verification is ambiguous, fail closed and keep GitHub state unchanged.

## Workflow

### 1. Probe the direct lane

Run a short bounded probe such as `git ls-remote <clone-url> HEAD` or an equivalent DNS/HTTPS check. If direct Git works, use the ordinary checkout flow and stop this skill.

Classify a deterministic DNS/egress failure once. Do not repeatedly retry the same blocked transport.

### 2. Read provider truth

Through the GitHub connector, read:

- repository default branch;
- requested source branch/ref;
- exact source SHA;
- current permissions needed for the temporary branch and artifact readback.

Record the exact SHA in the execution notes. All later verification is bound to it.

### 3. Create a disposable transport branch

Create a unique branch from the exact source SHA, for example:

`agent/local-mirror-<work-id>-<date>-<nonce>`

Do not create it from a stale local ref.

### 4. Materialize the preferred workflow template

Read `resources/bootstrap-workflow.yml.template`, replace all required placeholders, and create the resulting file on the disposable branch at:

`.github/workflows/adwf-local-mirror-bootstrap.yml`

Required placeholders:

- `__BOOTSTRAP_BRANCH__`
- `__SOURCE_SHA__`
- `__SOURCE_BRANCH__`
- `__ARTIFACT_NAME__`

The push that creates this file is the trigger. The workflow creates a bundle for a synthetic `adwf-source` ref pointing to the exact source SHA, writes a manifest and checksum, and uploads a one-day artifact.

### 5. Read back the Actions run

Find the run by all of these values, not only by display title:

- workflow path;
- disposable branch;
- transport commit SHA;
- event `push`.

Require `status=completed` and `conclusion=success`.

### 6. Download the artifact through the connector

Fetch the artifact named by `__ARTIFACT_NAME__` and download its ZIP bytes through the connector. Prefer this binary lane because it avoids repository bloat and needs no workflow write token.

If the connector cannot return artifact bytes, use `resources/bootstrap-workflow-fallback.yml.template` in a new disposable transport branch/run. That fallback publishes Base64 chunks only on the disposable branch and is intentionally separate because it requires `contents: write`.

### 7. Materialize locally

Run:

```bash
python skills/adwf-local-git-mirror/scripts/materialize_bundle.py \
  --artifact-zip <artifact.zip> \
  --target <local-workspace> \
  --source-sha <exact-source-sha> \
  --source-branch <source-branch> \
  --remote-url https://github.com/<owner>/<repo>.git
```

The materializer must fail if the ZIP is malformed, files are missing, the checksum differs, the manifest source SHA differs, `git bundle verify` fails, the requested commit is absent, or final `HEAD` differs.

### 8. Verify the local Git capability

At minimum run:

```bash
git -C <local-workspace> fsck --full --no-dangling
git -C <local-workspace> rev-parse HEAD
git -C <local-workspace> log -1 --oneline
git -C <local-workspace> status --short
```

For ADWF itself also run the relevant structural validators and `python .adwf/adwf.py self-test` when the work unit requires full verification.

### 9. Work locally, mutate remotely through the connector

Local Git is authoritative only for the materialized snapshot and local changes. When direct `git push` is unavailable, publish intended changes through connector Git/contents APIs to an authorized branch, then read back the remote SHA and run provider-side CI.

### 10. Cleanup

Artifacts use minimal retention. Treat transport branches as disposable infrastructure and remove them when the available GitHub interface supports safe branch deletion. If the connector cannot delete refs, do not force-repoint `main` or another protected ref as a workaround; record the transport branch for later cleanup.

## Output contract

Report only concise handoff facts:

- source repository/ref/SHA;
- transport branch/run/artifact IDs;
- artifact SHA verification result;
- local workspace path;
- local HEAD and branch;
- `git fsck` result;
- relevant test result;
- whether direct remote Git remains unavailable;
- any cleanup item.

Do not expose hidden reasoning or credentials.
