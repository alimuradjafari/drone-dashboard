// ============================================================
// DRONE CHARGING STATION DASHBOARD - MAIN JAVASCRIPT
// ============================================================

/**
 * TELEMETRY DATA MANAGER
 * Handles WebSocket connection and data state
 */
class TelemetryManager {
    constructor() {
        // Default telemetry state
        this.data = {
            droneId: 'Drone-001',
            connectionStatus: 'Connected',
            armed: false,
            flightMode: 'Stabilize',
            missionStatus: 'Idle',
            position: { lat: 37.7749, lng: -122.4194, alt: 42, heading: 127 },
            navigation: { groundSpeed: 8.4, distanceFromHome: 312 },
            attitude: { roll: 2, pitch: -5, yaw: 127 },
            gps: { fixType: '3D Fix', satellites: 14, hdop: 0.82 },
            battery: { percent: 72, voltage: 22.4, current: 18.6, capacity: 3240, timeLeft: 11, health: 'Healthy' },
            charging: { docked: false, status: 'Standby', progress: 62, voltage: 24.6, current: 4.2, eta: 18 },
            communication: { rssi: -68, linkStatus: 'Active', packetLoss: 0.4, lastUpdate: Date.now() },
            alerts: [
                { type: 'ok', icon: 'ti-check', message: 'All systems nominal' }
            ]
        };
        
        this.listeners = [];
        this.ws = null;
        this.reconnectTimer = null;
        this.updateInterval = null;
        this.batteryAlertLevel = null;
        this.linkAlertActive = false;
        this.isSimulating = new URLSearchParams(window.location.search).get('demo') === '1';
        
        // Start simulation
        if (this.isSimulating) this.startSimulation();
    }
    
