# BugBait Security

## The model

BugBait is a **local-first tool**: it runs on your computer, has no user accounts,
and sends nothing anywhere except the API calls you asked for (Muse Spark for the
agents, GitHub for clones and pull requests). There is no BugBait server that can
leak your code, because your code never leaves your machine.

## What is protected, and how

- **API tokens** (`META_API_KEY`, `GITHUB_TOKEN`) live only in your local `.env`,
  which is gitignored. The GitHub token is sent as an HTTP auth header, never
  embedded in clone URLs, so it cannot leak into logs or error output.
- **The dashboard** binds to `127.0.0.1` only. Nobody on your network can open it.
- **Your projects are never modified.** Every bug runs in a throwaway temp copy;
  dependency installs happen from a copy too. The only writes to your code are
  the ones you explicitly choose (Add tests / Open pull request).
- **GitHub writes are explicit and narrow.** Nothing is pushed without you
  clicking "Open pull request", and repos you can't push to get a PR in your
  fork — never a surprise PR upstream.

## Honest limits

- Tests run **on your machine**, so only point BugBait at code you trust:
  installing a project's dependencies executes that project's code.
- Anyone with access to your computer user account can read `.env`, like any
  developer tool. Your OS login is that boundary.

## MFA and the hosted version

Multi-factor authentication protects **accounts on a server**. The local app and
the planned desktop app have no accounts, so there is nothing for MFA to guard —
adding a password screen to a localhost tool would be theater, not security.

MFA belongs to the hosted website phase, where it is planned properly:

1. Sign in with GitHub (OAuth), so BugBait never stores a password at all.
2. Optional TOTP second factor (authenticator apps) with single-use backup codes.
3. Passkeys (WebAuthn) as the long-term upgrade.

## Reporting a vulnerability

Open a private report via GitHub Security Advisories on this repo, or email the
maintainer. Please don't file public issues for security bugs.
