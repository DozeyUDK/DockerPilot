"""Configuration templates for Docker Pilot."""
import os
from pathlib import Path

# Get the directory where templates are stored
TEMPLATES_DIR = Path(__file__).parent

def get_template_path(template_name):
    """Get the full path to a template file."""
    return TEMPLATES_DIR / template_name

__all__ = ["TEMPLATES_DIR", "get_template_path"]
