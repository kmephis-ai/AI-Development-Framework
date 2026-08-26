# External Thin Consumer Binding v1

`EXTERNAL_CONSUMER_BINDING` — consumer-owned proof/configuration contract for projects that use ADWF Core from a separate exact-SHA framework checkout. It exists for heterogeneous consumers that must conform to ADWF semantic contracts without receiving a filesystem clone of the managed `.adwf` package.

The binding is intentionally weaker than a full `CONSUMER_NATIVE` installation in one dimension and stricter in another. It does not claim managed-surface adoption, installed-byte ownership, provider gate success, deployment state, runtime health or product health. It does bind the framework repository and exact source SHA, consumer repository/default branch, detected Project Pack ID/digest, and native PR/main check identities.

The validator is read-only. `mutation_authority` is fixed to `NONE_BINDING_IS_PROOF_ONLY`, monetary budget is fixed to `$0`, and secrets are forbidden. A valid contract returns `VERIFIED_CONTRACT` while native gate evidence and runtime evidence remain `NOT_VERIFIED`.

A thin consumer must not contain an adopted `.adwf` managed surface. The framework checkout and the consumer checkout are separate roots. Framework repository/SHA, consumer repository, Project Pack detection/digest, self-seal, branch syntax and native gate declarations are verified fail-closed. Floating or substituted framework identity, pack substitution, binding tamper, reserved/circular ADWF check names, duplicate gate names, marker symlinks and mixed full-adoption/thin topology block validation.

This v1 contract does not route CI, query GitHub provider checks, write consumer files, modify workflows/rulesets, execute Project Pack commands, or control physical/runtime systems. Provider-native gate readback/delegation is a later bounded capability. A downstream consumer transaction may materialize the sealed consumer-owned binding only after the framework revision implementing this contract is protected-merged and post-merge verified.
