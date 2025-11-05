# Pre-Release Checklist

Use this checklist before pushing to GitHub:

## ✅ Files Check

- [ ] `.gitignore` - up to date, excludes logs and cache files
- [ ] `LICENSE` - MIT license file exists
- [ ] `CHANGELOG.md` - updated with current version
- [ ] `CONTRIBUTING.md` - contribution guidelines added
- [ ] `README.md` - updated and complete
- [ ] `.gitattributes` - line endings configured

## ✅ Code Quality

- [ ] No hardcoded passwords, API keys, or secrets
- [ ] No debug print statements left in code
- [ ] All imports are used
- [ ] No TODO comments in production code
- [ ] Code follows style guidelines

## ✅ Configuration Files

- [ ] `setup.py` and `pyproject.toml` are synchronized
- [ ] `requirements.txt` has version pins
- [ ] Entry points are correctly configured
- [ ] All template files are in place

## ✅ Documentation

- [ ] README.md is complete and accurate
- [ ] Installation instructions work
- [ ] Examples are correct
- [ ] All links work

## ✅ Git Status

Run these commands to check:

```bash
# Check what files are tracked
git status

# Check for sensitive data
git grep -i "password\|secret\|api_key\|token" -- :!*.template

# Check for large files (>1MB)
find . -type f -size +1M -not -path "./.git/*"

# Check for log files
find . -name "*.log" -not -path "./.git/*"
```

## ✅ Remove Unwanted Files

If you find files that shouldn't be in the repo:

```bash
# Remove from git (but keep locally)
git rm --cached src/dockerpilot/docker_pilot.log

# Remove from git completely
git rm src/dockerpilot/docker_pilot.log
```

## ✅ Final Checks

- [ ] All tests pass (if available)
- [ ] Installation scripts work on all platforms
- [ ] Documentation is up to date
- [ ] Version numbers are correct
- [ ] Author information is correct

## 🚀 Ready to Push

### Quick Release (One-Click)

**Linux/macOS:**
```bash
chmod +x prepare_release.sh
./prepare_release.sh
```

**Windows (PowerShell):**
```powershell
powershell -ExecutionPolicy Bypass -File prepare_release.ps1
```

### Manual Release Steps

When ready:

```bash
# Initialize git (if not already done)
git init

# Add all files
git add .

# Commit with descriptive message
git commit -m "Initial release: Docker Pilot v0.1.0"

# Add remote repository (replace with your GitHub URL)
git remote add origin https://github.com/DozeyUDK/DockerPilot.git

# Check what will be pushed
git status

# Push to GitHub
git branch -M main
git push -u origin main

# Tag the release (optional but recommended)
git tag -a v0.1.0 -m "Release version 0.1.0"
git push origin --tags
```

### Create GitHub Release

After pushing:

1. Go to GitHub repository
2. Click "Releases" → "Create a new release"
3. Choose tag: `v0.1.0`
4. Title: `Docker Pilot v0.1.0`
5. Description: Copy from `CHANGELOG.md`
6. Publish release

## 📋 Post-Release

After pushing:

- [ ] Create a GitHub release with release notes
- [ ] Update any external documentation
- [ ] Announce the release (if applicable)

