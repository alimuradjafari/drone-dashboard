# Drone Dashboard

A full-stack application for monitoring and managing drone telemetry in real-time. This system collects MAVLink telemetry data (via a companion computer like a Jetson Nano), processes it with a Python FastAPI backend, and displays it on an interactive web dashboard.

## Project Structure

- `drone-backend/` - FastAPI backend server. Handles database connections, authentication, and WebSocket communication with the dashboard.
- `raspberry-pi/` - Scripts for running on a companion computer (e.g., Raspberry Pi) to forward MAVLink telemetry to the backend.
- `index.html`, `script.js`, `styles.css` - The frontend web dashboard for real-time monitoring.
- `login.html`, `login.css` - Authentication pages for the dashboard.

## Features

- **Real-time Telemetry:** View live drone metrics including altitude, speed, battery level, and GPS coordinates.
- **WebSocket Streaming:** Fast, bi-directional data flow between the backend and the dashboard.
- **Authentication:** Secure login system to protect dashboard access.
- **Database Integration:** Stores drone data and user accounts securely.

## Setup & Installation

### 1. Backend Setup
```bash
cd drone-backend
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit the .env file with your specific configuration
uvicorn main:app --reload
```

### 2. Frontend Setup
Serve the root directory using a local web server, for example:
```bash
python -m http.server 8000
```
Then navigate to `http://localhost:8000/login.html` in your web browser.

### 3. Raspberry Pi (Telemetry Forwarder)
```bash
cd raspberry-pi
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python mavlink_forwarder.py
```

## Screenshots



