# DockerPilot Extras - Quick Start Guide (Web)

## 🚀 Quick Start

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

## 🌐 Hosting on the Same Host

If DockerPilot runs on `your-host:8080`, you can run DockerPilot Extras on `your-host:5000`:

```bash
export PORT=5000
python run_dev.py
```

Or use reverse proxy (Nginx) to expose on the same port with different paths.

## 🔧 Environment Variables

Create `.env` file (optional):
```bash
cp .env.example .env
```

Edit `.env`:
```env
PORT=5000
FLASK_ENV=development
SECRET_KEY=your-secret-key
```

---

**Ready?** Run backend and frontend, then open the application in your browser! 🚀