    /**
     * Start simulated telemetry updates (for testing without hardware)
     */
    startSimulation() {
        if (this.updateInterval) clearInterval(this.updateInterval);
        
        this.updateInterval = setInterval(() => {
            if (!this.isSimulating) return;
            
            // Only simulate if not connected to WebSocket
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                return; // WebSocket is handling updates
            }
            
            // Simulate telemetry changes
            const lat = this.data.position.lat + (Math.random() - 0.5) * 0.0001;
            const lng = this.data.position.lng + (Math.random() - 0.5) * 0.0001;
            const alt = Math.max(0, this.data.position.alt + (Math.random() - 0.5) * 2);
            let heading = (this.data.position.heading + (Math.random() - 0.5) * 5) % 360;
            if (heading < 0) heading += 360;
            
            const groundSpeed = Math.max(0, this.data.navigation.groundSpeed + (Math.random() - 0.5) * 1);
            const distanceHome = Math.max(0, this.data.navigation.distanceFromHome + (Math.random() - 0.5) * 2);
            const batteryPercent = Math.max(0, this.data.battery.percent - (Math.random() * 0.3));
            
            this.updateData({
                position: { lat, lng, alt, heading },
                navigation: { groundSpeed, distanceFromHome: distanceHome },
                battery: { percent: Math.round(batteryPercent) },
                communication: { lastUpdate: Date.now() }
            });
        }, 1500);
    }
    
    /**
     * Connect to WebSocket for real telemetry
     */
    connectWebSocket(url = TelemetryManager.defaultWebSocketUrl()) {
        try {
            console.log('🔌 Connecting to WebSocket:', url);
            this.ws = new WebSocket(url);
            
            this.ws.onopen = () => {
                console.log(' WebSocket connected successfully!');
                this.isSimulating = false;
                
                // CRITICAL FIX: Kill the background simulation interval completely 
                // to prevent deepMerge from erasing live backend fields.
                if (this.updateInterval) {
                    clearInterval(this.updateInterval);
                    this.updateInterval = null;
                }
                
                this.updateData({ 
                    connectionStatus: 'Connected',
                    communication: { linkStatus: 'Connected' }
                });
                this.addAlert('Connected to telemetry server', 'ok');
            };
            
            this.ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    
                    // Update data wrapper dynamically while respecting backend flags
                    this.updateData({
                        ...data,
                        connectionStatus: data.connectionStatus || 'Disconnected',
                        communication: { 
                            ...data.communication,
                            lastUpdate: Date.now() 
                        }
                    });
                    
                    // Trigger tracking alert filters if connected
                    if (data.connectionStatus !== 'Disconnected') {
                        this.linkAlertActive = false;
                        this.updateBatteryAlert(data.battery);
                    } else {
                        if (!this.linkAlertActive) {
                            this.addAlert('Hardware communication link interrupted.', 'crit');
                            this.linkAlertActive = true;
                        }
                    }
                    
                } catch (error) {
                    console.error('❌ Failed to parse WebSocket message:', error);
                }
            };
            
            this.ws.onclose = () => {
                console.log('❌ WebSocket disconnected');
                this.updateData({ 
                    connectionStatus: 'Disconnected',
                    communication: { linkStatus: 'Disconnected' }
                });
                this.addAlert('Lost connection to telemetry server', 'crit');
                
                if (this.isSimulating) this.startSimulation();
                if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
                this.reconnectTimer = setTimeout(() => {
                    console.log(' Attempting to reconnect...');
                    this.connectWebSocket(url);
                }, 5000);
            };
            
            this.ws.onerror = (error) => {
                console.error('❌ WebSocket error:', error);
                this.ws.close();
            };
        } catch (error) {
            console.error('❌ WebSocket connection failed:', error);
            // Remain disconnected in production; append ?demo=1 to enable simulation.
            if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
            this.reconnectTimer = setTimeout(() => {
                console.log('🔄 Retrying connection...');
                this.connectWebSocket(url);
            }, 5000);
        }
    }

    static defaultWebSocketUrl() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const hostname = window.location.hostname || 'localhost';
        const url = new URL(`${protocol}//${hostname}:8000/ws/telemetry`);
        const apiKey = window.localStorage.getItem('dashboardApiKey');
        if (apiKey) url.searchParams.set('token', apiKey);
        return url.toString();
    }
    
    /**
     * Update telemetry data
     */
    updateData(newData) {
        this.data = this.deepMerge(this.data, newData);
        this.notifyListeners();
    }
    
    /**
     * Deep merge helper
     */
    deepMerge(target, source) {
        const result = { ...target };
        for (const key in source) {
            if (source[key] && typeof source[key] === 'object' && !Array.isArray(source[key])) {
                result[key] = this.deepMerge(target[key] || {}, source[key]);
            } else {
                result[key] = source[key];
            }
        }
        return result;
    }
    
    /**
     * Add alert
     */
    addAlert(message, type = 'ok') {
        const alert = { type, icon: this.getAlertIcon(type), message };
        // De-duplicate repetitive system notifications
        if (this.data.alerts.length > 0 && this.data.alerts[0].message === message) return;
        this.data.alerts = [alert, ...this.data.alerts.slice(0, 9)];
        this.notifyListeners();
    }

    /** Add one alert only when the battery crosses into a new severity band. */
    updateBatteryAlert(battery) {
        if (!battery || typeof battery.percent !== 'number') return;

        const percent = Math.max(0, Math.min(100, Math.round(battery.percent)));
        const level = percent < 15 ? 'critical' : percent < 30 ? 'warning' : 'normal';
        if (level === this.batteryAlertLevel) return;

        const previousLevel = this.batteryAlertLevel;
        this.batteryAlertLevel = level;
        if (level === 'critical') {
            this.addAlert(`CRITICAL: Battery at ${percent}%!`, 'crit');
        } else if (level === 'warning') {
            this.addAlert(`Battery at ${percent}% - Land soon!`, 'warn');
        } else if (previousLevel === 'warning' || previousLevel === 'critical') {
            this.addAlert(`Battery recovered to ${percent}%`, 'ok');
        }
    }
    
    /**
     * Get alert icon based on type
     */
    getAlertIcon(type) {
        const icons = {
            'ok': 'ti-check',
            'warn': 'ti-alert-triangle',
            'crit': 'ti-x',
            'info': 'ti-info-circle'
        };
        return icons[type] || 'ti-check';
    }
    
    /**
     * Subscribe to telemetry updates
     */
    subscribe(listener) {
        this.listeners.push(listener);
        listener(this.data);
        return () => {
            this.listeners = this.listeners.filter(l => l !== listener);
        };
    }
    
    /**
     * Notify all listeners
     */
    notifyListeners() {
        for (const listener of this.listeners) {
            listener(this.data);
        }
    }
    
    /**
     * Send command to drone
     */
    sendCommand(command) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(command));
            return true;
        }
        console.warn('WebSocket not connected, command not sent');
        return false;
    }
    
    /**
     * Clean up resources
     */
    destroy() {
        if (this.updateInterval) clearInterval(this.updateInterval);
        if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
        if (this.ws) this.ws.close();
    }
}

