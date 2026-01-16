"""
Configuration settings for Flask backend
"""

import os
from pathlib import Path


class Config:
    """Base configuration"""
    # SECRET_KEY should be set via environment variable in production
    # For development, a random key is generated in app.py if not set
    SECRET_KEY = os.environ.get('SECRET_KEY', None)
    CONFIG_DIR = Path.home() / ".dockerpilot_extras"
    CONFIG_DIR.mkdir(exist_ok=True)
    PIPELINES_DIR = CONFIG_DIR / "pipelines"
    PIPELINES_DIR.mkdir(exist_ok=True)
    DEPLOYMENTS_DIR = CONFIG_DIR / "deployments"
    DEPLOYMENTS_DIR.mkdir(exist_ok=True)
    
    # CORS settings
    CORS_ORIGINS = os.environ.get('CORS_ORIGINS', '*').split(',')
    
    # Flask settings
    PORT = int(os.environ.get('PORT', 5000))
    DEBUG = os.environ.get('FLASK_ENV') == 'development'
    HOST = os.environ.get('HOST', '0.0.0.0')


class ProductionConfig(Config):
    """Production configuration"""
    DEBUG = False
    SECRET_KEY = os.environ.get('SECRET_KEY')
    if not SECRET_KEY:
        raise ValueError("SECRET_KEY must be set in production")


class DevelopmentConfig(Config):
    """Development configuration"""
    DEBUG = True

