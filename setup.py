from setuptools import setup, find_packages

setup(
    name="dockerpilot",
    version="0.1.0",
    description="DockerPilot CLI tool",
    author="dozey",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    python_requires=">=3.9",
    install_requires=[],
    entry_points={
        "console_scripts": [
            "dockerpilot=dockerpilot.main:main",
        ],
    },
)
