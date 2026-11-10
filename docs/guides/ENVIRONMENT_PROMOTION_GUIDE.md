# Environment Promotion Guide

This guide describes the classic dockerpilot promote command. It uses the legacy deployment configuration format and the Docker permissions of the local DockerPilot process. It is separate from the Secure Deploy broker and its broker-owned approval flow.

## Command

~~~bash
dockerpilot promote dev staging --config deployment-dev.yml
dockerpilot promote staging prod --config deployment-staging.yml
~~~

The source and target names must be one of dev, staging, or prod. The configuration file must exist and contain a deployment section.

## What the current implementation does

The promotion code currently applies these CPU and memory values to the target deployment configuration:

| Target | CPU limit | Memory limit |
|---|---:|---:|
| dev | 0.5 | 512Mi |
| staging | 1.0 | 1Gi |
| prod | 2.0 | 2Gi |

The current code preserves the configured image tag. It does not automatically append the environment suffixes shown in older versions of this guide. Although the implementation contains replica and suffix metadata, the promotion path does not apply replica scaling.

The deployment strategy is selected by target:

- prod uses the existing blue-green deployment implementation;
- dev and staging use the rolling deployment implementation.

After a successful deployment and post-promotion validation, DockerPilot saves a target configuration named deployment-<target>.yml next to the input configuration. If a metadata.json file is present, it also updates the target config reference.

### Special staging behavior

When promoting to staging, DockerPilot first checks whether a container with the configured base name or its _blue / _green variant is already running. If it finds one, it saves the staging configuration and records the promotion without creating another container.

## Important implementation limits

The current pre-promotion checks are **simulated**. The implementation prints messages about source health, target readiness, tests, and monitoring, but these messages are not backed by live test-runner or monitoring integrations. Do not treat them as a real CI or production-readiness gate.

Post-promotion validation performs an HTTP health check when the deployment configuration has a port mapping. Additional messages about services, performance, and logs are currently placeholders rather than independent monitoring checks.

The command does not automatically change the image tag, apply replica counts, or provision a separate host for each environment. It operates on the Docker client and deployment configuration available to the current process.

Do not interpret this classic promotion workflow as Secure Deploy. It does not use the root broker's independent plan verification or broker-owned approval ledger.

## Example configuration

Use a versioned image reference and keep credentials out of the example configuration:

~~~yaml
deployment:
  image_tag: "example/myapp:1.2.3"
  container_name: "myapp"
  port_mapping:
    "8080": "8080"
  restart_policy: "unless-stopped"
  health_check_endpoint: "/health"
  cpu_limit: "1.0"
  memory_limit: "1Gi"
~~~

Promote the configuration:

~~~bash
dockerpilot promote staging prod --config deployment-staging.yml
~~~

The target CPU and memory values are written into the configuration used for the promotion.

## Before production promotion

- Review the exact deployment configuration and image reference.
- Confirm the Docker context points to the intended host.
- Back up persistent data using the documented backup workflow.
- Run application and integration tests separately; the current pre-promotion messages do not execute them.
- Verify the health-check endpoint and monitor the target after deployment.
- Test the rollback path of the selected deployment strategy in a non-production environment.

## Related documentation

- [Quick Deploy](../quick-deploy.md)
- [Health Check Configuration](HEALTH_CHECKS_CONFIG.md)
- [Sudo and Backup Permissions](SUDO_SETUP.md)
- [Secure Deploy Broker Threat Model](../security/SECURE_DEPLOY_BROKER_THREAT_MODEL.md)