// ============================================================
// DASHBOARD RENDERER
// ============================================================
class DashboardRenderer {
    constructor(telemetry) {
        this.telemetry = telemetry;
        this.map = null;
        this.marker = null;
        this.trail = [];
        this.trailPolyline = null;
        this.homeMarker = null;
        this.stationMarker = null;
        this.alertTimeout = null;
        
        // DOM element references
        this.elements = this.getElements();
        
        // Initialize map
        this.initMap();
        
        // Subscribe to telemetry updates
        this.telemetry.subscribe((data) => this.render(data));
        
        // Update time counter
        this.startTimeCounter();
    }
    
    /**
     * Get all DOM element references
     */
    getElements() {
        return {
            droneId: document.getElementById('droneId'),
            connBadge: document.getElementById('connBadge'),
            armedBadge: document.getElementById('armedBadge'),
            modeBadge: document.getElementById('modeBadge'),
            missionBadge: document.getElementById('missionBadge'),
            lastUpdateTime: document.getElementById('lastUpdateTime'),
            
            altitude: document.getElementById('altitude'),
            groundSpeed: document.getElementById('groundSpeed'),
            heading: document.getElementById('heading'),
            distanceHome: document.getElementById('distanceHome'),
            
            roll: document.getElementById('roll'),
            pitch: document.getElementById('pitch'),
            yaw: document.getElementById('yaw'),
            
            gpsFixBadge: document.getElementById('gpsFixBadge'),
            gpsSats: document.getElementById('gpsSats'),
            gpsHdop: document.getElementById('gpsHdop'),
            
            batteryPercent: document.getElementById('batteryPercent'),
            batteryHealth: document.getElementById('batteryHealth'),
            batteryBar: document.getElementById('batteryBar'),
            batteryVoltage: document.getElementById('batteryVoltage'),
            batteryCurrent: document.getElementById('batteryCurrent'),
            batteryCapacity: document.getElementById('batteryCapacity'),
            flightTimeLeft: document.getElementById('flightTimeLeft'),
            
            dockingStatus: document.getElementById('dockingStatus'),
            chargingStatus: document.getElementById('chargingStatus'),
            chargeProgressBar: document.getElementById('chargeProgressBar'),
            chargeProgress: document.getElementById('chargeProgress'),
            chargingVoltage: document.getElementById('chargingVoltage'),
            chargingCurrent: document.getElementById('chargingCurrent'),
            chargingEta: document.getElementById('chargingEta'),
            
            rssiValue: document.getElementById('rssiValue'),
            rssiBars: document.getElementById('rssiBars'),
            linkStatus: document.getElementById('linkStatus'),
            packetLoss: document.getElementById('packetLoss'),
            
            alertsGrid: document.getElementById('alertsGrid')
        };
    }
    
