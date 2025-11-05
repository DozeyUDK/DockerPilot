"""Utility functions for Docker Pilot."""
from datetime import datetime
from typing import Dict, Any


def format_image_size(size_bytes: int) -> str:
    """Format image size for display."""
    if size_bytes == 0:
        return "0 B"
    
    size = float(size_bytes)
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


def format_creation_date(created_str: str) -> str:
    """Format creation date for display."""
    try:
        if created_str:
            created = datetime.fromisoformat(created_str.replace('Z', '+00:00'))
            now = datetime.now(created.tzinfo)
            diff = now - created
            
            if diff.days > 7:
                return created.strftime('%Y-%m-%d')
            elif diff.days > 0:
                return f"{diff.days} days ago"
            elif diff.seconds > 3600:
                hours = diff.seconds // 3600
                return f"{hours} hours ago"
            else:
                minutes = diff.seconds // 60
                return f"{minutes} min ago"
    except Exception:
        pass
    return "unknown"


def format_ports(ports: dict) -> str:
    """Format container ports for display."""
    if not ports:
        return "none"
    
    port_list = []
    for container_port, host_bindings in ports.items():
        if host_bindings:
            for binding in host_bindings:
                host_port = binding['HostPort']
                port_list.append(f"{host_port}→{container_port}")
        else:
            port_list.append(container_port)
    
    return ", ".join(port_list) if port_list else "none"


def get_container_size(container: Any) -> str:
    """Get container size."""
    try:
        # This is approximate - Docker doesn't provide easy size calculation
        return "N/A"  # Could be enhanced with df commands
    except Exception:
        return "N/A"


def calculate_uptime(container: Any) -> str:
    """Calculate container uptime."""
    try:
        if container.status != "running":
            return "N/A"
        
        created = datetime.fromisoformat(container.attrs['Created'].replace('Z', '+00:00'))
        uptime = datetime.now(created.tzinfo) - created
        
        days = uptime.days
        hours, remainder = divmod(uptime.seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        
        if days > 0:
            return f"{days}d {hours}h"
        elif hours > 0:
            return f"{hours}h {minutes}m"
        else:
            return f"{minutes}m"
    except Exception:
        return "N/A"


def count_containers_using_image(client: Any, image_id: str) -> int:
    """Count containers using specific image."""
    try:
        containers = client.containers.list(all=True)
        count = 0
        for container in containers:
            if container.image.id == image_id:
                count += 1
        return count
    except Exception:
        return 0


def calculate_cpu_percent(stats1: dict, stats2: dict) -> float:
    """Calculate CPU percentage from two stat measurements."""
    try:
        cpu1_total = stats1['cpu_stats']['cpu_usage']['total_usage']
        cpu1_system = stats1['cpu_stats'].get('system_cpu_usage', 0)
        
        cpu2_total = stats2['cpu_stats']['cpu_usage']['total_usage']
        cpu2_system = stats2['cpu_stats'].get('system_cpu_usage', 0)
        
        if cpu2_system - cpu1_system == 0:
            return 0.0
        
        cpu_percent = ((cpu2_total - cpu1_total) / (cpu2_system - cpu1_system)) * 100.0
        
        # Handle multi-core systems
        num_cores = len(stats2['cpu_stats']['cpu_usage'].get('percpu_usage', []))
        if num_cores > 0:
            cpu_percent = cpu_percent / num_cores
        
        return max(0.0, min(100.0, cpu_percent))
    except (KeyError, ZeroDivisionError):
        return 0.0


def get_trend_indicator(values: list) -> str:
    """Get trend indicator arrow based on values."""
    if len(values) < 2:
        return "→"
    
    recent = values[-3:] if len(values) >= 3 else values
    avg_recent = sum(recent) / len(recent)
    avg_older = sum(values[:-len(recent)]) / len(values[:-len(recent)]) if len(values) > len(recent) else avg_recent
    
    if avg_recent > avg_older * 1.05:
        return "↗️"
    elif avg_recent < avg_older * 0.95:
        return "↘️"
    else:
        return "→"

