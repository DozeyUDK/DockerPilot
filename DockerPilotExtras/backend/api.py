"""Flask-RESTful route registration for DockerPilot Extras."""

from __future__ import annotations

import os

from flask import jsonify, request


_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_AGENT_SAFE_ALLOWED_MUTATION_PATHS = frozenset(
    {
        "/api/auth/login",
        "/api/auth/logout",
    }
)
_AGENT_SAFE_ALLOWED_MUTATION_PREFIXES = ("/api/secure-deploy/",)


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def agent_safe_mutation_is_blocked(*, enabled: bool, method: str, path: str) -> bool:
    """Return True when agent-safe mode must reject this HTTP request.

    The rule is deliberately fail-closed for legacy API writes: new mutating
    endpoints are blocked automatically unless they live under the separately
    reviewed Secure Deploy contract or are limited to login/logout session state.
    """
    if not enabled:
        return False
    if (method or "").upper() not in _MUTATING_METHODS:
        return False
    normalized_path = path or ""
    if not normalized_path.startswith("/api/"):
        return False
    if normalized_path in _AGENT_SAFE_ALLOWED_MUTATION_PATHS:
        return False
    if any(normalized_path.startswith(prefix) for prefix in _AGENT_SAFE_ALLOWED_MUTATION_PREFIXES):
        return False
    return True


def _install_agent_safe_guard(api) -> None:
    """Install a startup-captured fail-closed guard for legacy API mutations."""
    enabled = _env_flag("DOCKERPILOT_AGENT_SAFE_MODE", False)
    app = getattr(api, "app", None)
    if app is None:
        raise RuntimeError("Flask app must be attached before registering DockerPilot API routes")

    @app.before_request
    def enforce_agent_safe_mode():
        if not agent_safe_mutation_is_blocked(
            enabled=enabled,
            method=request.method,
            path=request.path,
        ):
            return None
        return jsonify(
            {
                "success": False,
                "error": "Mutation blocked by DockerPilot agent-safe mode",
                "agent_safe_mode": True,
                "code": "agent_safe_mutation_blocked",
            }
        ), 403


