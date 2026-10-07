#!/usr/bin/env python3
"""
Network Packet Sniffer with Web Interface
Captures network packets and displays them in real-time via Flask-SocketIO web interface.
"""

import argparse
import logging
import os
import signal
import shutil
import subprocess
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

from scapy.all import PcapReader, IP, TCP, UDP, ICMP, ARP
from flask import Flask, render_template_string, send_from_directory
from flask_socketio import SocketIO

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


def _resolve_dumpcap() -> Optional[str]:
    """Return the configured packet-capture helper binary, if installed."""
    return shutil.which("dumpcap")


def _build_dumpcap_command(
    dumpcap_path: str,
    interface: Optional[str],
    filter_str: Optional[str],
) -> list[str]:
    """Build a shell-free dumpcap command that streams classic pcap to stdout."""
    command = [dumpcap_path, "-q", "-P"]
    if interface and interface.strip():
        command.extend(["-i", interface.strip()])
    if filter_str and filter_str.strip():
        command.extend(["-f", filter_str.strip()])
    command.extend(["-w", "-"])
    return command


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
        self.capture_process: Optional[subprocess.Popen] = None
        self.capture_stderr_thread: Optional[threading.Thread] = None
        self.capture_lock = threading.Lock()
        self.capture_generation = 0
        
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
    
    def start_sniffing(self, socketio: SocketIO):
        """Start packet capture through the dedicated dumpcap helper."""
        with self.capture_lock:
            already_sniffing = self.sniffing

        if already_sniffing:
            logger.warning("Sniffing already in progress, stopping first...")
            self.stop_sniffing(socketio)
            time.sleep(0.2)

        dumpcap_path = _resolve_dumpcap()
        if not dumpcap_path:
            message = (
                "dumpcap is not installed. Install Wireshark/dumpcap and configure "
                "non-root packet-capture permissions for the operator."
            )
            logger.error(message)
            socketio.emit('status', {'status': 'error', 'message': message})
            return

        command = _build_dumpcap_command(dumpcap_path, self.interface, self.filter_str)
        with self.capture_lock:
            self.capture_generation += 1
            generation = self.capture_generation
            self.sniffing = True

        def is_current_capture() -> bool:
            with self.capture_lock:
                return generation == self.capture_generation and self.sniffing

        def sniff_loop():
            process = None
            stderr_tail = deque(maxlen=20)
            emitted_error = False

            try:
                logger.info(
                    "Starting packet capture through dumpcap on interface: %s",
                    self.interface or "dumpcap default",
                )
                if self.filter_str:
                    logger.info("Using filter: %s", self.filter_str)

                # Hold the state lock across the final cancellation check and
                # process creation. A stop issued before startup either wins
                # before Popen (no helper is launched) or receives the newly
                # assigned process and terminates it.
                with self.capture_lock:
                    if generation != self.capture_generation or not self.sniffing:
                        return
                    process = subprocess.Popen(
                        command,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        shell=False,
                    )
                    self.capture_process = process
                    # Serialize the visible "started" state with stop_sniffing().
                    # A concurrent stop can only follow this with "stopped".
                    socketio.emit('status', {'status': 'started'})
                    logger.info("Sniffing started")

                def drain_stderr():
                    if process.stderr is None:
                        return
                    for raw_line in iter(process.stderr.readline, b''):
                        line = raw_line.decode('utf-8', errors='replace').strip()
                        if line:
                            stderr_tail.append(line)
                            logger.debug("dumpcap: %s", line)

                stderr_thread = threading.Thread(
                    target=drain_stderr,
                    daemon=True,
                    name="dockerpilot-searcher-dumpcap-stderr",
                )
                with self.capture_lock:
                    if generation == self.capture_generation:
                        self.capture_stderr_thread = stderr_thread
                stderr_thread.start()

                if process.stdout is None:
                    raise RuntimeError("dumpcap stdout pipe was not created")

                with PcapReader(process.stdout) as reader:
                    for packet in reader:
                        if not is_current_capture():
                            break
                        self.packet_callback(packet, socketio)

                returncode = process.wait(timeout=2)
                if is_current_capture() and returncode != 0:
                    detail = stderr_tail[-1] if stderr_tail else f"exit code {returncode}"
                    raise RuntimeError(f"dumpcap capture failed: {detail}")

            except (OSError, EOFError, RuntimeError) as exc:
                if is_current_capture():
                    emitted_error = True
                    message = str(exc)
                    logger.error("Packet capture failed: %s", message)
                    socketio.emit('status', {'status': 'error', 'message': message})
            except Exception as exc:
                if is_current_capture():
                    emitted_error = True
                    message = f"Packet capture failed: {exc}"
                    logger.error(message)
                    socketio.emit('status', {'status': 'error', 'message': message})
            finally:
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)

                with self.capture_lock:
                    is_current = generation == self.capture_generation
                    if self.capture_process is process:
                        self.capture_process = None
                    if is_current:
                        self.sniffing = False
                        self.capture_stderr_thread = None

                if is_current and not emitted_error:
                    socketio.emit('status', {'status': 'stopped'})

        self.sniff_thread = threading.Thread(
            target=sniff_loop,
            daemon=True,
            name="dockerpilot-searcher-capture",
        )
        self.sniff_thread.start()

    def stop_sniffing(self, socketio: Optional[SocketIO] = None, *, emit_status: bool = True):
        """Stop packet capture and terminate the helper process."""
        with self.capture_lock:
            process = self.capture_process
            if not self.sniffing and process is None:
                return

            self.capture_generation += 1
            stopped_generation = self.capture_generation
            self.sniffing = False
            self.capture_process = None
            self.capture_stderr_thread = None

        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

        with self.capture_lock:
            still_current = (
                self.capture_generation == stopped_generation
                and not self.sniffing
            )

        if emit_status and socketio is not None and still_current:
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
    """Handle start sniffing request."""
    if sniffer:
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


