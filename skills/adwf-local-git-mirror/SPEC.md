# Contract: adwf-local-git-mirror

Managed migration of DEVENV-001. Existing `SKILL.md`, templates and materializer remain the proven implementation.

## Purpose
Restore a real local Git repository at an exact provider-read SHA when direct Git/DNS/egress is unavailable but the GitHub Connector and GitHub Actions artifact readback are available.

## Declared effects
- shell/process execution: yes, for local `git`;
- filesystem: write, for safe artifact extraction and workspace materialization;
- network in package scripts: none; provider interaction is performed by the host/Connector workflow described by the Skill;
- secrets: none;
- declared external domain in documentation: `github.com`.

## Safety
Artifact bytes are untrusted until checksum, manifest, bundle verification, exact source SHA and `git fsck` pass. Local PASS never substitutes provider exact-head gates.
