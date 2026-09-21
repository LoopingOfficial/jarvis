# Security Policy

JARVIS can execute tools, access local resources, store secrets, and connect to remote systems. Security reports are therefore taken seriously.

## Supported version

The repository is currently pre-release software. Security fixes are applied to the active development branch and then carried into the next tagged release when releases are available.

## Reporting a vulnerability

Please avoid posting exploitable security details in a public issue.

When reporting a vulnerability, include:

- the affected component or file;
- reproduction steps;
- expected and actual behavior;
- impact;
- platform and Python version;
- whether credentials, local files, remote systems, or command execution are involved;
- any proposed mitigation.

If GitHub private vulnerability reporting is enabled for the repository, prefer that channel. Otherwise, open a minimal public issue asking for a private contact channel without including exploit details.

## Security boundaries

The default server binds to `127.0.0.1`. Do not expose it directly to the public Internet without an authentication and reverse-proxy design appropriate for your environment.

Treat the following as sensitive:

- `.env` files;
- API keys and model-provider tokens;
- SSH keys;
- connector credentials;
- encrypted vault material and recovery keys;
- local databases;
- conversation or memory exports;
- automation scripts with privileged commands.

## Operational recommendations

- use least-privilege credentials;
- keep secrets out of Git;
- review tool permissions before enabling destructive actions;
- run remote automation with a restricted account;
- keep Python and dependencies patched;
- inspect third-party models, plugins, connectors, and assets before use;
- back up encrypted data together with any required recovery material;
- do not disable confirmation gates simply to make automation faster.

## Disclosure

Please allow reasonable time for investigation and remediation before publishing technical details that could put users at risk.