    /**
     * Initialize Leaflet map
     */
    initMap() {
        const mapContainer = document.getElementById('map');
        if (!mapContainer) return;
        
        this.map = L.map(mapContainer, {
            center: [37.7749, -122.4194],
            zoom: 18,
            zoomControl: true,
            attributionControl: false
        });
        
        L.tileLayer('https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', {
            maxZoom: 20,
            subdomains: ['mt1', 'mt2', 'mt3'],
            attribution: '&copy; Google'
        }).addTo(this.map);
        
        const droneIcon = L.divIcon({
            className: 'custom-drone-icon',
            html: `<div style="
                width: 24px;
                height: 24px;
                background: #0078c8;
                border-radius: 50%;
                border: 2px solid white;
                box-shadow: 0 2px 8px rgba(0, 120, 200, 0.4);
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 12px;
            "></div>`,
            iconSize: [24, 24],
            iconAnchor: [12, 12]
        });
        
        this.marker = L.marker([37.7749, -122.4194], {
            icon: droneIcon,
            zIndexOffset: 1000
        }).addTo(this.map);
        
        const homeIcon = L.divIcon({
            className: 'custom-home-icon',
            html: `<div style="
                width: 20px;
                height: 20px;
                background: #00b85c;
                border-radius: 50%;
                border: 2px solid white;
                box-shadow: 0 2px 8px rgba(0, 184, 92, 0.4);
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 10px;
            ">🏠</div>`,
            iconSize: [20, 20],
            iconAnchor: [10, 10]
        });
        
        this.homeMarker = L.marker([37.7700, -122.4200], {
            icon: homeIcon
        }).addTo(this.map);
        
        const stationIcon = L.divIcon({
            className: 'custom-station-icon',
            html: `<div style="
                width: 20px;
                height: 20px;
                background: #e67e00;
                border-radius: 50%;
                border: 2px solid white;
                box-shadow: 0 2px 8px rgba(230, 126, 0, 0.4);
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 10px;
            ">⚡</div>`,
            iconSize: [20, 20],
            iconAnchor: [10, 10]
        });
        
        this.stationMarker = L.marker([37.7710, -122.4190], {
            icon: stationIcon
        }).addTo(this.map);
        
        this.trailPolyline = L.polyline([], {
            color: '#0078c8',
            weight: 2,
            opacity: 0.6,
            dashArray: '5, 5'
        }).addTo(this.map);
        
        setTimeout(() => {
            this.map.invalidateSize();
        }, 100);
    }
    
    /**
     * Start time counter for last update
     */
    startTimeCounter() {
        setInterval(() => {
            const lastUpdate = this.telemetry.data.communication.lastUpdate;
            if (lastUpdate) {
                const seconds = Math.floor((Date.now() - lastUpdate) / 1000);
                this.elements.lastUpdateTime.textContent = `${seconds}s ago`;
            }
        }, 1000);
    }
    
    /**
     * Render telemetry data wrappers
     */
    render(data) {
        this.renderTopBar(data);
        this.renderMap(data);
        this.renderNavigation(data);
        this.renderAttitude(data);
        this.renderGPS(data);
        this.renderBattery(data);
        this.renderCharging(data);
        this.renderCommunication(data);
        this.renderAlerts(data);
    }
    
