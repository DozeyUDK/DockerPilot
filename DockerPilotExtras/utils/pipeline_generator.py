"""
Utility module for pipeline generation
"""

import yaml
from typing import Dict, List, Optional


class PipelineGenerator:
    """Generator for CI/CD pipelines"""
    
    @staticmethod
    def generate_gitlab_pipeline(
        project_name: str,
        docker_image: str,
        dockerfile: str,
        runner_tags: List[str],
        stages: List[str],
        env_vars: Dict[str, str],
        deploy_strategy: str = "rolling",
        use_cache: bool = True,
        registry_url: Optional[str] = None,
        enable_environments: bool = True,
        deployment_config_path: str = "deployment.yml"
    ) -> str:
        """Generate GitLab CI pipeline YAML"""
        
        pipeline = {
            "image": "docker:latest",
            "services": ["docker:dind"],
            "variables": {
                "DOCKER_DRIVER": "overlay2",
                "DOCKER_TLS_CERTDIR": "/certs"
            },
            "stages": stages,
            "before_script": [
                "docker login -u $CI_REGISTRY_USER -p $CI_REGISTRY_PASSWORD $CI_REGISTRY"
            ]
        }
        
        # Add cache
        if use_cache:
            pipeline["cache"] = {
                "paths": ["~/.docker/"]
            }
        
        # Build stage
        if "build" in stages:
            pipeline["build"] = {
                "stage": "build",
                "tags": runner_tags,
                "script": [
                    f"docker build -t {docker_image} -f {dockerfile} .",
                    f"docker tag {docker_image} $CI_REGISTRY_IMAGE:{docker_image}",
                    f"docker push $CI_REGISTRY_IMAGE:{docker_image}"
                ],
                "only": ["main", "master", "develop"]
            }
            
            # Add environment variables to build
            if env_vars:
                pipeline["build"]["variables"] = env_vars
        
        # Test stage
        if "test" in stages:
            test_script = [
                f"docker run --rm {docker_image} npm test",
                f"docker run --rm {docker_image} npm run lint"
            ]
            
            pipeline["test"] = {
                "stage": "test",
                "tags": runner_tags,
                "script": test_script,
                "only": ["main", "master", "develop"]
            }
            
            if "build" in stages:
                pipeline["test"]["needs"] = ["build"]
        
        # Deploy stages for multiple environments
        if "deploy" in stages:
            if enable_environments:
                # Deploy to DEV (from develop branch)
                pipeline["deploy:dev"] = {
                    "stage": "deploy",
                    "tags": runner_tags,
                    "script": [
                        f"docker pull $CI_REGISTRY_IMAGE:{docker_image}",
                        f"docker tag $CI_REGISTRY_IMAGE:{docker_image} {docker_image}",
                        f"dockerpilot deploy config {deployment_config_path} --type rolling"
                    ],
                    "environment": {
                        "name": "development",
                        "url": f"https://dev.{project_name}.example.com"
                    },
                    "only": ["develop"]
                }
                
                # Deploy to STAGING (from staging branch or after dev success)
                pipeline["deploy:staging"] = {
                    "stage": "deploy",
                    "tags": runner_tags,
                    "script": [
                        f"docker pull $CI_REGISTRY_IMAGE:{docker_image}",
                        f"docker tag $CI_REGISTRY_IMAGE:{docker_image} {docker_image}",
                        f"dockerpilot promote dev staging --config {deployment_config_path}"
                    ],
                    "environment": {
                        "name": "staging",
                        "url": f"https://staging.{project_name}.example.com"
                    },
                    "only": ["staging"],
                    "when": "manual"  # Manual approval for staging
                }
                
                # Deploy to PROD (from main/master branch or after staging success)
                pipeline["deploy:prod"] = {
                    "stage": "deploy",
                    "tags": runner_tags,
                    "script": [
                        f"docker pull $CI_REGISTRY_IMAGE:{docker_image}",
                        f"docker tag $CI_REGISTRY_IMAGE:{docker_image} {docker_image}",
                        f"dockerpilot promote staging prod --config {deployment_config_path}"
                    ],
                    "environment": {
                        "name": "production",
                        "url": f"https://{project_name}.example.com"
                    },
                    "only": ["main", "master"],
                    "when": "manual"  # Manual approval for production
                }
            else:
                # Single deploy stage (backward compatibility)
                deploy_script = [
                    f"docker pull $CI_REGISTRY_IMAGE:{docker_image}",
                    f"docker tag $CI_REGISTRY_IMAGE:{docker_image} {docker_image}",
                    f"dockerpilot deploy config {deployment_config_path} --type {deploy_strategy}"
                ]
                
                pipeline["deploy"] = {
                    "stage": "deploy",
                    "tags": runner_tags,
                    "script": deploy_script,
                    "environment": {
                        "name": "production",
                        "url": f"https://{project_name}.example.com"
                    },
                    "only": ["main", "master"]
                }
        
        return yaml.dump(pipeline, default_flow_style=False, allow_unicode=True, sort_keys=False)
    
    @staticmethod
    def generate_jenkins_pipeline(
        project_name: str,
        docker_image: str,
        dockerfile: str,
        agent: str,
        credentials_id: str,
        stages: List[str],
        env_vars: Dict[str, str],
        deploy_strategy: str = "rolling",
        enable_environments: bool = True,
        deployment_config_path: str = "deployment.yml"
    ) -> str:
        """Generate Jenkins Pipeline (Jenkinsfile)"""
        
        pipeline = f"""pipeline {{
    agent {{ label '{agent}' }}
    
    environment {{
        DOCKER_IMAGE = '{docker_image}'
        DOCKERFILE = '{dockerfile}'
"""
        
        # Add environment variables
        for key, value in env_vars.items():
            pipeline += f"        {key} = '{value}'\n"
        
        pipeline += """    }
    
    stages {
"""
        
        # Build stage
        if "build" in stages:
            pipeline += f"""        stage('Build') {{
            steps {{
                script {{
                    def image = docker.build("${{DOCKER_IMAGE}}", "-f ${{DOCKERFILE}} .")
                    docker.withRegistry('', '{credentials_id}') {{
                        image.push()
                    }}
                }}
            }}
        }}
"""
        
        # Test stage
        if "test" in stages:
            pipeline += f"""        stage('Test') {{
            steps {{
                sh 'docker run --rm ${{DOCKER_IMAGE}} npm test'
                sh 'docker run --rm ${{DOCKER_IMAGE}} npm run lint'
            }}
        }}
"""
        
        # Deploy stages
        if "deploy" in stages:
            if enable_environments:
                # Deploy to DEV
                pipeline += f"""        stage('Deploy to DEV') {{
            when {{
                branch 'develop'
            }}
            steps {{
                sh 'docker pull ${{DOCKER_IMAGE}}'
                sh 'dockerpilot deploy config {deployment_config_path} --type rolling'
            }}
        }}
        
        stage('Deploy to STAGING') {{
            when {{
                branch 'staging'
            }}
            steps {{
                input message: 'Deploy to STAGING?', ok: 'Deploy'
                sh 'docker pull ${{DOCKER_IMAGE}}'
                sh 'dockerpilot promote dev staging --config {deployment_config_path}'
            }}
        }}
        
        stage('Deploy to PROD') {{
            when {{
                branch 'main'
            }}
            steps {{
                input message: 'Deploy to PRODUCTION?', ok: 'Deploy'
                sh 'docker pull ${{DOCKER_IMAGE}}'
                sh 'dockerpilot promote staging prod --config {deployment_config_path}'
            }}
        }}
"""
            else:
                # Single deploy stage (backward compatibility)
                pipeline += f"""        stage('Deploy') {{
            steps {{
                sh 'docker pull ${{DOCKER_IMAGE}}'
                sh 'dockerpilot deploy config {deployment_config_path} --type {deploy_strategy}'
            }}
        }}
"""
        
        pipeline += """    }
    
    post {
        success {
            echo 'Pipeline succeeded!'
            archiveArtifacts artifacts: '**/*.log', allowEmptyArchive: true
        }
        failure {
            echo 'Pipeline failed!'
        }
        always {
            cleanWs()
        }
    }
}
"""
        
        return pipeline


