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
