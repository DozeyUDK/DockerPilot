#!/usr/bin/env python3
"""
Migration script to move deployment configs to unified directory structure
Moves configs from various locations to ~/.dockerpilot_extras/deployments/{container_name}_{unique_id}/
"""

import os
import json
import yaml
import shutil
from pathlib import Path
from datetime import datetime
import hashlib
import re

def generate_deployment_id(container_name: str, image_tag: str = None) -> str:
    """Generate unique deployment identifier"""
    timestamp = datetime.now().isoformat()
    hash_input = f"{container_name}_{image_tag or 'latest'}_{timestamp}"
    hash_value = hashlib.sha256(hash_input.encode()).hexdigest()[:12]
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', container_name.lower())
    unique_id = f"{hash_value[:4]}!{hash_value[4:8]}{hash_value[8:12]}"
    return f"{safe_name}_{unique_id}"

def migrate_deployment_configs():
    """Migrate deployment configs to new structure"""
    config_dir = Path.home() / ".dockerpilot_extras"
    deployments_dir = config_dir / "deployments"
    deployments_dir.mkdir(exist_ok=True, parents=True)
    
    # Locations to check for configs
    locations = [
        config_dir,  # ~/.dockerpilot_extras/
        Path.home() / 'DockerPilot',  # Main project directory
    ]
    
    migrated = []
    skipped = []
    
    print("🔄 Migrating deployment configs to unified structure...")
    print(f"📁 Target directory: {deployments_dir}\n")
    
    # Find all deployment configs
    for location in locations:
        if not location.exists():
            continue
        
        # Check for deployment-{env}.yml files
        for env in ['dev', 'staging', 'prod']:
            config_path = location / f'deployment-{env}.yml'
            if config_path.exists():
                try:
                    with open(config_path, 'r', encoding='utf-8') as f:
                        config = yaml.safe_load(f) or {}
                        container_name = config.get('deployment', {}).get('container_name', '')
                        image_tag = config.get('deployment', {}).get('image_tag', 'latest')
                        
                        if container_name:
                            # Create deployment directory
                            deployment_id = generate_deployment_id(container_name, image_tag)
                            deployment_dir = deployments_dir / deployment_id
                            deployment_dir.mkdir(exist_ok=True, parents=True)
                            
                            # Copy config
                            dest_path = deployment_dir / f'deployment-{env}.yml'
                            shutil.copy2(config_path, dest_path)
                            
                            # Create/update metadata
                            metadata_path = deployment_dir / 'metadata.json'
                            if metadata_path.exists():
                                with open(metadata_path, 'r', encoding='utf-8') as f:
                                    metadata = json.load(f)
                            else:
                                metadata = {
                                    'container_name': container_name,
                                    'image_tag': image_tag,
                                    'created_at': datetime.now().isoformat(),
                                    'deployment_id': deployment_id,
                                    'migrated_from': str(config_path)
                                }
                            
                            metadata['last_updated'] = datetime.now().isoformat()
                            metadata[f'env_{env}_config'] = str(dest_path)
                            
                            with open(metadata_path, 'w', encoding='utf-8') as f:
                                json.dump(metadata, f, indent=2)
                            
                            migrated.append(f"{config_path} -> {deployment_dir.name}/deployment-{env}.yml")
                            print(f"✓ Migrated: {config_path.name} -> {deployment_dir.name}/")
                        else:
                            skipped.append(f"{config_path} (missing container_name)")
                except Exception as e:
                    skipped.append(f"{config_path} (error: {e})")
        
        # Check for main deployment.yml
        main_config_path = location / 'deployment.yml'
        if main_config_path.exists():
            try:
                with open(main_config_path, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f) or {}
                    container_name = config.get('deployment', {}).get('container_name', '')
                    image_tag = config.get('deployment', {}).get('image_tag', 'latest')
                    
                    if container_name:
                        # Check if deployment directory already exists
                        deployment_dir = None
                        for existing_dir in deployments_dir.iterdir():
                            if existing_dir.is_dir():
                                metadata_path = existing_dir / 'metadata.json'
                                if metadata_path.exists():
                                    with open(metadata_path, 'r', encoding='utf-8') as f:
                                        metadata = json.load(f)
                                        if metadata.get('container_name', '').lower() == container_name.lower():
                                            deployment_dir = existing_dir
                                            break
                        
                        if not deployment_dir:
                            deployment_id = generate_deployment_id(container_name, image_tag)
                            deployment_dir = deployments_dir / deployment_id
                            deployment_dir.mkdir(exist_ok=True, parents=True)
                        
                        # Copy config
                        dest_path = deployment_dir / 'deployment.yml'
                        shutil.copy2(main_config_path, dest_path)
                        
                        # Update metadata
                        metadata_path = deployment_dir / 'metadata.json'
                        if metadata_path.exists():
                            with open(metadata_path, 'r', encoding='utf-8') as f:
                                metadata = json.load(f)
                        else:
                            metadata = {
                                'container_name': container_name,
                                'image_tag': image_tag,
                                'created_at': datetime.now().isoformat(),
                                'deployment_id': deployment_dir.name,
                                'migrated_from': str(main_config_path)
                            }
                        
                        metadata['last_updated'] = datetime.now().isoformat()
                        metadata['main_config'] = str(dest_path)
                        
                        with open(metadata_path, 'w', encoding='utf-8') as f:
                            json.dump(metadata, f, indent=2)
                        
                        migrated.append(f"{main_config_path} -> {deployment_dir.name}/deployment.yml")
                        print(f"✓ Migrated: {main_config_path.name} -> {deployment_dir.name}/")
                    else:
                        skipped.append(f"{main_config_path} (missing container_name)")
            except Exception as e:
                skipped.append(f"{main_config_path} (error: {e})")
        
        # Check for named configs (e.g., grafana-deployment.yml)
        if hasattr(location, 'glob'):
            for config_path in location.glob('*-deployment.yml'):
                if 'deployment-dev.yml' in str(config_path) or 'deployment-staging.yml' in str(config_path) or 'deployment-prod.yml' in str(config_path):
                    continue  # Already processed
                
                try:
                    with open(config_path, 'r', encoding='utf-8') as f:
                        config = yaml.safe_load(f) or {}
                        container_name = config.get('deployment', {}).get('container_name', '')
                        image_tag = config.get('deployment', {}).get('image_tag', 'latest')
                        
                        if container_name:
                            deployment_id = generate_deployment_id(container_name, image_tag)
                            deployment_dir = deployments_dir / deployment_id
                            deployment_dir.mkdir(exist_ok=True, parents=True)
                            
                            # Copy config
                            dest_path = deployment_dir / 'deployment.yml'
                            shutil.copy2(config_path, dest_path)
                            
                            # Create metadata
                            metadata = {
                                'container_name': container_name,
                                'image_tag': image_tag,
                                'created_at': datetime.now().isoformat(),
                                'deployment_id': deployment_id,
                                'migrated_from': str(config_path)
                            }
                            
                            metadata_path = deployment_dir / 'metadata.json'
                            with open(metadata_path, 'w', encoding='utf-8') as f:
                                json.dump(metadata, f, indent=2)
                            
                            migrated.append(f"{config_path} -> {deployment_dir.name}/deployment.yml")
                            print(f"✓ Migrated: {config_path.name} -> {deployment_dir.name}/")
                        else:
                            skipped.append(f"{config_path} (missing container_name)")
                except Exception as e:
                    skipped.append(f"{config_path} (error: {e})")
    
    print(f"\n✅ Migration completed!")
    print(f"📊 Migrated: {len(migrated)} configs")
    if skipped:
        print(f"⚠️  Skipped: {len(skipped)} configs")
        for skip in skipped:
            print(f"   - {skip}")
    
    return migrated, skipped

if __name__ == '__main__':
    migrated, skipped = migrate_deployment_configs()
    if migrated:
        print(f"\n💡 Note: Old configs remain in original locations.")
        print(f"   You can remove them after verifying everything works correctly.")

