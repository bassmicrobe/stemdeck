# Security Policy

## Supported Versions

LayerLab is an unofficial StemDeck fork test build in active alpha. Only the
latest fork release receives security fixes - there are no long-term-support
branches yet. Please update to the newest release before reporting an issue.

| Version         | Supported |
| --------------- | --------- |
| Latest release  | Yes       |
| Any older build | No        |

## Reporting a Vulnerability

Please report security issues privately, not in a public issue. Open the
repository's **Security** tab and click **Report a vulnerability** (GitHub
Private Vulnerability Reporting). This keeps the details private until a fix is
available.

Include where you can:

- Affected version and operating system
- Steps to reproduce
- Impact (what an attacker could do)
- Any relevant logs or proof of concept

What to expect:

- Acknowledgement within about 5 business days (best-effort; small team).
- We confirm the report, assess severity, and keep you updated.
- Fixes ship in the next release. We credit reporters unless you prefer not to
  be named.

## Scope and threat model

LayerLab is local-first and single-user by design: it runs on your own
machine, has no authentication, and is same-origin only. Reports that it "has
no login" or "no per-user access control" describe intended behavior, not
vulnerabilities.

The default backend must remain bound to loopback. API requests reject untrusted
Host headers (DNS rebinding), mismatched Origin headers, and cross-site Fetch
Metadata. Requests without Origin are allowed for native local clients; these
checks are not authentication. Additional hosts may be explicitly configured
with `STEMDECK_ALLOWED_HOSTS` (no wildcards). A reverse proxy must preserve the
correct Host/scheme and enforce authentication, rate limits and storage quotas
before any public or shared-network deployment. This backend does not provide
tenant isolation and must not be used as a shared multi-user service as-is.

JSON bodies are limited to 1 MiB. Audio uploads permit 100 MiB plus bounded
multipart overhead; both declared and streamed sizes are checked. Multipart
files are closed on success and failure. Dynamic exports have a shared capacity
limit (`STEMDECK_MAX_EXPORTS`, default 2); requests over capacity return 503.

### Known dependency limitations

The current Torch/Torchaudio compatibility pins (2.6.x, or 2.2.x on Intel macOS)
leave known PyTorch security advisories unresolved. Do not load untrusted model
checkpoints or replace the model cache with files from an unverified source.
Demucs currently downloads official models with hash checking, but that does
not fix the vulnerable deserializer or make arbitrary local checkpoints safe.
A complete remediation requires a Torch upgrade, audio-writer migration and
real-model compatibility testing on each supported platform. See
[AUDIT.ja.md](AUDIT.ja.md) for the current audit scope and remaining gaps.

We are most interested in reports about:

- Malicious media files or URLs (SSRF, command or argument injection)
- Cross-site scripting (XSS) or Content-Security-Policy bypass in the desktop
  webview
- Path traversal in the backend file APIs
- Integrity of downloaded binaries (FFmpeg, the runtime pack)

## Good-faith research

We will not pursue action against good-faith security research that respects
user privacy and avoids data destruction or service disruption. Thank you for
helping keep LayerLab users safe.
