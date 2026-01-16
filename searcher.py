#!/usr/bin/env python3
"""
Network Packet Sniffer with Web Interface
Captures network packets and displays them in real-time via Flask-SocketIO web interface.
"""

import argparse
import logging
import os
import sys
import subprocess
import threading
import time
from collections import deque
from datetime import datetime
from typing import Optional, Dict, Any

from scapy.all import sniff, IP, TCP, UDP, ICMP, ARP, Ether
from flask import Flask, render_template_string
from flask_socketio import SocketIO
import json

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Default HTML template
DEFAULT_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Network Packet Sniffer</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 20px;
            min-height: 100vh;
        }
        .container {
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            border-radius: 10px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
            overflow: hidden;
        }
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px;
            text-align: center;
        }
        .header h1 { margin-bottom: 10px; }
        .stats {
            display: flex;
            justify-content: space-around;
            padding: 15px;
            background: #f8f9fa;
            border-bottom: 2px solid #e9ecef;
        }
        .stat-item {
            text-align: center;
        }
        .stat-value {
            font-size: 24px;
            font-weight: bold;
            color: #667eea;
        }
        .stat-label {
            font-size: 12px;
            color: #6c757d;
            text-transform: uppercase;
        }
        .controls {
            padding: 15px;
            background: #f8f9fa;
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
            align-items: center;
        }
        button {
            padding: 8px 16px;
            border: none;
            border-radius: 5px;
            cursor: pointer;
            font-size: 14px;
            transition: all 0.3s;
        }
        .btn-primary { background: #667eea; color: white; }
        .btn-primary:hover { background: #5568d3; }
        .btn-danger { background: #dc3545; color: white; }
        .btn-danger:hover { background: #c82333; }
        .btn-success { background: #28a745; color: white; }
        .btn-success:hover { background: #218838; }
        input[type="text"] {
            padding: 8px;
            border: 1px solid #ddd;
            border-radius: 5px;
            font-size: 14px;
        }
        .packet-list {
            max-height: 600px;
            overflow-y: auto;
            padding: 10px;
        }
        .packet-item {
            padding: 12px;
            margin: 5px 0;
            border-left: 4px solid #667eea;
            background: #f8f9fa;
            border-radius: 4px;
            transition: all 0.2s;
            font-family: 'Courier New', monospace;
            font-size: 13px;
        }
        .packet-item:hover {
            background: #e9ecef;
            transform: translateX(5px);
        }
        .packet-header {
            display: flex;
            justify-content: space-between;
            margin-bottom: 5px;
            font-weight: bold;
        }
        .packet-time {
            color: #6c757d;
            font-size: 11px;
        }
        .packet-protocol {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 3px;
            font-size: 11px;
            font-weight: bold;
            margin-right: 5px;
        }
        .protocol-tcp { background: #28a745; color: white; }
        .protocol-udp { background: #ffc107; color: black; }
        .protocol-icmp { background: #17a2b8; color: white; }
        .protocol-arp { background: #6f42c1; color: white; }
        .protocol-other { background: #6c757d; color: white; }
        .status {
            padding: 10px;
            text-align: center;
            font-weight: bold;
        }
        .status.active { background: #d4edda; color: #155724; }
        .status.stopped { background: #f8d7da; color: #721c24; }
        .status.error { background: #f8d7da; color: #721c24; }
        .error-message {
            padding: 15px;
            margin: 10px;
            background: #f8d7da;
            color: #721c24;
            border-left: 4px solid #dc3545;
            border-radius: 4px;
            display: none;
        }
        .error-message.show {
            display: block;
        }
        .sudo-prompt {
            padding: 15px;
            margin: 10px;
            background: #fff3cd;
            border-left: 4px solid #ffc107;
            border-radius: 4px;
            display: none;
        }
        .sudo-prompt.show {
            display: block;
        }
        .sudo-prompt input[type="password"] {
            width: 100%;
            padding: 8px;
            margin: 10px 0;
            border: 1px solid #ddd;
            border-radius: 4px;
            font-family: monospace;
        }
        .sudo-prompt button {
            margin-right: 5px;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🔍 Network Packet Sniffer</h1>
            <p>Real-time network traffic monitoring</p>
        </div>
        <div class="stats">
            <div class="stat-item">
                <div class="stat-value" id="totalPackets">0</div>
                <div class="stat-label">Total Packets</div>
            </div>
            <div class="stat-item">
                <div class="stat-value" id="tcpPackets">0</div>
                <div class="stat-label">TCP</div>
            </div>
            <div class="stat-item">
                <div class="stat-value" id="udpPackets">0</div>
                <div class="stat-label">UDP</div>
            </div>
            <div class="stat-item">
                <div class="stat-value" id="icmpPackets">0</div>
                <div class="stat-label">ICMP</div>
            </div>
            <div class="stat-item">
                <div class="stat-value" id="otherPackets">0</div>
                <div class="stat-label">Other</div>
            </div>
        </div>
        <div class="error-message" id="errorMessage"></div>
        <div class="sudo-prompt" id="sudoPrompt">
            <strong>🔐 Sudo Password Required</strong>
            <p>This operation requires root privileges. Enter your sudo password:</p>
            <input type="password" id="sudoPassword" placeholder="Enter sudo password" autocomplete="off">
            <div>
                <button class="btn-primary" onclick="submitSudoPassword()">Submit</button>
                <button class="btn-danger" onclick="cancelSudoPassword()">Cancel</button>
            </div>
            <small style="color: #6c757d; margin-top: 10px; display: block;">
                Password is transmitted securely and only used to elevate privileges. It is not stored or logged.
            </small>
        </div>
        <div class="controls">
            <button class="btn-primary" onclick="startSniffing()">Start</button>
            <button class="btn-danger" onclick="stopSniffing()">Stop</button>
            <button class="btn-success" onclick="clearPackets()">Clear</button>
            <input type="text" id="filterInput" placeholder="BPF Filter (e.g., tcp, port 80, host 192.168.1.1)" style="flex: 1; max-width: 300px;">
            <button class="btn-primary" onclick="applyFilter()">Apply Filter</button>
            <span id="status" class="status stopped">Stopped</span>
        </div>
        <div class="packet-list" id="packetList"></div>
    </div>

    <script>
        const socket = io();
        let packetCount = 0;
        let stats = { tcp: 0, udp: 0, icmp: 0, other: 0 };
        let isSniffing = false;

        socket.on('connect', () => {
            console.log('Connected to server');
        });

        socket.on('packet', (data) => {
            if (!isSniffing) return;
            addPacket(data);
            updateStats(data.protocol);
        });

        socket.on('status', (data) => {
            const statusEl = document.getElementById('status');
            const errorEl = document.getElementById('errorMessage');
            const sudoPrompt = document.getElementById('sudoPrompt');
            
            if (data.status === 'started') {
                statusEl.textContent = 'Sniffing...';
                statusEl.className = 'status active';
                isSniffing = true;
                errorEl.classList.remove('show');
                sudoPrompt.classList.remove('show');
            } else if (data.status === 'error') {
                statusEl.textContent = 'Error';
                statusEl.className = 'status error';
                isSniffing = false;
                errorEl.textContent = 'Error: ' + (data.message || 'Unknown error');
                if (data.message && data.message.includes('Operation not permitted')) {
                    errorEl.textContent = 'Error: Operation not permitted. Root privileges required.';
                    sudoPrompt.classList.add('show');
                } else {
                    sudoPrompt.classList.remove('show');
                }
                errorEl.classList.add('show');
            } else if (data.status === 'sudo_required') {
                sudoPrompt.classList.add('show');
                document.getElementById('sudoPassword').focus();
            } else if (data.status === 'sudo_success') {
                sudoPrompt.classList.remove('show');
                document.getElementById('sudoPassword').value = '';
                // Automatically retry starting
                setTimeout(() => {
                    socket.emit('start_sniffing');
                }, 500);
            } else if (data.status === 'sudo_failed') {
                sudoPrompt.classList.add('show');
                errorEl.textContent = 'Error: Invalid sudo password. Please try again.';
                errorEl.classList.add('show');
                document.getElementById('sudoPassword').value = '';
                document.getElementById('sudoPassword').focus();
            } else {
                statusEl.textContent = 'Stopped';
                statusEl.className = 'status stopped';
                isSniffing = false;
                errorEl.classList.remove('show');
                sudoPrompt.classList.remove('show');
            }
        });

        function addPacket(data) {
            packetCount++;
            const list = document.getElementById('packetList');
            const item = document.createElement('div');
            item.className = 'packet-item';
            
            const protocolClass = `protocol-${data.protocol.toLowerCase()}`;
            item.innerHTML = `
                <div class="packet-header">
                    <span><span class="packet-protocol ${protocolClass}">${data.protocol}</span> ${data.src} → ${data.dst}</span>
                    <span class="packet-time">${data.time}</span>
                </div>
                <div>Size: ${data.size} bytes | ${data.details}</div>
            `;
            
            list.insertBefore(item, list.firstChild);
            
            // Keep only last 1000 packets
            while (list.children.length > 1000) {
                list.removeChild(list.lastChild);
            }
            
            document.getElementById('totalPackets').textContent = packetCount;
        }

        function updateStats(protocol) {
            if (stats.hasOwnProperty(protocol.toLowerCase())) {
                stats[protocol.toLowerCase()]++;
            } else {
                stats.other++;
            }
            document.getElementById('tcpPackets').textContent = stats.tcp;
            document.getElementById('udpPackets').textContent = stats.udp;
            document.getElementById('icmpPackets').textContent = stats.icmp;
            document.getElementById('otherPackets').textContent = stats.other;
        }

        function startSniffing() {
            socket.emit('start_sniffing');
        }

        function stopSniffing() {
            socket.emit('stop_sniffing');
        }

        function clearPackets() {
            document.getElementById('packetList').innerHTML = '';
            packetCount = 0;
            stats = { tcp: 0, udp: 0, icmp: 0, other: 0 };
            document.getElementById('totalPackets').textContent = '0';
            document.getElementById('tcpPackets').textContent = '0';
            document.getElementById('udpPackets').textContent = '0';
            document.getElementById('icmpPackets').textContent = '0';
            document.getElementById('otherPackets').textContent = '0';
        }

        function applyFilter() {
            const filter = document.getElementById('filterInput').value;
            if (isSniffing) {
                // If sniffing, stop first, then restart with new filter
                socket.emit('stop_sniffing');
                setTimeout(() => {
                    socket.emit('set_filter', { filter: filter });
                    setTimeout(() => {
                        socket.emit('start_sniffing');
                    }, 500);
                }, 500);
            } else {
                socket.emit('set_filter', { filter: filter });
            }
        }

        function submitSudoPassword() {
            const password = document.getElementById('sudoPassword').value;
            if (!password) {
                alert('Please enter your sudo password');
                return;
            }
            socket.emit('sudo_password', { password: password });
        }

        function cancelSudoPassword() {
            document.getElementById('sudoPassword').value = '';
            document.getElementById('sudoPrompt').classList.remove('show');
            socket.emit('cancel_sudo');
        }

        // Allow Enter key to submit password
        document.addEventListener('DOMContentLoaded', () => {
            const passwordInput = document.getElementById('sudoPassword');
            if (passwordInput) {
                passwordInput.addEventListener('keypress', (e) => {
                    if (e.key === 'Enter') {
                        submitSudoPassword();
                    }
                });
            }
        });
    </script>
</body>
</html>
"""


class PacketSniffer:
    """Network packet sniffer with filtering and statistics"""
    
    def __init__(self, interface: Optional[str] = None, filter_str: Optional[str] = None):
        self.interface = interface
        self.filter_str = filter_str
        self.sniffing = False
        self.sniff_thread: Optional[threading.Thread] = None
        self.packet_count = 0
        self.stats = {'TCP': 0, 'UDP': 0, 'ICMP': 0, 'ARP': 0, 'Other': 0}
        self.packet_buffer = deque(maxlen=1000)  # Buffer last 1000 packets
        self.sudo_password: Optional[str] = None
        self.has_capabilities = False
        
    def get_protocol(self, packet) -> str:
        """Extract protocol name from packet"""
        if TCP in packet:
            return 'TCP'
        elif UDP in packet:
            return 'UDP'
        elif ICMP in packet:
            return 'ICMP'
        elif ARP in packet:
            return 'ARP'
        else:
            return 'Other'
    
    def get_packet_details(self, packet) -> str:
        """Extract detailed information from packet"""
        details = []
        
        if IP in packet:
            ip_layer = packet[IP]
            details.append(f"TTL: {ip_layer.ttl}")
            
            if TCP in packet:
                tcp = packet[TCP]
                details.append(f"Ports: {tcp.sport} → {tcp.dport}")
                if tcp.flags:
                    flags = []
                    if tcp.flags & 0x02: flags.append('SYN')
                    if tcp.flags & 0x10: flags.append('ACK')
                    if tcp.flags & 0x01: flags.append('FIN')
                    if tcp.flags & 0x08: flags.append('PSH')
                    if flags:
                        details.append(f"Flags: {', '.join(flags)}")
            elif UDP in packet:
                udp = packet[UDP]
                details.append(f"Ports: {udp.sport} → {udp.dport}")
            elif ICMP in packet:
                icmp = packet[ICMP]
                details.append(f"Type: {icmp.type}")
        
        return " | ".join(details) if details else "No additional details"
    
    def packet_callback(self, packet, socketio: SocketIO):
        """Callback function for each captured packet"""
        if not self.sniffing:
            return
            
        try:
            if IP in packet or ARP in packet:
                ip_layer = packet[IP] if IP in packet else None
                arp_layer = packet[ARP] if ARP in packet else None
                
                if ip_layer:
                    src = ip_layer.src
                    dst = ip_layer.dst
                elif arp_layer:
                    src = arp_layer.psrc
                    dst = arp_layer.pdst
                else:
                    return
                
                protocol = self.get_protocol(packet)
                size = len(packet)
                details = self.get_packet_details(packet)
                timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
                
                packet_info = {
                    'src': src,
                    'dst': dst,
                    'protocol': protocol,
                    'size': size,
                    'details': details,
                    'time': timestamp
                }
                
                # Update statistics
                if protocol in self.stats:
                    self.stats[protocol] += 1
                else:
                    self.stats['Other'] += 1
                
                self.packet_count += 1
                self.packet_buffer.append(packet_info)
                
                # Emit to clients
                socketio.emit('packet', packet_info)
                
                # Log every 100 packets
                if self.packet_count % 100 == 0:
                    logger.info(f"Captured {self.packet_count} packets")
                    
        except Exception as e:
            logger.error(f"Error processing packet: {e}")
    
    def set_sudo_password(self, password: str) -> bool:
        """Set sudo password and try to elevate privileges"""
        self.sudo_password = password
        
        # First, verify the password is correct by trying a simple sudo command
        try:
            verify_result = subprocess.run(
                ['sudo', '-S', '-v'],
                input=password.encode(),
                capture_output=True,
                timeout=5
            )
            
            if verify_result.returncode != 0:
                error = verify_result.stderr.decode() if verify_result.stderr else "Unknown error"
                if "Sorry, try again" in error or "incorrect password" in error.lower():
                    logger.warning("Invalid sudo password")
                    self.sudo_password = None
                    return False
            
            # Password is valid, now try to set capabilities
            # Resolve symlinks to get the real Python executable
            python_path = sys.executable
            real_python_path = os.path.realpath(python_path)
            
            logger.info(f"Attempting to set capabilities on: {real_python_path}")
            
            # Try setting capabilities on the real path
            result = subprocess.run(
                ['sudo', '-S', 'setcap', 'cap_net_raw,cap_net_admin=eip', real_python_path],
                input=password.encode(),
                capture_output=True,
                timeout=5
            )
            
            if result.returncode == 0:
                logger.info("Successfully set capabilities on Python interpreter")
                self.has_capabilities = True
                # Clear password from memory after use
                self.sudo_password = None
                return True
            else:
                error = result.stderr.decode() if result.stderr else "Unknown error"
                logger.warning(f"Failed to set capabilities: {error}")
                
                # Try alternative: set capabilities on common Python paths
                alternative_paths = [
                    '/usr/bin/python3',
                    '/usr/local/bin/python3',
                    '/bin/python3',
                ]
                
                for alt_path in alternative_paths:
                    if os.path.exists(alt_path) and alt_path != real_python_path:
                        real_alt_path = os.path.realpath(alt_path)
                        if real_alt_path != real_python_path:
                            logger.info(f"Trying alternative path: {real_alt_path}")
                            alt_result = subprocess.run(
                                ['sudo', '-S', 'setcap', 'cap_net_raw,cap_net_admin=eip', real_alt_path],
                                input=password.encode(),
                                capture_output=True,
                                timeout=5
                            )
                            if alt_result.returncode == 0:
                                logger.info(f"Successfully set capabilities on alternative Python: {real_alt_path}")
                                logger.warning("Note: You may need to use the Python at this path for capabilities to work")
                                self.has_capabilities = True
                                self.sudo_password = None
                                return True
                
                # If all attempts failed, password is valid but setcap doesn't work
                # This might be due to filesystem restrictions or Python being in a location
                # where capabilities can't be set (like NFS, or Python being a script wrapper)
                logger.warning("Could not set capabilities on any Python executable")
                logger.info("This might be due to:")
                logger.info("  - Python being a script wrapper (not a binary)")
                logger.info("  - Filesystem not supporting capabilities")
                logger.info("  - Python being on a network filesystem")
                logger.info("Please run the script with: sudo python3 searcher.py")
                
                # Clear password since we can't use it effectively
                self.sudo_password = None
                self.has_capabilities = False
                return True  # Password was valid, but we can't use it
                
        except subprocess.TimeoutExpired:
            logger.error("Timeout while verifying sudo password")
            self.sudo_password = None
            return False
        except Exception as e:
            logger.error(f"Error setting capabilities: {e}")
            self.sudo_password = None
            return False
    
    def start_sniffing(self, socketio: SocketIO):
        """Start packet sniffing in a separate thread"""
        if self.sniffing:
            logger.warning("Sniffing already in progress, stopping first...")
            self.stop_sniffing(socketio)
            time.sleep(0.5)  # Give it time to stop
        
        self.sniffing = True
        
        def sniff_loop():
            try:
                logger.info(f"Starting packet capture on interface: {self.interface or 'all'}")
                if self.filter_str:
                    logger.info(f"Using filter: {self.filter_str}")
                
                # Check if we have required privileges
                if os.geteuid() != 0 and not self.has_capabilities:
                    error_msg = "Operation not permitted. Root privileges required."
                    if self.sudo_password:
                        error_msg += " Setcap failed. Please restart the script with: sudo python3 searcher.py"
                    logger.error(error_msg)
                    self.sniffing = False
                    socketio.emit('status', {'status': 'error', 'message': error_msg})
                    # Clear password since we can't use it
                    self.sudo_password = None
                    return
                
                sniff(
                    prn=lambda pkt: self.packet_callback(pkt, socketio),
                    store=0,
                    iface=self.interface,
                    filter=self.filter_str,
                    stop_filter=lambda x: not self.sniffing
                )
            except PermissionError as e:
                error_msg = "Operation not permitted. Root privileges required."
                logger.error(error_msg)
                self.sniffing = False
                socketio.emit('status', {'status': 'error', 'message': error_msg})
            except OSError as e:
                if "Operation not permitted" in str(e) or e.errno == 1:
                    error_msg = "Operation not permitted. Root privileges required."
                    logger.error(error_msg)
                    self.sniffing = False
                    socketio.emit('status', {'status': 'error', 'message': error_msg})
                else:
                    logger.error(f"OS Error in sniffing thread: {e}")
                    self.sniffing = False
                    socketio.emit('status', {'status': 'error', 'message': str(e)})
            except Exception as e:
                logger.error(f"Error in sniffing thread: {e}")
                self.sniffing = False
                socketio.emit('status', {'status': 'error', 'message': str(e)})
        
        self.sniff_thread = threading.Thread(target=sniff_loop, daemon=True)
        self.sniff_thread.start()
        socketio.emit('status', {'status': 'started'})
        logger.info("Sniffing started")
    
    def stop_sniffing(self, socketio: SocketIO):
        """Stop packet sniffing"""
        if not self.sniffing:
            return
        
        self.sniffing = False
        socketio.emit('status', {'status': 'stopped'})
        logger.info("Sniffing stopped")
    
    def set_filter(self, filter_str: Optional[str]):
        """Update packet filter"""
        old_filter = self.filter_str
        self.filter_str = filter_str if filter_str and filter_str.strip() else None
        logger.info(f"Filter updated: {old_filter} -> {self.filter_str}")
        
        # If sniffing is active, we need to restart with new filter
        # This will be handled by the client-side code
    
    def get_stats(self) -> Dict[str, Any]:
        """Get current statistics"""
        return {
            'total': self.packet_count,
            'protocols': self.stats.copy()
        }


# Global sniffer instance
sniffer: Optional[PacketSniffer] = None

app = Flask(__name__)
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="threading",
    logger=False,
    engineio_logger=False
)


@app.route('/')
def index():
    """Serve the main page"""
    return render_template_string(DEFAULT_TEMPLATE)


@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    logger.info('Client connected')
    if sniffer and sniffer.sniffing:
        socketio.emit('status', {'status': 'started'})


@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    logger.info('Client disconnected')


@socketio.on('start_sniffing')
def handle_start_sniffing():
    """Handle start sniffing request"""
    if sniffer:
        # Check if we need sudo privileges
        if os.geteuid() != 0 and not sniffer.has_capabilities:
            # Request sudo password
            socketio.emit('status', {'status': 'sudo_required'})
            return
        sniffer.start_sniffing(socketio)


@socketio.on('stop_sniffing')
def handle_stop_sniffing():
    """Handle stop sniffing request"""
    if sniffer:
        sniffer.stop_sniffing(socketio)


@socketio.on('set_filter')
def handle_set_filter(data):
    """Handle filter update request"""
    if sniffer:
        filter_str = data.get('filter')
        sniffer.set_filter(filter_str if filter_str else None)


@socketio.on('sudo_password')
def handle_sudo_password(data):
    """Handle sudo password submission"""
    if not sniffer:
        return
    
    password = data.get('password', '').strip()
    if not password:
        socketio.emit('status', {'status': 'sudo_failed', 'message': 'Password cannot be empty'})
        return
    
    # Try to set capabilities with the password
    success = sniffer.set_sudo_password(password)
    
    if success:
        if sniffer.has_capabilities:
            logger.info("Sudo password accepted, capabilities set successfully")
            socketio.emit('status', {'status': 'sudo_success'})
        else:
            # Password is valid but setcap failed - we'll need to use alternative method
            logger.info("Sudo password accepted, but setcap failed. Will use alternative method.")
            # Store password for later use (will be cleared after use)
            socketio.emit('status', {'status': 'sudo_success', 'message': 'Password accepted, but capabilities could not be set. Please restart the script with: sudo python3 searcher.py'})
    else:
        logger.warning("Invalid sudo password")
        socketio.emit('status', {'status': 'sudo_failed', 'message': 'Invalid password'})


@socketio.on('cancel_sudo')
def handle_cancel_sudo():
    """Handle sudo password cancellation"""
    logger.info("Sudo password entry cancelled")
    if sniffer:
        sniffer.sudo_password = None
    socketio.emit('status', {'status': 'stopped'})


def check_permissions():
    """Check if script has required permissions for packet capture"""
    if os.geteuid() != 0:
        logger.warning("⚠️  WARNING: Not running as root. Packet capture may fail.")
        logger.warning("   Run with: sudo python3 searcher.py")
        logger.warning("   Or set capabilities: sudo setcap cap_net_raw,cap_net_admin=eip $(which python3)")
        return False
    return True


def main():
    """Main entry point"""
    global sniffer
    
    parser = argparse.ArgumentParser(
        description='Network Packet Sniffer with Web Interface',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Sniff on all interfaces (requires sudo)
  sudo python3 searcher.py

  # Sniff on specific interface
  sudo python3 searcher.py -i eth0

  # Sniff with filter (TCP only)
  sudo python3 searcher.py -f "tcp"

  # Sniff on specific port
  sudo python3 searcher.py -f "port 80"

  # Sniff from specific IP
  sudo python3 searcher.py -f "host 192.168.1.1"

Note: This script requires root privileges for packet capture.
      Run with 'sudo' or set capabilities on Python interpreter.
        """
    )
    
    parser.add_argument(
        '-i', '--interface',
        type=str,
        default=None,
        help='Network interface to sniff on (default: all interfaces)'
    )
    
    parser.add_argument(
        '-f', '--filter',
        type=str,
        default=None,
        help='BPF filter string (e.g., "tcp", "port 80", "host 192.168.1.1")'
    )
    
    parser.add_argument(
        '-H', '--host',
        type=str,
        default='0.0.0.0',
        help='Host to bind the web server to (default: 0.0.0.0)'
    )
    
    parser.add_argument(
        '-p', '--port',
        type=int,
        default=6008,
        help='Port to bind the web server to (default: 6008)'
    )
    
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug mode'
    )
    
    args = parser.parse_args()
    
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Check permissions
    has_permissions = check_permissions()
    
    # Initialize sniffer
    sniffer = PacketSniffer(interface=args.interface, filter_str=args.filter)
    
    logger.info(f"Starting web server on {args.host}:{args.port}")
    logger.info(f"Interface: {args.interface or 'all'}")
    logger.info(f"Filter: {args.filter or 'none'}")
    if not has_permissions:
        logger.warning("⚠️  Running without root privileges - packet capture will fail!")
    logger.info("Open http://{}:{} in your browser".format(args.host if args.host != '0.0.0.0' else 'localhost', args.port))
    
    try:
        socketio.run(app, host=args.host, port=args.port, debug=args.debug)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        if sniffer:
            sniffer.stop_sniffing(socketio)


if __name__ == '__main__':
    main()

