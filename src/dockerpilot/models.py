"""Data models for Docker Pilot."""
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict


class LogLevel(Enum):
    """Logging level enumeration."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


@dataclass
class DeploymentConfig:
    """Deployment configuration."""
    image_tag: str
    container_name: str
    port_mapping: Dict[str, str]
    environment: Dict[str, str]
    volumes: Dict[str, str]
    restart_policy: str = "unless-stopped"
    health_check_endpoint: str = "/health"
    health_check_timeout: int = 30
    health_check_retries: int = 10
    build_args: Dict[str, str] = None
    network: str = "bridge"
    cpu_limit: str = None
    memory_limit: str = None


@dataclass
class ContainerStats:
    """Container statistics."""
    cpu_percent: float
    memory_usage_mb: float
    memory_limit_mb: float
    memory_percent: float
    network_rx_mb: float
    network_tx_mb: float
    pids: int
    timestamp: datetime