def register_api_routes(
    api,
    *,
    HealthCheck,
    AuthStatus,
    AuthLogin,
    AuthLogout,
    PipelineGenerate,
    PipelineSave,
    PipelineLibrary,
    PipelineDeploymentConfig,
    PipelineIntegration,
    DeploymentConfig,
    DeploymentExecute,
    DeploymentHistory,
    EnvironmentPromote,
    CancelPromotion,
    CheckSudoRequired,
    ElevationToken,
    EnvironmentPromoteSingle,
    DeploymentProgress,
    EnvironmentStatus,
    PrepareContainerConfig,
    ImportDeploymentConfig,
    EnvServersMap,
    EnvContainerBindings,
    StatusCheck,
    PreflightCheck,
    ContainerList,
    DockerImages,
    DockerfilePaths,
    FileBrowser,
    ExecuteCommand,
    GetCommandHelp,
    DockerPilotCommands,
    StorageStatus,
    StorageTestPostgres,
    StorageDiscoverLocalPostgres,
    StorageBootstrapLocalPostgres,
    StorageConfigure,
    ServerList,
    ServerCreate,
    ServerUpdate,
    ServerDelete,
    ServerTest,
    ServerSelect,
    BlueGreenReplace,
    ContainerMigrate,
    ContainerMigrationCollection,
    ContainerMigrationJob,
    MigrationProgress,
    CancelMigration,
    SecureDeployDrafts=None,
    SecureDeployDraftDetail=None,
    SecureDeployValidate=None,
    SecureDeployPlan=None,
    SecureDeployPlanDetail=None,
    SecureDeployPlanApprove=None,
    SecureDeployApprovalDetail=None,
    SecureDeployApprovalRevoke=None,
    SecureDeployBrokerDryRun=None,
    SecureDeployCanaryAdmit=None,
    SecureDeployCanaryDeploy=None,
    SecureDeployCanaryRemove=None,
):
    """Register all API resources and routes."""
    _install_agent_safe_guard(api)

    api.add_resource(HealthCheck, "/api/health")
    api.add_resource(AuthStatus, "/api/auth/status")
    api.add_resource(AuthLogin, "/api/auth/login")
    api.add_resource(AuthLogout, "/api/auth/logout")
    api.add_resource(PipelineGenerate, "/api/pipeline/generate")
    api.add_resource(PipelineSave, "/api/pipeline/save")
    api.add_resource(
        PipelineLibrary,
        "/api/pipeline/saved",
        "/api/pipeline/saved/<string:filename>",
    )
    api.add_resource(PipelineDeploymentConfig, "/api/pipeline/deployment-config")
    api.add_resource(PipelineIntegration, "/api/pipeline/integrate")
    api.add_resource(DeploymentConfig, "/api/deployment/config")
    api.add_resource(DeploymentExecute, "/api/deployment/execute")
    api.add_resource(DeploymentHistory, "/api/deployment/history")
    api.add_resource(EnvironmentPromote, "/api/environment/promote")
    api.add_resource(CancelPromotion, "/api/environment/cancel-promotion")
    api.add_resource(CheckSudoRequired, "/api/environment/check-sudo")
    api.add_resource(ElevationToken, "/api/environment/elevation-token")
    api.add_resource(EnvironmentPromoteSingle, "/api/environment/promote-single")
    api.add_resource(DeploymentProgress, "/api/environment/progress")
    api.add_resource(EnvironmentStatus, "/api/environment/status")
    api.add_resource(PrepareContainerConfig, "/api/environment/prepare-config")
    api.add_resource(ImportDeploymentConfig, "/api/environment/import-config")
    api.add_resource(EnvServersMap, "/api/environment/servers-map")
    api.add_resource(EnvContainerBindings, "/api/environment/container-bindings")
    api.add_resource(StatusCheck, "/api/status")
    api.add_resource(PreflightCheck, "/api/preflight")
    api.add_resource(ContainerList, "/api/containers")
    api.add_resource(DockerImages, "/api/docker/images")
    api.add_resource(DockerfilePaths, "/api/docker/dockerfiles")
    api.add_resource(FileBrowser, "/api/files/browse")
    api.add_resource(ExecuteCommand, "/api/command/execute")
    api.add_resource(GetCommandHelp, "/api/command/help")
    api.add_resource(DockerPilotCommands, "/api/dockerpilot/commands")
    api.add_resource(StorageStatus, "/api/storage/status")
    api.add_resource(StorageTestPostgres, "/api/storage/test-postgres")
    api.add_resource(StorageDiscoverLocalPostgres, "/api/storage/discover-local-postgres")
    api.add_resource(StorageBootstrapLocalPostgres, "/api/storage/bootstrap-local-postgres")
    api.add_resource(StorageConfigure, "/api/storage/configure")
    api.add_resource(ServerList, "/api/servers")
    api.add_resource(ServerCreate, "/api/servers/create")
    api.add_resource(ServerUpdate, "/api/servers/<string:server_id>")
    api.add_resource(ServerDelete, "/api/servers/<string:server_id>")
    api.add_resource(ServerTest, "/api/servers/<string:server_id>/test", "/api/servers/test")
    api.add_resource(ServerSelect, "/api/servers/select")
    api.add_resource(BlueGreenReplace, "/api/containers/blue-green-replace")
    api.add_resource(ContainerMigrate, "/api/containers/migrate")
    api.add_resource(ContainerMigrationCollection, "/api/containers/migrations")
    api.add_resource(
        ContainerMigrationJob,
        "/api/containers/migrations/<string:migration_id>",
    )
    api.add_resource(MigrationProgress, "/api/containers/migration-progress")
    api.add_resource(CancelMigration, "/api/containers/cancel-migration")
    if SecureDeployDrafts is not None:
        api.add_resource(SecureDeployDrafts, "/api/secure-deploy/drafts")
        api.add_resource(SecureDeployDraftDetail, "/api/secure-deploy/drafts/<string:draft_id>")
        api.add_resource(SecureDeployValidate, "/api/secure-deploy/validate")
        api.add_resource(SecureDeployPlan, "/api/secure-deploy/plan")
        api.add_resource(SecureDeployPlanDetail, "/api/secure-deploy/plans/<string:plan_id>")
        if SecureDeployPlanApprove is not None:
            api.add_resource(
                SecureDeployPlanApprove,
                "/api/secure-deploy/plans/<string:plan_id>/approve",
            )
            api.add_resource(
                SecureDeployApprovalDetail,
                "/api/secure-deploy/approvals/<string:approval_id>",
            )
            api.add_resource(
                SecureDeployApprovalRevoke,
                "/api/secure-deploy/approvals/<string:approval_id>/revoke",
            )
            api.add_resource(
                SecureDeployBrokerDryRun,
                "/api/secure-deploy/plans/<string:plan_id>/broker-dry-run",
            )
            if SecureDeployCanaryAdmit is not None:
                api.add_resource(
                    SecureDeployCanaryAdmit,
                    "/api/secure-deploy/plans/<string:plan_id>/canary/admit",
                )
                api.add_resource(
                    SecureDeployCanaryDeploy,
                    "/api/secure-deploy/plans/<string:plan_id>/canary/deploy",
                )
                api.add_resource(
                    SecureDeployCanaryRemove,
                    "/api/secure-deploy/canary/executions/<string:canary_execution_id>/remove",
                )
