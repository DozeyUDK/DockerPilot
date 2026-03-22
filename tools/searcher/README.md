# Network Searcher

`searcher.py` is an optional packet-sniffing helper that lives outside the main `dockerpilot` package.

## Install

```bash
pip install -r tools/searcher/requirements.txt
```

## Run

```bash
sudo python3 tools/searcher/searcher.py
```

Examples:

```bash
sudo python3 tools/searcher/searcher.py -i eth0
sudo python3 tools/searcher/searcher.py -f "tcp"
```