    /**
     * Render top status bar elements
     */
    renderTopBar(data) {
        this.elements.droneId.textContent = data.droneId || 'Drone-001';
        
        const connBadge = this.elements.connBadge;
        const isConnected = data.connectionStatus === 'Connected';
        connBadge.className = `badge ${isConnected ? 'badge-green' : 'badge-red'}`;
        connBadge.innerHTML = `<span class="dot ${isConnected ? 'pulse' : ''}"></span> ${data.connectionStatus}`;
        
        const armedBadge = this.elements.armedBadge;
        const isArmed = isConnected ? (data.armed || false) : false;
        armedBadge.className = `badge ${isArmed ? 'badge-red' : 'badge-green'}`;
        armedBadge.innerHTML = `<i class="ti ti-power"></i> ${isArmed ? 'Armed' : 'Disarmed'}`;
        
        this.elements.modeBadge.textContent = isConnected ? (data.flightMode || 'Stabilize') : 'Offline';
        this.elements.modeBadge.className = `badge ${isConnected ? 'badge-blue' : 'badge-gray'}`;
        
        const missionBadge = this.elements.missionBadge;
        const missionStatus = isConnected ? (data.missionStatus || 'Idle') : 'Unknown';
        missionBadge.textContent = missionStatus;
        const missionColors = {
            'Idle': 'badge-gray',
            'Taking Off': 'badge-blue',
            'En Route': 'badge-amber',
            'Landing': 'badge-amber',
            'Charging': 'badge-green',
            'Unknown': 'badge-gray'
        };
        missionBadge.className = `badge ${missionColors[missionStatus] || 'badge-gray'}`;
    }
    
    /**
     * Render map smoothly without high-frequency layout thrashing
     */
    renderMap(data) {
        const pos = data.position;
        if (!pos || typeof pos.lat !== 'number' || typeof pos.lng !== 'number' || !this.map || !this.marker) return;
        if (pos.lat === 0.0 && pos.lng === 0.0) return; // Prevent raw GPS lock jumps

        try {
            const targetLatLng = [pos.lat, pos.lng];
            
            // Move marker object instantly 
            this.marker.setLatLng(targetLatLng);
            
            // Pan map view bounds only if drone travels outside the center focal buffer
            const mapCenter = this.map.getCenter();
            const distanceMoved = mapCenter.distanceTo(targetLatLng); // distance in meters
            
            if (distanceMoved > 15) { 
                this.map.panTo(targetLatLng, { animate: true, duration: 0.2 });
            }
            
            if (data.connectionStatus === 'Connected') {
                if (this.trail.length === 0 || 
                    this.trail[this.trail.length - 1][0] !== pos.lat || 
                    this.trail[this.trail.length - 1][1] !== pos.lng) {
                    
                    this.trail.push(targetLatLng);
                    if (this.trail.length > 100) this.trail = this.trail.slice(-100);
                    if (this.trailPolyline) this.trailPolyline.setLatLngs(this.trail);
                }
            }
        } catch(e) {
            // Silently absorb minor runtime race conditions during high-speed hardware data packets
        }
    }
    
    /**
     * Render navigation metrics securely
     */
    renderNavigation(data) {
        const isConnected = data.connectionStatus === 'Connected';
        this.elements.altitude.textContent = isConnected ? (data.position.alt || 0).toFixed(1) : '0.0';
        this.elements.groundSpeed.textContent = isConnected ? (data.navigation.groundSpeed || 0).toFixed(1) : '0.0';
        this.elements.heading.textContent = isConnected ? (data.position.heading || 0).toFixed(0) : '0';
        this.elements.distanceHome.textContent = isConnected ? (data.navigation.distanceFromHome || 0).toFixed(0) : '0';
    }
    
    /**
     * Render attitude mechanics
     */
    renderAttitude(data) {
        const isConnected = data.connectionStatus === 'Connected';
        const roll = isConnected ? (data.attitude.roll || 0) : 0;
        const pitch = isConnected ? (data.attitude.pitch || 0) : 0;
        const yaw = isConnected ? (data.attitude.yaw || 0) : 0;
        
        this.elements.roll.textContent = `${roll.toFixed(1)}°`;
        this.elements.pitch.textContent = `${pitch.toFixed(1)}°`;
        this.elements.yaw.textContent = `${yaw.toFixed(1)}°`;
        
        this.elements.roll.className = `attitude-val ${Math.abs(roll) > 45 ? 'danger' : Math.abs(roll) > 30 ? 'warning' : ''}`;
        this.elements.pitch.className = `attitude-val ${Math.abs(pitch) > 45 ? 'danger' : Math.abs(pitch) > 30 ? 'warning' : ''}`;
    }
    
