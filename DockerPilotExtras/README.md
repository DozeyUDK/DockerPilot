# DockerPilot Extras - CI/CD Manager (Web)

Web application complementing [DockerPilot](https://github.com/DozeyUDK/DockerPilot) - graphical interface for managing CI/CD workflows for GitLab and Jenkins.

## 🚀 Quick Start

> **Stability notice:** DockerPilotExtras is not yet stable and is under active development.

### 1. Install Dependencies

**Backend:**
```bash
pip install -r requirements.txt
```

**Frontend:**
```bash
cd frontend
npm install
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

### 4. Using Loader Script (Recommended)

For easier startup, use the loader script that starts both backend and frontend:

```bash
python loader.py
```

This will automatically:
- Check dependencies
- Start Flask backend
- Start React frontend
- Handle port conflicts
- Stop both servers on Ctrl+C

## 🎯 Features

- **CI/CD Pipeline Generator** - Create pipelines for GitLab CI and Jenkins
- **Deployment Management** - Visual management and execution of deployments
- **Environment Promotion** - Workflow dev → staging → prod
- **Status and Monitoring** - Check Docker and DockerPilot status

## 🏗️ Architecture

- **Backend**: Flask (Python) - REST API
- **Frontend**: React + Vite - Single Page Application
- **Integration**: DockerPilot CLI

## 📋 Requirements

- Python 3.8+
- Node.js 16+ and npm
- Docker 20.10+
- DockerPilot installed and available in PATH

## 🚀 Installation

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

## 📖 Basic Usage

### Pipeline Generator

1. Open `http://localhost:3000` (dev) or `http://localhost:5000` (prod)
2. Select **"CI/CD Pipelines"** tab
3. Fill in the form:
   - Type: GitLab CI or Jenkins
   - Project name
   - Docker Image
   - Stages (build, test, deploy)
4. Click **"Generate Pipeline"**
5. View preview and save/download

### Deployment

1. Go to **"Deployments"** tab
2. Edit YAML configuration
3. Select deployment strategy
4. Click **"Execute Deployment"**

### Environment Promotion

1. Go to **"Environments"** tab
2. Use promotion buttons: DEV → STAGING → PROD
3. Confirm promotion

### Status

1. Go to **"Status"** tab
2. Check Docker and DockerPilot status
3. View container list

## 🌐 Hosting

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

## 📁 Project Structure

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

## 🔧 API Endpoints

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

### Status
- `GET /api/status` - Docker and DockerPilot status
- `GET /api/containers` - Container list
- `GET /api/health` - Health check

## 🐛 Troubleshooting

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

## 🔒 Security

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

## 📚 API Documentation

API documentation is available via `/api/health` endpoint and source code in `backend/app.py`.

## 🤝 Support

If you encounter issues:
1. Check backend and frontend logs
2. Verify Docker and DockerPilot status
3. Report an issue in the repository

## 📝 License

MIT License - see [LICENSE](LICENSE)

## 🔗 Related Projects

- [DockerPilot](https://github.com/DozeyUDK/DockerPilot) - Main Docker management tool

---

**DockerPilot Extras** - Web-based CI/CD Manager for DockerPilot
