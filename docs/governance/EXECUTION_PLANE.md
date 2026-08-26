# Execution Plane Boundary

## Decision

ADWF separates reproducible engineering evidence from environment-bound runtime evidence.

**Reproducible engineering evidence runs in GitHub-hosted CI. A private owner workstation or home execution node is runtime-only and has no CI, required-check, merge, or self-certification authority.**

The machine-readable source is `.adwf/execution-plane.json`.

## GitHub-hosted CI authority

The following evidence classes are canonical only when produced by GitHub-hosted CI on the exact subject revision:

- static analysis;
- unit tests;
- reproducible integration tests;
- schema and documentation validation;
- build/package integrity;
- security scans;
- platform smoke on GitHub-hosted operating systems;
- governance and trusted gates.

Canonical `adwf-*` workflows may use only the explicitly approved GitHub-hosted runner labels. `self-hosted`, owner-PC labels, unknown labels, or private runners are fail-closed violations.

A local run can be useful for debugging, but it is never required ADWF CI evidence and cannot satisfy protected-merge authority.

## Private runtime execution node

A private execution node is allowed only when the property under test cannot be reproduced faithfully in GitHub-hosted CI. Examples include:

- private/local network topology and private service endpoints;
- OS integration tied to the actual owner machine;
- local credential-store behavior;
- GUI/runtime integration;
- physical devices;
- other environment-bound runtime facts.

The node is not registered as a runner for a public repository and is not required for ADWF CI availability. If required private runtime evidence has not run, the state is `NOT_VERIFIED`; GitHub CI must not synthesize a PASS.

## PNCC application

For PNCC, GitHub-hosted CI owns all reproducible engineering checks. The private Windows node is reserved for runtime facts such as the real Proxifier/PuTTY/DPAPI/WinForms environment, private VPS path, local network behavior, and the real `127.0.0.1:1081` integration contract.

This separation keeps CI reproducible and owner-independent while preserving a distinct Runtime Truth plane.
