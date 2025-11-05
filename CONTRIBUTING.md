# Contributing to Docker Pilot

Thank you for your interest in contributing to Docker Pilot! 🚀

## Getting Started

1. **Fork the repository**
   ```bash
   git clone https://github.com/YOUR_USERNAME/DockerPilot.git
   cd DockerPilot
   ```

2. **Set up development environment**
   ```bash
   # Install dependencies
   pip install -r requirements.txt
   pip install -e .[test]
   ```

3. **Create a branch**
   ```bash
   git checkout -b feature/your-feature-name
   ```

## Development Guidelines

### Code Style
- Follow PEP 8 Python style guide
- Use type hints where appropriate
- Write clear, descriptive variable and function names
- Add docstrings to all public functions and classes

### Testing
- Write tests for new features
- Ensure all tests pass before submitting:
  ```bash
  pytest tests/
  ```
- Run linting:
  ```bash
  # Add linting tool if needed
  ```

### Commit Messages
- Use clear, descriptive commit messages
- Start with a verb (Add, Fix, Update, Remove, etc.)
- Reference issue numbers if applicable: `Fix #123: Description`

Example:
```
Add: Support for Docker Compose files
Fix: Memory leak in monitoring module
Update: Documentation for deployment strategies
```

## Pull Request Process

1. **Update documentation** if needed (README.md, CHANGELOG.md)
2. **Add tests** for new functionality
3. **Ensure all tests pass** and code is clean
4. **Update CHANGELOG.md** with your changes
5. **Create a pull request** with:
   - Clear description of changes
   - Reference to related issues (if any)
   - Screenshots/examples if applicable

### PR Template
```markdown
## Description
Brief description of changes

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Documentation update
- [ ] Performance improvement
- [ ] Refactoring

## Testing
- [ ] Tests pass locally
- [ ] Manual testing completed

## Checklist
- [ ] Code follows style guidelines
- [ ] Documentation updated
- [ ] CHANGELOG.md updated
- [ ] No new warnings introduced
```

## Reporting Issues

When reporting bugs or requesting features:

1. **Check existing issues** to avoid duplicates
2. **Use the issue template** with:
   - Clear description
   - Steps to reproduce (for bugs)
   - Expected vs actual behavior
   - Environment details (OS, Python version, Docker version)
   - Logs or error messages

## Code of Conduct

- Be respectful and inclusive
- Focus on constructive feedback
- Help others learn and improve
- Follow the project's coding standards

## Questions?

- Open an issue for questions or discussions
- Check existing documentation in README.md
- Review closed issues for similar questions

Thank you for contributing! 🙏

