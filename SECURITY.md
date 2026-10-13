# Security policy

The supported version is the latest tagged release candidate on the default development branch.

Please report a suspected vulnerability through the repository's private security-reporting channel. If private reporting is unavailable, contact the repository owner before publishing details. Do not include credentials, personal data, or a working exploit in a public issue.

Include the affected commit/version, minimal reproduction, impact, whether Mock or optional Codex mode is involved, and any evidence of filesystem, process or network escape. You should receive acknowledgement within seven days. Disclosure timing will be coordinated after triage and a fix or mitigation is available.

The product threat model, subprocess constraints, approval boundary and local data guidance are documented in [`docs/SECURITY.md`](docs/SECURITY.md). Internet-facing previews must also follow the backend-enforced [`public-preview` security profile](docs/PUBLIC_PREVIEW_SECURITY.md) and the [deployment runbook](docs/DEPLOYMENT_RUNBOOK.md); reports that show a disabled route executing are treated as a release blocker.
