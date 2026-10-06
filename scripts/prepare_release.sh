#!/bin/bash

# Docker Pilot - Release Preparation Script
# This script prepares the repository for GitHub release

set -e

VERSION=$(awk -F'"' '/^version = "/ {print $2; exit}' pyproject.toml)
if [ -z "$VERSION" ]; then
    echo "Could not resolve DockerPilot version from pyproject.toml" >&2
    exit 1
fi

echo "🚀 Docker Pilot - Release Preparation v$VERSION"
echo "======================================"
echo ""

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Check if git is initialized
if [ ! -d ".git" ]; then
    echo -e "${YELLOW}⚠️  Git repository not initialized${NC}"
    echo "Initializing git repository..."
    git init
    echo -e "${GREEN}✅ Git repository initialized${NC}"
fi

# Check for unwanted files
echo ""
echo "📋 Checking for unwanted files..."
UNWANTED_FILES=()

# Check for log files
if [ -f "src/dockerpilot/docker_pilot.log" ]; then
    UNWANTED_FILES+=("src/dockerpilot/docker_pilot.log")
fi

# Check for cache directories
if [ -d "__pycache__" ]; then
    UNWANTED_FILES+=("__pycache__")
fi

if [ ${#UNWANTED_FILES[@]} -gt 0 ]; then
    echo -e "${YELLOW}⚠️  Found files that should be removed:${NC}"
    for file in "${UNWANTED_FILES[@]}"; do
        echo "   - $file"
    done
    echo ""
    read -p "Remove these files from git tracking? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        for file in "${UNWANTED_FILES[@]}"; do
            if [ -f "$file" ]; then
                git rm --cached "$file" 2>/dev/null || echo "File not tracked: $file"
            fi
        done
        echo -e "${GREEN}✅ Files removed from git tracking${NC}"
    fi
else
    echo -e "${GREEN}✅ No unwanted files found${NC}"
fi

# Check for sensitive data
echo ""
echo "🔒 Checking for sensitive data..."
SENSITIVE_PATTERNS=("password" "secret" "api_key" "token" "credential")
FOUND_SENSITIVE=false

for pattern in "${SENSITIVE_PATTERNS[@]}"; do
    if grep -r -i "$pattern" --include="*.py" --include="*.yml" --include="*.yaml" --exclude-dir=".git" --exclude="*.template" . 2>/dev/null | grep -v "credentials\|password\|token" | grep -v "#\|template\|example" > /dev/null; then
        echo -e "${YELLOW}⚠️  Found potential sensitive data: $pattern${NC}"
        FOUND_SENSITIVE=true
    fi
done

if [ "$FOUND_SENSITIVE" = false ]; then
    echo -e "${GREEN}✅ No sensitive data found${NC}"
fi

# Check file sizes
echo ""
echo "📦 Checking for large files..."
LARGE_FILES=$(find . -type f -size +1M -not -path "./.git/*" -not -path "./.venv/*" -not -path "./venv/*" 2>/dev/null || true)
if [ -z "$LARGE_FILES" ]; then
    echo -e "${GREEN}✅ No large files found${NC}"
else
    echo -e "${YELLOW}⚠️  Found large files:${NC}"
    echo "$LARGE_FILES"
fi

# Summary
echo ""
echo "======================================"
echo -e "${GREEN}✅ Release preparation complete!${NC}"
echo ""
echo "Next steps:"
echo "  1. Review changes: git status"
echo "  2. Add files: git add ."
echo "  3. Commit: git commit -m 'chore(release): prepare DockerPilot v$VERSION'"
echo "  4. Tag after review/merge: git tag -a v$VERSION -m 'DockerPilot v$VERSION'"
echo "  5. Push the reviewed branch/tag through the normal release workflow"
echo ""