def check_capture_helper() -> bool:
    """Verify that dumpcap is installed and usable by the unprivileged operator."""
    dumpcap_path = _resolve_dumpcap()
    if not dumpcap_path:
        logger.error(
            "dumpcap is required. Install Wireshark/dumpcap and configure "
            "non-root packet-capture permissions."
        )
        return False

    try:
        result = subprocess.run(
            [dumpcap_path, "-D"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=5,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.error("Unable to validate dumpcap: %s", exc)
        return False

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "dumpcap returned an error").strip()
        logger.error("dumpcap is not usable by this account: %s", detail)
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
  # Capture on dumpcap's default interface
  python3 tools/searcher/searcher.py

  # Capture on a specific interface
  python3 tools/searcher/searcher.py -i eth0

  # Capture with a BPF filter
  python3 tools/searcher/searcher.py -f "tcp"

Security model:
  The Flask/Socket.IO process stays unprivileged and never accepts a sudo
  password. Packet access is delegated to dumpcap, which must be configured
  by the operating system for non-root capture.
        """
    )
    
    parser.add_argument(
        '-i', '--interface',
        type=str,
        default=None,
        help='Network interface to sniff on (default: dumpcap-selected interface)'
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
        default='127.0.0.1',
        help='Host to bind the web server to (default: 127.0.0.1; use another address explicitly)'
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
    
    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        logger.error(
            "Refusing to run the Searcher web process as root. Configure dumpcap "
            "for non-root capture instead."
        )
        return 2

    if not check_capture_helper():
        return 2

    # Initialize sniffer
    sniffer = PacketSniffer(interface=args.interface, filter_str=args.filter)

    if args.host not in {'127.0.0.1', 'localhost', '::1'}:
        logger.warning(
            "Searcher is explicitly bound to a non-loopback address (%s). "
            "No application authentication is provided; protect access externally.",
            args.host,
        )

    logger.info(f"Starting web server on {args.host}:{args.port}")
    logger.info(f"Interface: {args.interface or 'dumpcap default'}")
    logger.info(f"Filter: {args.filter or 'none'}")
    logger.info("Open http://{}:{} in your browser".format(args.host, args.port))
    
    previous_sigterm_handler = signal.getsignal(signal.SIGTERM)

    def handle_shutdown_signal(signum, _frame):
        logger.info("Received shutdown signal %s", signum)
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, handle_shutdown_signal)

    try:
        socketio.run(app, host=args.host, port=args.port, debug=args.debug)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        # Always terminate the capability-bearing capture helper, including
        # SIGTERM and unexpected server exceptions.
        if sniffer:
            sniffer.stop_sniffing(socketio, emit_status=False)
        signal.signal(signal.SIGTERM, previous_sigterm_handler)

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
