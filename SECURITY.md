# Security Policy

CASEFILE processes insurance claims, so security reports are taken seriously and
handled privately.

## Supported versions

The `main` branch is the supported line. Fixes land there first; there are no
maintained release branches yet.

## Reporting a vulnerability

**Do not open a public issue.**

Use GitHub's private reporting flow:
<https://github.com/Abhirupmandal/Casefile/security/advisories/new>

Please include:

- affected component (agent, tool, API route, persistence layer, …)
- a reproduction or proof of concept
- the impact you believe it has, and any suggested mitigation

You can expect an acknowledgement within 5 business days and a status update
within 10. Fixes are released on `main` with a commit message that credits the
reporter unless you ask to stay anonymous.

## Scope

In scope:

- agent-to-tool authorization bypasses (an agent reaching a tool outside its
  matrix entry in `docs/tools.md`)
- prompt injection that changes a decision or escapes prompt quarantine
- approval-gate or audit-trail tampering
- secrets exposure through logs, API errors or traces
- authentication or authorization flaws in the REST API
- request smuggling, SSRF, or path traversal through intake fields

Out of scope:

- findings that require a compromised host or database file
- denials of service from unbounded local resource use without a remote trigger
- reports about placeholder credentials in `.env.example`

## Design references

- [`docs/security.md`](docs/security.md) — boundaries and controls
- [`docs/threat-model.md`](docs/threat-model.md) — assets, actors, threats
- [`docs/security-inventory.md`](docs/security-inventory.md) — control inventory
- [`docs/tools.md`](docs/tools.md) — agent/tool authorization matrix
