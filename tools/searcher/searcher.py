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
from pathlib import Path
from typing import Optional, Dict, Any

from scapy.all import sniff, IP, TCP, UDP, ICMP, ARP, Ether
from flask import Flask, render_template_string, send_from_directory
from flask_socketio import SocketIO
import json

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Sniffer UI: templates/sniffer.html + sniffer.js (served at /sniffer.js)
_SEARCHER_DIR = Path(__file__).resolve().parent


def _load_sniffer_html_template() -> str:
    return (_SEARCHER_DIR / "templates" / "sniffer.html").read_text(encoding="utf-8")


DEFAULT_TEMPLATE = _load_sniffer_html_template()


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
                logger.info("Please run the script with: sudo python3 tools/searcher/searcher.py")
                
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
                        error_msg += " Setcap failed. Please restart the script with: sudo python3 tools/searcher/searcher.py"
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


@app.route('/sniffer.js')
def sniffer_js():
    """Serve sniffer UI script (same directory as sniffer.html)."""
    return send_from_directory(
        _SEARCHER_DIR / 'templates',
        'sniffer.js',
        mimetype='application/javascript; charset=utf-8',
    )


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
            socketio.emit('status', {'status': 'sudo_success', 'message': 'Password accepted, but capabilities could not be set. Please restart the script with: sudo python3 tools/searcher/searcher.py'})
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
        logger.warning("   Run with: sudo python3 tools/searcher/searcher.py")
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
  sudo python3 tools/searcher/searcher.py

  # Sniff on specific interface
  sudo python3 tools/searcher/searcher.py -i eth0

  # Sniff with filter (TCP only)
  sudo python3 tools/searcher/searcher.py -f "tcp"

  # Sniff on specific port
  sudo python3 tools/searcher/searcher.py -f "port 80"

  # Sniff from specific IP
  sudo python3 tools/searcher/searcher.py -f "host 192.168.1.1"

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
