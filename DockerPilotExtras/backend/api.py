"""Flask-RESTful route registration for DockerPilot Extras."""

from __future__ import annotations


def register_api_routes(
    api,
    *,
    HealthCheck,
    AuthStatus,
    AuthLogin,
    AuthLogout,
    PipelineGenerate,
    PipelineSave,
    PipelineDeploymentConfig,
    PipelineIntegration,
    DeploymentConfig,
    DeploymentExecute,
    DeploymentHistory,
    EnvironmentPromote,
    CancelPromotion,
    CheckSudoRequired,
    ElevationToken,
    SudoPassword,
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
    MigrationProgress,
    CancelMigration,
):
    """Register all API resources and routes."""
    api.add_resource(HealthCheck, "/api/health")
    api.add_resource(AuthStatus, "/api/auth/status")
    api.add_resource(AuthLogin, "/api/auth/login")
    api.add_resource(AuthLogout, "/api/auth/logout")
    api.add_resource(PipelineGenerate, "/api/pipeline/generate")
    api.add_resource(PipelineSave, "/api/pipeline/save")
    api.add_resource(PipelineDeploymentConfig, "/api/pipeline/deployment-config")
    api.add_resource(PipelineIntegration, "/api/pipeline/integrate")
    api.add_resource(DeploymentConfig, "/api/deployment/config")
    api.add_resource(DeploymentExecute, "/api/deployment/execute")
    api.add_resource(DeploymentHistory, "/api/deployment/history")
    api.add_resource(EnvironmentPromote, "/api/environment/promote")
    api.add_resource(CancelPromotion, "/api/environment/cancel-promotion")
    api.add_resource(CheckSudoRequired, "/api/environment/check-sudo")
    api.add_resource(ElevationToken, "/api/environment/elevation-token")
    api.add_resource(SudoPassword, "/api/environment/sudo-password")
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
    api.add_resource(MigrationProgress, "/api/containers/migration-progress")
    api.add_resource(CancelMigration, "/api/containers/cancel-migration")
