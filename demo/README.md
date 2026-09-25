# DockerPilot live demo

This directory is infrastructure for an isolated, disposable live demo. It does **not** contain a copy of DockerPilot.

The source of truth is always the checkout that created the Codespace:

- `src/dockerpilot/` provides the CLI and runtime logic.
- `DockerPilotExtras/` provides the real web backend and frontend.
- `demo/` only creates sample containers, isolated state, runtime credentials, and lifecycle helpers.
- `.devcontainer/devcontainer.json` defines the Codespaces environment.

A new Codespace created from `main` therefore uses the current `main`. A Codespace created from a feature branch or PR uses that branch/PR instead.

## Start in GitHub Codespaces

Use the repository's **Open in GitHub Codespaces** link or create a Codespace from the branch you want to demonstrate. The dev container automatically runs:

1. `bash demo/bootstrap.sh`
2. `bash demo/start.sh`

Bootstrap installs DockerPilot from the current checkout with `pip install -e`, installs the real DockerPilotExtras dependencies, and builds the real React frontend. Start launches sample containers in Docker-in-Docker and then starts DockerPilotExtras on port `5000`.

Run this at any time to see the URL and generated credentials:

```bash
bash demo/status.sh
```

The generated username is `demo`. The password and Flask session key are generated per demo state and stored outside the repository under `~/.dockerpilot_demo/`.

## Share the demo

Codespaces forwards port `5000` privately by default. Right before a presentation you can make it public:

```bash
bash demo/public.sh
```

Share the displayed URL and generated demo password. Make it private again when finished:

```bash
bash demo/private.sh
```

GitHub resets a public forwarded port back to private after the port is removed/re-added or the Codespace restarts, so public visibility is intentionally temporary.

## Refresh after pulling changes

An already-running Codespace does not rewrite itself when `main` changes. After updating the checkout, rebuild from that checkout:

```bash
git pull
bash demo/refresh.sh
```

A newly created Codespace always starts from the selected current branch/ref and needs no synchronization layer or copied demo repository.

## Reset the demo

To recreate demo state and rotate the generated credentials:

```bash
bash demo/reset.sh
```

Demo state uses an isolated HOME under `~/.dockerpilot_demo/home`, so it does not overwrite a developer's normal `~/.dockerpilot_extras` configuration.

## Stop

```bash
bash demo/stop.sh
```

## CI smoke

`.github/workflows/demo-smoke.yml` boots the same demo on a GitHub-hosted Linux runner. It verifies that:

- DockerPilotExtras can authenticate with the generated demo credentials;
- Extras sees a working local Docker daemon and DockerPilot installation;
- the DockerPilot CLI sees the seeded demo containers.

This catches changes in the real application that would break the Codespaces demo.
