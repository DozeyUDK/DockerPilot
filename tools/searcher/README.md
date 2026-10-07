# Network Searcher

`searcher.py` is an optional packet-sniffing helper that lives outside the main `dockerpilot` package.

## Security model

The Flask/Socket.IO web process is intentionally unprivileged.

It never accepts a sudo password and never applies Linux capabilities to the Python interpreter. Packet capture is delegated to the dedicated `dumpcap` helper. Searcher also binds to `127.0.0.1` by default; binding to another address must be requested explicitly.

Do not run Searcher with `sudo python3` and do not apply `CAP_NET_RAW`/`CAP_NET_ADMIN` to a general Python executable.

## Install

Install the Python dependencies:

```bash
pip install -r tools/searcher/requirements.txt
```

Install `dumpcap` using your operating system's Wireshark package and configure the distro-supported non-root capture mechanism.

For Debian/Ubuntu, a typical setup is:

```bash
sudo apt install wireshark-common
sudo dpkg-reconfigure wireshark-common
sudo usermod -aG wireshark "$USER"
```

Choose the option that allows non-superusers to capture packets, then log out and back in so the group membership is refreshed.

Verify the helper before starting Searcher:

```bash
dumpcap -D
```

If that command cannot list interfaces as your normal user, fix the operating-system capture-helper configuration instead of elevating the Searcher web process.

## Run

```bash
python3 tools/searcher/searcher.py
```

Examples:

```bash
python3 tools/searcher/searcher.py -i eth0
python3 tools/searcher/searcher.py -f "tcp"
```

The UI listens on `127.0.0.1:6008` by default.

An explicit non-loopback bind is possible:

```bash
python3 tools/searcher/searcher.py --host 192.0.2.10
```

Searcher does not provide application authentication, so expose it beyond loopback only behind an access-control boundary you trust.
