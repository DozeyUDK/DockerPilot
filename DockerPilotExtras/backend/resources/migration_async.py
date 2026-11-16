"""Additive asynchronous migration API resources."""

from __future__ import annotations

from backend.services.migration_jobs import (
    MigrationCancellationTooLate,
    MigrationJobCapacityExceeded,
    MigrationJobConflict,
    MigrationJobNotFound,
)
from backend.services.migration_runner import MigrationSpec
from backend.services.migration_service import MigrationQueueFull


def create_async_migration_resources(*, Resource, request, migration_service, copy_resource=None):
    """Build collection/detail resources around an injected service."""

    class ContainerMigrationCollection(Resource):
        def post(self):
            spec = MigrationSpec.from_payload(request.get_json())
            if not all(
                isinstance(value, str) and bool(value.strip())
                for value in (
                    spec.container_name,
                    spec.source_server_id,
                    spec.target_server_id,
                )
            ):
                return {
                    "error": "container_name, source_server_id and target_server_id must be non-empty strings",
                    "code": "invalid_migration_request",
                }, 400
            spec = MigrationSpec(
                container_name=spec.container_name.strip(),
                source_server_id=spec.source_server_id.strip(),
                target_server_id=spec.target_server_id.strip(),
                include_data=spec.include_data,
                stop_source=spec.stop_source,
                copy_only=spec.copy_only,
                source_container_id=spec.source_container_id,
                source_engine_id=spec.source_engine_id,
            )
            if spec.source_server_id == spec.target_server_id:
                return {
                    "error": "Source and target servers must be different",
                    "code": "invalid_migration_request",
                }, 400
            if not isinstance(spec.include_data, bool) or not isinstance(
                spec.stop_source,
                bool,
            ):
                return {
                    "error": "include_data and stop_source must be booleans",
                    "code": "invalid_migration_request",
                }, 400
            try:
                if spec.copy_only is not False:
                    if copy_resource is None:
                        return {"error": "Copy to host is unavailable"}, 501
                    try:
                        copy_resource().copy_plan(spec.to_payload())
                    except ValueError as exc:
                        return {"error": str(exc)}, 400
                    except Exception:
                        return {"error": "Could not verify the source or target Docker host"}, 502
                job = migration_service.submit(spec)
            except MigrationJobConflict as exc:
                body = {
                    "error": "A migration is already active for this container",
                    "code": "migration_conflict",
                }
                try:
                    migration_service.get_async(exc.job_id)
                except MigrationJobNotFound:
                    pass
                else:
                    body["migration_id"] = exc.job_id
                return body, 409
            except (MigrationQueueFull, MigrationJobCapacityExceeded):
                return (
                    {
                        "error": "Migration capacity is currently exhausted",
                        "code": "migration_capacity_exceeded",
                    },
                    503,
                    {"Retry-After": "1"},
                )
            except RuntimeError:
                return {
                    "error": "Migration service is unavailable",
                    "code": "migration_service_unavailable",
                }, 503

            status_url = f"/api/containers/migrations/{job['id']}"
            return (
                {
                    "success": True,
                    "migration_id": job["id"],
                    "status_url": status_url,
                    "cancel_url": status_url,
                    "job": job,
                },
                202,
                {"Location": status_url},
            )

        def get(self):
            jobs = migration_service.active_async()
            return {"success": True, "jobs": jobs, "count": len(jobs)}

    class ContainerMigrationJob(Resource):
        def get(self, migration_id):
            try:
                job = migration_service.get_async(migration_id)
            except MigrationJobNotFound:
                return {
                    "error": "Migration job not found",
                    "code": "migration_not_found",
                }, 404
            return {"success": True, "job": job}

        def delete(self, migration_id):
            try:
                job = migration_service.cancel_async(migration_id)
            except MigrationJobNotFound:
                return {
                    "error": "Migration job not found",
                    "code": "migration_not_found",
                }, 404
            except MigrationCancellationTooLate:
                return {
                    "error": "Migration is already finalizing",
                    "code": "migration_cancellation_too_late",
                }, 409
            status = 200 if job["status"] in {"completed", "failed", "cancelled"} else 202
            return {"success": True, "job": job}, status

    class ContainerCopyPlan(Resource):
        def post(self):
            data = request.get_json(silent=True)
            try:
                return copy_resource().copy_plan(data)
            except ValueError as exc:
                return {"error": str(exc)}, 400
            except Exception:
                return {"error": "Could not verify the source or target Docker host"}, 502

    class ContainerCopyStart(Resource):
        def post(self, migration_id):
            try:
                job = migration_service.get_async(migration_id)
            except MigrationJobNotFound:
                return {"error": "Copy job not found"}, 404
            metadata = job.get("metadata", {})
            if job.get("status") != "completed" or not metadata.get("created_container_id"):
                return {"error": "The job has no completed container copy"}, 409
            try:
                return copy_resource().start_copy(metadata)
            except ValueError as exc:
                return {"error": str(exc)}, 409
            except Exception:
                return {"error": "Could not start the copy on the target Docker host"}, 502

    if copy_resource is not None:
        return ContainerMigrationCollection, ContainerMigrationJob, ContainerCopyPlan, ContainerCopyStart
    return ContainerMigrationCollection, ContainerMigrationJob
