"""API resource modules for DockerPilot Extras backend."""

from .auth import create_auth_resources
from .commands import create_command_resources
from .environment import create_environment_resources
from .migration import create_migration_resource
from .pipeline import create_pipeline_resources
from .promotion import create_promotion_resources
from .progress import create_progress_resources
from .servers import create_server_resources
from .storage import create_storage_resources
from .status import create_status_resources

__all__ = [
    "create_auth_resources",
    "create_command_resources",
    "create_environment_resources",
    "create_migration_resource",
    "create_pipeline_resources",
    "create_promotion_resources",
    "create_progress_resources",
    "create_server_resources",
    "create_storage_resources",
    "create_status_resources",
]
