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
<img width="1920" height="961" alt="log" src="https://github.com/user-attachments/assets/bd1e9f73-4b9f-460b-aa7a-6ad5ab7f9140" />
<img width="1440" height="899" alt="dashb" src="https://github.com/user-attachments/assets/eb3d7c0f-498b-416a-afaf-7120422bce70" />

<img width="1918" height="1021" alt="gps" src="https://github.com/user-attachments/assets/1feb81f1-4ef4-45cb-9ae8-d8858bfa0e2f" />

<img width="1899" height="1023" alt="multi" src="https://github.com/user-attachments/assets/124eeee9-6355-4226-98d4-2474ce1bdb3f" />


