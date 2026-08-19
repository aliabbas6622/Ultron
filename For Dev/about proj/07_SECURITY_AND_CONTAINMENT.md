# Security and Containment

## Policy Boundary

The LLM can propose.

It cannot authorize itself.

```text
LLM
 -> ActionIntent
 -> native PolicyEngine
 -> allow | constrain | ask | sandbox | deny
```

## Trust and Provenance

All external data carries:
- source
- trust class
- sensitivity
- provenance

Examples:

```text
owner instruction     -> authoritative
local system config   -> trusted
webpage               -> untrusted
email                 -> untrusted
MCP tool description  -> untrusted
MCP tool output       -> untrusted
downloaded skill      -> hostile until reviewed
```

Tool-description poisoning must be treated as prompt injection.

## ContainmentPolicy

Long-term multi-device expansion must obey a non-negotiable containment invariant:

> ULTRON may never install, activate, replicate, or migrate itself onto a new host without explicit per-device human pairing and approval.

Discovery is not admission.

Compatibility is not authority.

The model cannot change this rule.

## Future Pairing Requirements

When multi-device support is implemented:

- each node has a cryptographic identity/keypair
- pairing requires explicit human action
- revocation removes hosting rights
- hosting authority is separate from instruction authority
- permissions are least-privilege
- node trust can be revoked independently
- no ambient auto-deploy based on proximity/network discovery

V0.x does not need to implement the distributed fabric yet.

## MCP Threat Model

An MCP server must be treated as an untrusted capability provider.

Track:
- server identity
- capability allowlist
- schema/tool-description hash
- version changes
- permissions
- trust level

A changed tool description must not silently gain additional authority.

## Side-Effect Safety

Use:
- action IDs
- idempotency keys
- risk classification
- expected state change
- rollback metadata where possible

## Failure Controls

Use:
- bounded retries
- deadlines
- cancellation
- circuit breakers
- health checks
- fallback routes
- subprocess isolation
