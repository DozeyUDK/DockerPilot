# DockerPilot Extras - CI/CD Manager (Web)

Web application complementing [DockerPilot](https://github.com/DozeyUDK/DockerPilot) - graphical interface for managing CI/CD workflows for GitLab and Jenkins.

## Quick Start

> **Stability notice:** DockerPilotExtras is not yet stable and is under active development.

### 1. Install Dependencies

**One-time setup (recommended on Ubuntu/Debian 24.04+):**
```bash
cd DockerPilotExtras
chmod +x setup_extras.sh && ./setup_extras.sh
```
This creates a `.venv`, installs Python dependencies (avoids PEP 668 error), and optionally runs `npm install` in `frontend/` if Node.js is installed.

**Node.js** is not installed by the script. Install it if you need the frontend:
- Debian/Ubuntu: `sudo apt install nodejs npm`
- Or: https://nodejs.org/

**Manual install (if you prefer):**
```bash
# Backend (use venv on systems with PEP 668)
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Frontend
cd frontend && npm install
```

### 2. Development Mode

**Terminal 1 - Backend Flask:**
```bash
python run_dev.py
```
Backend will be available at `http://localhost:5000`

**Terminal 2 - Frontend React:**
```bash
cd frontend
npm run dev
```
Frontend will be available at `http://localhost:3000`

### 3. Production Mode

**1. Build frontend:**
```bash
cd frontend
npm run build
```

**2. Run backend (also serves frontend):**
```bash
python run_dev.py
```

Application will be available at `http://localhost:5000`

### Security Configuration

Set allowed browser origins and secure cookies explicitly in production:

```bash
# Comma-separated list of allowed origins
export CORS_ORIGINS="https://extras.example.com"

# Ensure cookies are marked Secure when behind HTTPS
export SESSION_COOKIE_SECURE=true
```

### 4. Using Loader Script (Recommended)

For easier startup, use the loader script that starts both backend and frontend:

```bash
.venv/bin/python loader.py
# or after: source .venv/bin/activate
python loader.py
```

This will automatically:
- Check dependencies
- Start Flask backend
- Start React frontend
- Handle port conflicts
- Stop both servers on Ctrl+C

## Features

- **CI/CD Pipeline Generator** - Create pipelines for GitLab CI and Jenkins
- **Container CI/CD Flow** - Build, test, scan, deploy, smoke and rollback-ready templates
- **Environment Promotion** - Workflow dev → staging → prod
- **Status and Monitoring** - Check Docker and DockerPilot status

## Architecture

- **Backend**: Flask (Python) - REST API
- **Frontend**: React + Vite - Single Page Application
- **Integration**: DockerPilot CLI
- **State Storage**: file JSON (legacy/default) or PostgreSQL (single source of truth)

## State Storage (Single Source Of Truth)

DockerPilot Extras now supports PostgreSQL as persistent state backend for:
- servers configuration
- environment → server mapping
- deployment history

Default mode is still file-based (`~/.dockerpilot_extras/*.json`), but you can switch to PostgreSQL.

### PostgreSQL schema (versioned)

Schema is managed with migration metadata + checksum validation to avoid silent table drift.

Tables:
- `dp_schema_migrations` - migration version/checksum history
- `dp_servers` - servers and auth payload (JSONB for secrets/metadata)
- `dp_settings` - global settings (for example `default_server`)
- `dp_env_servers` - environment mapping (`dev/staging/prod` → `server_id`)
- `dp_deployment_history` - deployment execution history

If migration checksum doesn't match expected schema version, backend refuses to continue in PostgreSQL mode.

### Configure storage backend

Using environment variables:

```bash
export DP_STORAGE_BACKEND=postgres
export DP_POSTGRES_HOST=127.0.0.1
export DP_POSTGRES_PORT=5432
export DP_POSTGRES_DB=dockerpilot_extras
export DP_POSTGRES_USER=postgres
export DP_POSTGRES_PASSWORD=your-password
export DP_POSTGRES_SSLMODE=prefer
export DP_POSTGRES_SCHEMA=DockerPilot
export DP_POSTGRES_TABLE_PREFIX=dp_
export DP_POSTGRES_AUTO_CREATE_SCHEMA=false
python run_dev.py
```

PostgreSQL layout controls:
- `schema` (default: `DockerPilot`)
- `table_prefix` (default: `dp_`)
- `tables` (optional explicit names per role)
- `auto_create_schema` (default: `false`; set `true` only when account can create schema)

This allows using a restricted DB account (DevOps/AppOps) without full DBA privileges.

Or using API:
- `POST /api/storage/configure` with `{"backend":"postgres","postgres":{...},"migrate_from_file":true}`
- `POST /api/storage/configure` with `{"backend":"file"}` to switch back

Example with explicit schema and table prefix:
```bash
curl -X POST http://localhost:5000/api/storage/configure \
  -H "Content-Type: application/json" \
  -d '{
    "backend":"postgres",
    "postgres":{
      "host":"127.0.0.1",
      "port":5432,
      "database":"dockerpilot_extras",
      "user":"postgres",
      "password":"secret",
      "schema":"DockerPilot",
      "table_prefix":"dp_",
      "auto_create_schema":true
    },
    "migrate_from_file":true
  }'
```

### Use existing local container (`postgres-dozeyserver`)

1. Discover runtime params:
```bash
curl http://localhost:5000/api/storage/discover-local-postgres?container_name=postgres-dozeyserver
```

2. Configure PostgreSQL storage:
```bash
curl -X POST http://localhost:5000/api/storage/configure \
  -H "Content-Type: application/json" \
  -d '{"backend":"postgres","container_name":"postgres-dozeyserver","migrate_from_file":true}'
```

### Bootstrap PostgreSQL container next to app

```bash
curl -X POST http://localhost:5000/api/storage/bootstrap-local-postgres \
  -H "Content-Type: application/json" \
  -d '{
    "container_name":"postgres-dozeyserver",
    "image":"postgres:16-alpine",
    "host_port":5432,
    "database":"dockerpilot_extras",
    "user":"dockerpilot",
    "password":"change-me-now",
    "configure_storage":true,
    "migrate_from_file":true
  }'
```

## Requirements

- Python 3.9+
- Node.js 18+ and npm
- Docker 20.10+
- DockerPilot installed and available in PATH

## Installation

### 1. Backend (Flask)

```bash
# Install Python dependencies
pip install -r requirements.txt

# Configure environment variables (optional)
cp .env.example .env
# Edit .env if you need to change configuration
```

### 2. Frontend (React)

```bash
cd frontend

# Install Node.js dependencies
npm install

# Or if you use yarn
yarn install
```

### 3. DockerPilot Verification

```bash
# Check if DockerPilot is installed
dockerpilot --version

# If not, install from: https://github.com/DozeyUDK/DockerPilot
```

## Basic Usage

### Pipeline Generator

1. Open `http://localhost:3000` (dev) or `http://localhost:5000` (prod)
2. Select **"CI/CD Pipelines"** tab
3. Fill in the form:
   - Type: GitLab CI or Jenkins
   - Project name
   - Docker Image
   - Stages (build, test, scan, deploy, smoke)
4. Click **"Generate Pipeline"**
5. View preview and save/download

### Environment Promotion

1. Go to **"Environments"** tab
2. Use promotion buttons: DEV → STAGING → PROD
3. Confirm promotion
4. If DEV and Pre-Prod share one server, UI uses environment-scoped assignments to avoid duplicate full-host container lists.

### Status

1. Go to **"Status"** tab
2. Check Docker and DockerPilot status (local or selected remote server scope)
3. Run **Setup Preflight** to verify local Extras dependencies (Python/Node/npm/Docker/DockerPilot)
4. View container list

## Hosting

### On the Same Host as DockerPilot

If DockerPilot runs on `your-host:8080`, you can run DockerPilot Extras on `your-host:5000`:

```bash
export PORT=5000
python run_dev.py
```

### Configuration with Reverse Proxy (Nginx)

```nginx
# /etc/nginx/sites-available/dockerpilot-extras
server {
    listen 80;
    server_name your-domain.com;

    location /extras/ {
        proxy_pass http://127.0.0.1:5000/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Configuration with DockerPilot

If DockerPilot runs on port 8080, you can configure DockerPilot Extras on port 5000:

```bash
# Set environment variable
export PORT=5000

# Run application
python run_dev.py
```

## Project Structure

```
DockerPilotExtras/
├── backend/
│   ├── app.py              # Flask main application
│   ├── config.py           # Configuration
│   └── __init__.py
├── frontend/
│   ├── src/
│   │   ├── pages/          # Page components
│   │   ├── services/       # API services
│   │   ├── App.jsx         # Main component
│   │   └── main.jsx
│   ├── package.json
│   └── vite.config.js
├── utils/
│   └── pipeline_generator.py
├── requirements.txt
├── run_dev.py
└── README.md
```

## API Endpoints

### Pipeline
- `POST /api/pipeline/generate` - Generate pipeline
- `POST /api/pipeline/save` - Save pipeline

### Deployment
- `GET /api/deployment/config` - Get configuration
- `POST /api/deployment/config` - Save configuration
- `POST /api/deployment/execute` - Execute deployment
- `GET /api/deployment/history` - Deployment history

### Environment
- `POST /api/environment/promote` - Promote environment
- `GET /api/environment/container-bindings` - Get explicit environment → container assignment map
- `PUT /api/environment/container-bindings` - Update explicit environment → container assignment map

### Status
- `GET /api/status` - Docker and DockerPilot status with context (local/remote)
- `GET /api/preflight` - Setup preflight checks (Python deps, Node/npm, Docker, DockerPilot)
- `GET /api/containers` - Container list
- `GET /api/health` - Health check

### Storage
- `GET /api/storage/status` - Active storage backend and health/schema info
- `POST /api/storage/test-postgres` - Test PostgreSQL connectivity
- `GET /api/storage/discover-local-postgres` - Inspect existing local PostgreSQL container
- `POST /api/storage/bootstrap-local-postgres` - Create/start local PostgreSQL container
- `POST /api/storage/configure` - Switch storage backend (`file`/`postgres`) and migrate state

## Troubleshooting

### Backend won't start

```bash
# Check if all dependencies are installed
pip install -r requirements.txt

# Check if port 5000 is free
netstat -an | grep 5000
```

### Frontend doesn't connect to backend

- Check if backend is running on port 5000
- Check proxy configuration in `frontend/vite.config.js`
- In production mode, make sure frontend is built

### DockerPilot not found

```bash
# Check if DockerPilot is in PATH
which dockerpilot  # Linux/Mac
where dockerpilot  # Windows

# Check if it works
dockerpilot --version
```

### CORS Errors

Backend has CORS enabled by default for all sources. In production, configure `CORS_ORIGINS` in `.env`.

## Security

- **SECRET_KEY**: Change `SECRET_KEY` in production (set via `SECRET_KEY` environment variable)
- **CORS**: Configure `CORS_ORIGINS` to limit access (set via `CORS_ORIGINS` environment variable)
- **HTTPS**: Use HTTPS in production
- **Authentication**: Consider adding authentication for API endpoints
- **Credentials**: All passwords and SSH keys are stored in user's home directory (`~/.dockerpilot_extras/`) and never hardcoded in the application

### Environment Variables

Create `.env` file (optional):
```bash
PORT=5000
FLASK_ENV=development
SECRET_KEY=your-secret-key-here
CORS_ORIGINS=http://localhost:3000,http://localhost:5000
```

## API Documentation

API documentation is available via `/api/health` endpoint and source code in `backend/app.py`.

## Support

If you encounter issues:
1. Check backend and frontend logs
2. Verify Docker and DockerPilot status
3. Report an issue in the repository

## License

MIT License - see [LICENSE](LICENSE)

## Related Projects

- [DockerPilot](https://github.com/DozeyUDK/DockerPilot) - Main Docker management tool

---

**DockerPilot Extras** - Web-based CI/CD Manager for DockerPilot
