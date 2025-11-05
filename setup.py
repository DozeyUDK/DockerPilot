from setuptools import setup, find_packages

with open("requirements.txt", "r", encoding="utf-8") as f:
    requirements = [line.strip() for line in f if line.strip() and not line.startswith("#")]

setup(
    name="dockerpilot",
    version="0.1.0",
    description="Docker container management tool with advanced deployment capabilities",
    author="dozey",
    author_email="dozeynwct@hotmail.com",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    python_requires=">=3.9",
    install_requires=requirements,
    extras_require={
        "git": ["GitPython>=3.1.0"],
        "test": ["pytest>=7.0.0", "pytest-cov>=4.0.0"],
    },
    entry_points={
        "console_scripts": [
            "dockerpilot=dockerpilot.main:main",
        ],
    },
)
