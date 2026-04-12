"""Deployment and migration progress API resources."""

from __future__ import annotations


def create_progress_resources(
    *,
    Resource,
    app,
    request,
    deployment_progress,
    migration_progress,
    migration_cancel_flags,
    datetime_cls,
):
    """Return progress resource classes with injected dependencies."""

    class MigrationProgress(Resource):
        """Get migration progress for a container."""

        def get(self):
            try:
                container_name = request.args.get("container_name")

                if not container_name:
                    active_migrations = {}
                    for name, progress in migration_progress.items():
                        if progress and progress.get("stage") not in ["completed", "failed", "cancelled"]:
                            active_migrations[name] = progress

                    return {
                        "success": True,
                        "active_migrations": active_migrations,
                        "count": len(active_migrations),
                    }

                progress = migration_progress.get(container_name, None)
                if progress:
                    return {"success": True, "progress": progress}
                return {"success": True, "progress": None}
            except Exception as exc:
                app.logger.error(f"Error getting migration progress: {exc}")
                return {"error": str(exc)}, 500

    class CancelMigration(Resource):
        """Cancel ongoing container migration."""

        def post(self):
            try:
                data = request.get_json()
                container_name = data.get("container_name")

                if not container_name:
                    return {"error": "container_name is required"}, 400

                migration_cancel_flags[container_name] = True

                if container_name in migration_progress:
                    migration_progress[container_name] = {
                        "stage": "cancelling",
                        "progress": migration_progress[container_name].get("progress", 0),
                        "message": f"Cancelling container migration {container_name}...",
                        "timestamp": datetime_cls.now().isoformat(),
                    }

                app.logger.info(f"Cancel flag set for migration {container_name}")
                return {
                    "success": True,
                    "message": (
                        f"Cancelling container migration {container_name}. "
                        "Migration will be stopped at the next checkpoint."
                    ),
                }
            except Exception as exc:
                app.logger.error(f"Cancel migration failed: {exc}")
                return {"error": str(exc)}, 500

    class DeploymentProgress(Resource):
        """Get deployment progress for a container or all active deployments."""

        def get(self):
            try:
                container_name = request.args.get("container_name")

                if not container_name:
                    active_deployments = {}
                    completed_deployments = []

                    for name, progress in deployment_progress.items():
                        if not progress:
                            continue
                        merged_progress = progress
                        live_migration = migration_progress.get(name)
                        if live_migration and progress.get("stage") in ["migrating", "preparing"]:
                            merged_progress = {**progress, **live_migration}

                        stage = merged_progress.get("stage", "")
                        if stage not in ["completed", "failed", "error", "cancelled"]:
                            active_deployments[name] = merged_progress
                        else:
                            timestamp_str = merged_progress.get("timestamp")
                            if timestamp_str:
                                try:
                                    timestamp = datetime_cls.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                                    if timestamp.tzinfo is None:
                                        timestamp = timestamp.replace(
                                            tzinfo=datetime_cls.now().astimezone().tzinfo
                                        )

                                    age_seconds = (
                                        datetime_cls.now(timestamp.tzinfo) - timestamp
                                    ).total_seconds()
                                    if age_seconds > 30:
                                        completed_deployments.append(name)
                                except (ValueError, TypeError):
                                    completed_deployments.append(name)
                            else:
                                completed_deployments.append(name)

                    for name in completed_deployments:
                        deployment_progress.pop(name, None)

                    return {
                        "success": True,
                        "active_deployments": active_deployments,
                        "count": len(active_deployments),
                    }

                progress = deployment_progress.get(container_name, None)
                live_migration = migration_progress.get(container_name, None)

                if progress and live_migration and progress.get("stage") in ["migrating", "preparing"]:
                    return {"success": True, "progress": {**progress, **live_migration}}

                if progress:
                    return {"success": True, "progress": progress}
                if live_migration:
                    return {"success": True, "progress": live_migration}
                return {"success": True, "progress": None}
            except Exception as exc:
                app.logger.error(f"Error getting deployment progress: {exc}")
                return {"error": str(exc)}, 500

    return MigrationProgress, CancelMigration, DeploymentProgress
