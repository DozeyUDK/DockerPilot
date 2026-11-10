# DockerPilot

[![CI](https://github.com/DozeyUDK/DockerPilot/actions/workflows/ci.yml/badge.svg)](https://github.com/DozeyUDK/DockerPilot/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/DozeyUDK/DockerPilot)](LICENSE)
[![Release](https://img.shields.io/github/v/tag/DozeyUDK/DockerPilot?sort=semver)](https://github.com/DozeyUDK/DockerPilot/releases)
[![Docs](https://img.shields.io/badge/docs-guides-blue)](docs/)
[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/DozeyUDK/DockerPilot?quickstart=1)

<img width="2667" height="465" alt="DockerPilot" src="https://github.com/user-attachments/assets/c6ae8332-7e0e-4588-b014-ad9a90992087" />

**DockerPilot is a Docker operations platform with an optional privilege-separated Secure Deploy path, broker-owned approvals, and independent policy revalidation.**

It combines a CLI/TUI, an optional operations web panel, and an MCP server. Secure Deploy is the security-focused deployment path: it moves privileged deployment decisions out of the Extras web process and into a separately controlled host broker.

> Secure Deploy is an optional architecture, not a blanket claim that every DockerPilot component is isolated from Docker or safe to expose to an untrusted user.

## Why Secure Deploy

A web application that can access the Docker daemon has substantial host authority. DockerPilot's Secure Deploy profile is designed to keep that authority out of the DockerPilotExtras control plane.

- **Deterministic plans:** a deployment plan is canonicalized and identified by a SHA-256 hash. Security-relevant fields, including plan expiry, are covered by that hash.
- **Independent revalidation:** the broker recomputes the plan identity, source-spec and Compose semantics, firewall plan, and DozeyGuard result. It does not trust a status or hash merely because Extras supplied it.
- **Broker-owned approval:** the broker creates and stores approval records itself. Approval is bound to one plan ID and hash; the control plane receives an approval ID, not authority-bearing approval JSON.
- **Separate approver boundary:** a root-only Unix socket accepts approval requests. The broker derives the peer UID through Linux SO_PEERCRED and checks a root-owned allowlist.
- **Closed execution path:** the broker exposes an allowlist of operations and a fixed canary workflow. It is not a generic root shell, arbitrary Docker API, or unrestricted Compose runner.

### Secure Deploy flow

~~~mermaid
flowchart TB
    Operator["Operator"] --> Extras["DockerPilotExtras<br/>unprivileged control plane"]
    Extras --> Plan["Validate spec and create<br/>canonical deployment plan"]
    Plan --> Hash["plan_sha256"]
    Hash --> Control["Broker control socket<br/>request verification / challenge"]
    Control --> Broker["Root-owned Secure Deploy broker"]
    Broker --> Verify["Recompute plan, Compose,<br/>firewall and DozeyGuard"]
    Verify --> Challenge["Non-authorizing approval challenge"]
    Challenge --> Approver["Allowlisted local process"]
    Approver --> Socket["Root-only approver.sock<br/>SO_PEERCRED + UID allowlist"]
    Socket --> Ledger["Broker-owned approval ledger"]
    Ledger --> Broker
    Broker --> Admission["Fixed canary admission<br/>and execution state machine"]
    Admission --> Docker["Docker Engine"]
    Tamper["Plan drift / forged result / expired approval"] --> Reject["Reject before Docker action"]
~~~

The approval socket proves that the request came from an allowlisted local UID outside the Extras service-account boundary. It does not make the Extras UI an independently trusted plan display. The repository does not currently provide a separate trusted plan viewer for the root approver; plan contents and hash must be verified out of band. Approval by hash alone is not proof that the plan contents were reviewed.

### Current scope and limits

Secure Deploy currently supports a constrained broker-owned canary path. It does not provide generic privileged deployment of arbitrary Compose files.

The broker protocol does not expose arbitrary shell or command execution, arbitrary client-supplied Compose, generic apply, unrestricted firewall changes, or secret materialization. The canary path uses a broker-owned Compose template and rechecks the final bytes before execution.

The broker itself runs with host-level authority and Docker daemon access. Compromise of the broker, host root, Docker daemon, or an allowlisted approver identity is outside the protection boundary. Repository unit files and installer tooling do not prove that the broker has been installed on a particular host; installation is a separate, privileged operator action.

See the [current broker threat model](docs/security/SECURE_DEPLOY_BROKER_THREAT_MODEL.md), [approval model](docs/security/SECURE_DEPLOY_APPROVAL_MODEL.md), and [security invariants](docs/security/SECURITY_INVARIANTS.md).

## Components

| Component | Purpose |
|---|---|
| **DockerPilot CLI/TUI** | Local container, image, monitoring, rolling/blue-green/canary deployment, backup, and configuration operations |
| **DockerPilotExtras** | Optional web workbench for environments, CI profiles, status, storage, and operations |
| **MCP server** | Structured DockerPilot tools for AI clients; read-only by default |
| **Secure Deploy broker** | Separate privileged boundary for the constrained, approved canary workflow |
| **DozeyGuard** | Offline policy scanner for normalized Compose input; it does not call Docker or apply changes |

DockerPilot can also generate CI pipeline configuration for GitHub Actions, GitLab CI, and Jenkins. It writes pipeline files; the external CI runner executes them.

The CLI and ordinary MCP operations use the Docker permissions of their process. Membership in the Docker group or unrestricted access to docker.sock is effectively root-equivalent. Prefer rootless Docker where practical and grant daemon access only to trusted operators.

MCP read-only mode is the default, but it can be changed through configuration. The MCP tool's **confirm: true** argument is caller acknowledgement, not independent human approval. MCP and Secure Deploy are separate execution paths; see the [MCP safety model](docs/mcp.md).

## Live Demo

Launch the isolated demo from the current repository checkout:

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/DozeyUDK/DockerPilot?quickstart=1)

The Codespace runs the real CLI package and DockerPilotExtras frontend/backend against Docker-in-Docker. The public demo is **read-only by default**. Its backend guard blocks Docker-admin mutations, command execution, migrations, server/storage changes, and Secure Deploy mutations.

After startup:

~~~bash
bash demo/status.sh
~~~

To demonstrate the Secure Deploy verification path in a disposable test harness:

~~~bash
bash demo/secure-deploy-demo.sh
~~~

This runs selected regression tests against the real broker, plan, and approval code using temporary state and a fake DozeyGuard executable. It demonstrates broker revalidation, plan-tamper rejection, approval provenance checks, and expiry binding. It does **not** start a production broker, access the host Docker daemon, change firewall rules, read secrets, or deploy a container. It is a code-path demonstration, not a production installation.

Codespaces forwards port 5000 privately by default. **bash demo/public.sh** only exposes the read-only demo; it refuses to make an interactive demo public. See [demo/README.md](demo/README.md) for isolation and lifecycle details.

## Install

### CLI only

Linux/macOS:

~~~bash
git clone https://github.com/DozeyUDK/DockerPilot.git
cd DockerPilot
./install.sh
~~~

Windows PowerShell:

~~~powershell
git clone https://github.com/DozeyUDK/DockerPilot.git
cd DockerPilot
powershell -ExecutionPolicy Bypass -File install.ps1
~~~

### CLI + DockerPilotExtras

Linux/macOS:

~~~bash
git clone https://github.com/DozeyUDK/DockerPilot.git
cd DockerPilot
./install_everything.sh
~~~

Windows PowerShell:

~~~powershell
git clone https://github.com/DozeyUDK/DockerPilot.git
cd DockerPilot
powershell -ExecutionPolicy Bypass -File install.ps1 -Extras
~~~

The script-based installer uses a virtual environment by default, avoiding PEP 668 conflicts on current Debian and Ubuntu releases. DockerPilotExtras also needs Node.js 18+ and npm to build its frontend.

### Manual install

Use a virtual environment rather than modifying the system Python:

~~~bash
git clone https://github.com/DozeyUDK/DockerPilot.git
cd DockerPilot
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
~~~

Optional extras:

~~~bash
python -m pip install -e ".[tui]"       # Mouse-friendly terminal UI
python -m pip install -e ".[mcp]"       # MCP server
python -m pip install -e ".[git]"       # Git integration
python -m pip install -e ".[test]"      # Test dependencies
~~~

Avoid **--break-system-packages** on PEP 668 distributions. The installer's explicit **--system** mode is intended only for environments where the operator deliberately manages system Python packages; the default virtual-environment install is recommended.

**Requirements:** Python 3.10+, Docker Engine 20.10+. Node.js 18+ and npm are required for the DockerPilotExtras frontend.

## First commands

~~~bash
dockerpilot --help
dockerpilot container list --all
dockerpilot monitor dashboard myapp --duration 300
dockerpilot tui
dockerpilot deploy init --output deployment.yml
~~~

Classic CLI deployment commands use the current process's Docker permissions and the legacy deployment configuration format. They do not automatically use the Secure Deploy broker. See [Quick Deploy](docs/quick-deploy.md) for the classic deployment workflow.

## Documentation

| Guide | What it covers |
|---|---|
| [DockerPilotExtras](DockerPilotExtras/README.md) | Web panel, environments, CI profiles, status, storage, and API |
| [Quick Deploy](docs/quick-deploy.md) | Classic CLI deployment and strategies |
| [Multiple Containers](docs/multi-container.md) | Multi-container operations |
| [Health Checks](docs/guides/HEALTH_CHECKS_CONFIG.md) | Endpoint discovery and overrides |
| [Environment Promotion](docs/guides/ENVIRONMENT_PROMOTION_GUIDE.md) | Current behavior and limits of dockerpilot promote |
| [Sudo and Backup Permissions](docs/guides/SUDO_SETUP.md) | Scoped elevation and Docker permission boundaries |
| [MCP Integration](docs/mcp.md) | MCP setup, configuration, and safety controls |
| [DozeyGuard](components/dozeyguard/README.md) | Scanner role, JSON contract, build, and integration |
| [Release Process](docs/RELEASING.md) | Version synchronization, CI gates, and release policy |

Security and implementation references:

- [Secure Deploy broker threat model](docs/security/SECURE_DEPLOY_BROKER_THREAT_MODEL.md)
- [Secure Deploy approval model](docs/security/SECURE_DEPLOY_APPROVAL_MODEL.md)
- [Approval provenance and trust boundary](docs/security/APPROVAL_PROVENANCE_V1.md)
- [Secure Deploy contract v1](docs/security/SECURE_DEPLOY_CONTRACT_V1.md)
- [Broker protocol v1](docs/security/SECURE_DEPLOY_BROKER_PROTOCOL_V1.md)
- [Security invariants](docs/security/SECURITY_INVARIANTS.md)
- [Adversarial test plan](docs/security/ADVERSARIAL_TEST_PLAN.md)

## Contributing

Issues and pull requests are welcome. Please include focused tests for behavior changes and keep security claims aligned with the current implementation. Start with the [contributing guidelines](CONTRIBUTING.md).

## License

MIT. See [LICENSE](LICENSE).

---

- **Version**: 0.9.0-pre.3
- **Python**: 3.10+
- **Docker**: 20.10+
- **Components**: CLI package, optional DockerPilotExtras web panel, optional MCP server