    /**
     * Render GPS health indexes
     */
    renderGPS(data) {
        const gps = data.gps;
        const isConnected = data.connectionStatus === 'Connected';
        this.elements.gpsSats.textContent = isConnected ? (gps.satellites || 0) : 0;
        this.elements.gpsHdop.textContent = isConnected ? (gps.hdop || 0).toFixed(2) : '0.00';
        
        const fixType = isConnected ? (gps.fixType || 'No Fix') : 'No Fix';
        this.elements.gpsFixBadge.textContent = fixType;
        const fixColors = {
            '3D Fix': 'badge-green',
            '2D Fix': 'badge-amber',
            'No Fix': 'badge-red'
        };
        this.elements.gpsFixBadge.className = `badge ${fixColors[fixType] || 'badge-gray'}`;
    }
    
    /**
     * Render battery metrics securely with color warning indicators
     */
    renderBattery(data) {
        const batt = data.battery;
        const isConnected = data.connectionStatus === 'Connected';
        
        // Extract and normalize percentage value safely
        const percent = (isConnected && batt && typeof batt.percent === 'number') ? Math.max(0, Math.min(100, batt.percent)) : 0;
        
        // Update Battery Percentage Text & Progress Bar Width
        if (this.elements.batteryPercent) {
            this.elements.batteryPercent.textContent = isConnected ? `${Math.round(percent)}%` : '0%';
        }
        
        if (this.elements.batteryBar) {
            this.elements.batteryBar.style.width = `${percent}%`;
            this.elements.batteryBar.className = 'batt-bar';
            
            if (percent < 20) {
                this.elements.batteryBar.classList.add('danger');
            } else if (percent < 40) {
                this.elements.batteryBar.classList.add('warning');
            }
        }
        
        // Update Battery Health Badge
        if (this.elements.batteryHealth) {
            let healthText = 'Unknown';
            let healthClass = 'badge badge-gray';
            
            if (isConnected) {
                if (percent > 75) {
                    healthText = 'Excellent';
                    healthClass = 'badge badge-green';
                } else if (percent > 30) {
                    healthText = 'Healthy';
                    healthClass = 'badge badge-green';
                } else {
                    healthText = 'Low Battery';
                    healthClass = 'badge badge-red';
                }
            }
            
            this.elements.batteryHealth.textContent = healthText;
            this.elements.batteryHealth.className = healthClass;
        }
        
        // Display Sub-metrics securely against parsing failure drops
        if (this.elements.batteryVoltage) {
            this.elements.batteryVoltage.textContent = (isConnected && typeof batt?.voltage === 'number') 
                ? batt.voltage.toFixed(2) 
                : '0.00';
        }
        
        if (this.elements.batteryCurrent) {
            this.elements.batteryCurrent.textContent = (isConnected && typeof batt?.current === 'number') 
                ? batt.current.toFixed(1) 
                : '0.0';
        }
        
        if (this.elements.batteryCapacity) {
            this.elements.batteryCapacity.textContent = (isConnected && typeof batt?.capacity === 'number') 
                ? batt.capacity.toLocaleString() 
                : '0';
        }
        
        if (this.elements.flightTimeLeft) {
            this.elements.flightTimeLeft.textContent = (isConnected && typeof batt?.timeLeft === 'number') 
                ? Math.max(0, batt.timeLeft).toFixed(0) 
                : '0';
        }
    }
    