def parse_env_vars(env_text: str) -> Dict[str, str]:
    """Parse environment variables from text"""
    env_vars = {}
    for line in env_text.strip().split("\n"):
        line = line.strip()
        if line and "=" in line:
            key, value = line.split("=", 1)
            env_vars[key.strip()] = value.strip()
    return env_vars


def generate_deployment_config_for_environment(
    base_config: Dict,
    environment: str,
    image_tag: str,
    container_name: str
) -> Dict:
    """Generate deployment configuration for specific environment"""
    
    env_configs = {
        'dev': {
            'replicas': 1,
            'cpu_limit': '0.5',
            'memory_limit': '512m',
            'image_tag_suffix': '-dev',
            'port_mapping': {'8080': '8080'},
            'environment': {'ENV': 'development', 'LOG_LEVEL': 'debug'}
        },
        'staging': {
            'replicas': 2,
            'cpu_limit': '1.0',
            'memory_limit': '1g',
            'image_tag_suffix': '-staging',
            'port_mapping': {'8080': '8081'},
            'environment': {'ENV': 'staging', 'LOG_LEVEL': 'info'}
        },
        'prod': {
            'replicas': 3,
            'cpu_limit': '2.0',
            'memory_limit': '2g',
            'image_tag_suffix': '',
            'port_mapping': {'8080': '8080'},
            'environment': {'ENV': 'production', 'LOG_LEVEL': 'warn'}
        }
    }
    
    if environment not in env_configs:
        environment = 'dev'
    
    env_config = env_configs[environment]
    
    # Merge with base config
    deployment = base_config.get('deployment', {})
    deployment.update({
        'image_tag': f"{image_tag}{env_config['image_tag_suffix']}",
        'container_name': f"{container_name}-{environment}",
        'cpu_limit': env_config['cpu_limit'],
        'memory_limit': env_config['memory_limit'],
        'port_mapping': env_config['port_mapping'],
        'environment': {**deployment.get('environment', {}), **env_config['environment']},
        'restart_policy': deployment.get('restart_policy', 'unless-stopped'),
        'health_check_endpoint': deployment.get('health_check_endpoint', '/health'),
        'health_check_timeout': deployment.get('health_check_timeout', 30),
        'health_check_retries': deployment.get('health_check_retries', 10),
        'network': deployment.get('network', 'bridge')
    })
    
    return {
        'deployment': deployment,
        'build': base_config.get('build', {})
    }