    /**
     * Render landing station telemetry structures
     */
    renderCharging(data) {
        const charging = data.charging;
        const isDocked = charging.docked || false;
        
        this.elements.dockingStatus.textContent = isDocked ? 'Docked' : 'Undocked';
        this.elements.dockingStatus.className = `badge ${isDocked ? 'badge-green' : 'badge-gray'}`;
        
        const status = charging.status || 'Standby';
        this.elements.chargingStatus.textContent = status;
        const statusColors = {
            'Charging': 'badge-green',
            'Completed': 'badge-blue',
            'Fault': 'badge-red',
            'Standby': 'badge-amber'
        };
        this.elements.chargingStatus.className = `badge ${statusColors[status] || 'badge-gray'}`;
        
        const progress = charging.progress || 0;
        this.elements.chargeProgress.textContent = `${progress}%`;
        this.elements.chargeProgressBar.style.width = `${progress}%`;
        
        this.elements.chargingVoltage.textContent = `${(charging.voltage || 0).toFixed(1)} V`;
        this.elements.chargingCurrent.textContent = `${(charging.current || 0).toFixed(1)} A`;
        this.elements.chargingEta.textContent = `~${(charging.eta || 0).toFixed(0)} min`;
    }
    
    /**
     * Render radio hardware communication nodes
     */
    renderCommunication(data) {
        const comm = data.communication;
        const isConnected = data.connectionStatus === 'Connected';
        const rssi = isConnected ? (comm.rssi || -100) : -100;
        
        this.elements.rssiValue.textContent = `${rssi} dBm`;
        
        const bars = this.elements.rssiBars.querySelectorAll('span');
        const rssiPercent = Math.max(0, Math.min(100, ((rssi + 100) / 70) * 100));
        const activeBars = isConnected ? Math.floor((rssiPercent / 100) * 5) : 0;
        
        bars.forEach((bar, index) => {
            bar.className = index < activeBars ? '' : 'inactive';
        });
        
        const linkStatus = isConnected ? (comm.transport || comm.linkStatus || 'Active') : 'Disconnected';
        this.elements.linkStatus.textContent = linkStatus;
        this.elements.linkStatus.className = `badge ${isConnected ? 'badge-green' : 'badge-red'}`;
        this.elements.packetLoss.textContent = `${(isConnected ? (comm.packetLoss || 0) : 0).toFixed(1)}%`;
    }
    
    /**
     * Render live status alert matrices efficiently without innerHTML loops
     */
    renderAlerts(data) {
        if (!this.elements.alertsGrid) return;
        const alerts = data.alerts || [];
        const grid = this.elements.alertsGrid;
        
        if (alerts.length === 0) {
            grid.innerHTML = `<div class="alert-item alert-ok"><i class="ti ti-check"></i> All systems nominal</div>`;
            return;
        }
        
        const visibleAlerts = alerts.slice(0, 6);
        const existingItems = grid.querySelectorAll('.alert-item');
        
        visibleAlerts.forEach((alert, index) => {
            const typeClass = { 'ok': 'alert-ok', 'warn': 'alert-warn', 'crit': 'alert-crit', 'info': 'alert-info' }[alert.type] || 'alert-ok';
            const innerHTMLContent = `<i class="ti ${alert.icon || 'ti-check'}"></i> ${alert.message || ''}`;
            
            if (existingItems[index]) {
                if (existingItems[index].innerHTML !== innerHTMLContent) {
                    existingItems[index].className = `alert-item ${typeClass}`;
                    existingItems[index].innerHTML = innerHTMLContent;
                }
            } else {
                const div = document.createElement('div');
                div.className = `alert-item ${typeClass}`;
                div.innerHTML = innerHTMLContent;
                grid.appendChild(div);
            }
        });
        
        if (existingItems.length > visibleAlerts.length) {
            for (let i = visibleAlerts.length; i < existingItems.length; i++) {
                existingItems[i].remove();
            }
        }
    }
}

// ============================================================
// INITIALIZE APPLICATION INTERFACE
// ============================================================
document.addEventListener('DOMContentLoaded', function() {
    const telemetry = new TelemetryManager();
    const dashboard = new DashboardRenderer(telemetry);
    
    setTimeout(() => {
        telemetry.connectWebSocket();
    }, 2000);
    
    window.__telemetry = telemetry;
    window.__dashboard = dashboard;
    
    console.log(' Drone Landing Station Dashboard initialized cleanly.');
});
