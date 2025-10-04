#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import docker
from rich.console import Console
from rich.table import Table
import os
import sys
import json
import time
import requests
from datetime import datetime

def print_banner():
    banner = """
╔══════════════════════════════════════════════════════════════════════╗
║                                                                      ║
║  ██████╗  ██████╗  ██████╗██╗  ██╗███████╗██████╗                    ║
║  ██╔══██╗██╔═══██╗██╔════╝██║ ██╔╝██╔════╝██╔══██╗                   ║
║  ██║  ██║██║   ██║██║     █████╔╝ █████╗  ██████╔╝                   ║
║  ██║  ██║██║   ██║██║     ██╔═██╗ ██╔══╝  ██╔══██╗                   ║
║  ██████╔╝╚██████╔╝╚██████╗██║  ██╗███████╗██║  ██║                   ║
║  ╚═════╝  ╚═════╝  ╚═════╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝                   ║
║                                                                      ║
║  ███╗   ███╗ █████╗ ███╗   ██╗ █████╗  ██████╗ ██╗███╗   ██╗ ██████╗ ║
║  ████╗ ████║██╔══██╗████╗  ██║██╔══██╗██╔════╝ ██║████╗  ██║██╔════╝ ║
║  ██╔████╔██║███████║██╔██╗ ██║███████║██║  ███╗██║██╔██╗ ██║██║  ███╗║
║  ██║╚██╔╝██║██╔══██║██║╚██╗██║██╔══██║██║   ██║██║██║╚██╗██║██║   ██║║
║  ██║ ╚═╝ ██║██║  ██║██║ ╚████║██║  ██║╚██████╔╝██║██║ ╚████║╚██████╔╝║
║  ╚═╝     ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝╚═╝  ╚═╝ ╚═════╝ ╚═╝╚═╝  ╚═══╝ ╚═════╝ ║
║                                                                      ║
║  ████████╗ ██████╗  ██████╗ ██╗                                      ║
║  ╚══██╔══╝██╔═══██╗██╔═══██╗██║                                      ║
║     ██║   ██║   ██║██║   ██║██║                                      ║
║     ██║   ██║   ██║██║   ██║██║                                      ║
║     ██║   ╚██████╔╝╚██████╔╝███████╗                                 ║
║     ╚═╝    ╚═════╝  ╚═════╝ ╚══════╝                                 ║
║                                                                      ║
╚══════════════════════════════════════════════════════════════════════╝
"""
    print(banner)

client = docker.from_env()
console = Console()

def list_containers(show_all=True):
    containers = client.containers.list(all=show_all)
    table = Table(title="Kontenery Docker")
    table.add_column("Nr", style="bold blue")
    table.add_column("ID", style="cyan")
    table.add_column("Nazwa", style="green")
    table.add_column("Status", style="magenta")
    table.add_column("Obraz", style="yellow")

    for idx, c in enumerate(containers, start=1):
        table.add_row(
            str(idx),
            c.short_id,
            c.name,
            c.status,
            c.image.tags[0] if c.image.tags else "brak"
        )
    console.print(table)
    return containers

def stop_container(container_name):
    try:
        container = client.containers.get(container_name)
        if container.status == "exited":
            console.print(f"[yellow]Kontener {container.name} jest już zatrzymany[/yellow]")
            return
        console.print(f"[cyan]Zatrzymywanie kontenera {container.name}...[/cyan]")
        container.stop()
        console.print(f"[green]Kontener {container.name} zatrzymany[/green]")
    except docker.errors.NotFound:
        console.print(f"[bold red]Nie znaleziono kontenera: {container_name}[/bold red]")
    except docker.errors.APIError as e:
        console.print(f"[bold red]Błąd API Dockera:[/bold red] {e}")

def stop_and_remove(container_name):
    try:
        container = client.containers.get(container_name)
        console.print(f"[cyan]Zatrzymywanie {container.name}...[/cyan]")
        container.stop()
        console.print(f"[cyan]Usuwanie {container.name}...[/cyan]")
        container.remove()
        console.print(f"[green]Kontener {container.name} usunięty[/green]")
    except docker.errors.NotFound:
        console.print(f"[bold red]Nie znaleziono kontenera: {container_name}[/bold red]")
    except docker.errors.APIError as e:
        console.print(f"[bold red]Błąd API Dockera:[/bold red] {e}")

def start_existing_container(container_name):
    try:
        container = client.containers.get(container_name)
        if container.status == "running":
            console.print(f"[yellow]Kontener {container.name} jest już uruchomiony[/yellow]")
            return
        console.print(f"[cyan]Uruchamianie kontenera {container.name}...[/cyan]")
        container.start()
        console.print(f"[green]Kontener {container.name} uruchomiony[/green]")
    except docker.errors.NotFound:
        console.print(f"[bold red]Nie znaleziono kontenera: {container_name}[/bold red]")
    except docker.errors.APIError as e:
        console.print(f"[bold red]Błąd API Dockera:[/bold red] {e}")

def remove_image(image_name):
    try:
        console.print(f"[cyan]Usuwanie obrazu {image_name}...[/cyan]")
        client.images.remove(image=image_name, force=True)
        console.print(f"[green]Obraz {image_name} usunięty[/green]")
    except docker.errors.ImageNotFound:
        console.print(f"[bold red]Nie znaleziono obrazu: {image_name}[/bold red]")
    except docker.errors.APIError as e:
        console.print(f"[bold red]Błąd API Dockera:[/bold red] {e}")

def build_image(default_path=".", tag="myimage:latest"):
    path = input(f"Ścieżka do katalogu z Dockerfile [domyślnie {default_path}]: ") or default_path
    path = os.path.abspath(os.path.expanduser(path))
    dockerfile_path = os.path.join(path, "Dockerfile")
    if not os.path.isfile(dockerfile_path):
        console.print(f"[bold red]UWAGA![/bold red] Nie znaleziono pliku Dockerfile w katalogu: {path}")
        return
    home_dir = os.path.expanduser("~")
    if path.startswith(home_dir):
        console.print(f"[yellow]Uwaga! Budowanie obrazu w katalogu domowym ({path}) może powodować błędy z uprawnieniami. Lepiej utwórz dedykowany katalog, np. ~/docker_build[/yellow]")
    console.print(f"[cyan]Budowanie obrazu {tag} z katalogu {path}...[/cyan]")
    try:
        image, logs = client.images.build(path=path, tag=tag, rm=True)
        for log in logs:
            if 'stream' in log:
                console.print(log['stream'], end="")
        console.print(f"[green]Obraz {tag} zbudowany pomyślnie[/green]")
    except docker.errors.BuildError as e:
        console.print(f"[bold red]Błąd budowania obrazu:[/bold red] {e}")
    except docker.errors.APIError as e:
        console.print(f"[bold red]Błąd API Dockera:[/bold red] {e}")
    except Exception as e:
        console.print(f"[bold red]Nieoczekiwany błąd:[/bold red] {e}")

def run_new_container(image_name, name="mycontainer", ports=None, command=None):
    try:
        console.print(f"[cyan]Uruchamianie nowego kontenera {name} z obrazu {image_name}...[/cyan]")
        client.containers.run(image_name, name=name, detach=True, ports=ports, command=command)
        console.print(f"[green]Kontener {name} uruchomiony[/green]")
    except docker.errors.ImageNotFound:
        console.print(f"[bold red]Nie znaleziono obrazu: {image_name}[/bold red]")
    except docker.errors.APIError as e:
        console.print(f"[bold red]Błąd API Dockera:[/bold red] {e}")

def list_images():
    images = client.images.list()
    table = Table(title="Obrazy Docker")
    table.add_column("Nr", style="bold blue")
    table.add_column("ID", style="cyan")
    table.add_column("Tagi", style="green")
    table.add_column("Rozmiar", style="magenta")

    for idx, img in enumerate(images, start=1):
        tags = ", ".join(img.tags) if img.tags else "<none>"
        size_mb = img.attrs['Size'] / (1024 * 1024)
        table.add_row(
            str(idx),
            img.short_id.split(":")[1],
            tags,
            f"{size_mb:.2f} MB"
        )
    console.print(table)
    return images

# --- Nowe opcje devopsowe ---

def update_restart_policy(container_name, policy):
    """Ustawia restart policy na kontenerze (no, always, on-failure, unless-stopped)"""
    try:
        container = client.containers.get(container_name)
        console.print(f"[cyan]Aktualizuję restart policy kontenera {container.name} na '{policy}'...[/cyan]")
        container.update(restart_policy={"Name": policy})
        console.print(f"[green]Restart policy ustawione na '{policy}'[/green]")
    except docker.errors.NotFound:
        console.print(f"[bold red]Nie znaleziono kontenera: {container_name}[/bold red]")
    except docker.errors.APIError as e:
        console.print(f"[bold red]Błąd API Dockera przy aktualizacji:[/bold red] {e}")

def inspect_container(container_name):
    if not container_name.strip():
        console.print("[yellow]Nie podano nazwy kontenera, wracam do menu.[/yellow]")
        return
    try:
        container = client.containers.get(container_name)
        info = container.attrs
        pretty = json.dumps(info, indent=2)
        console.print(pretty)
    except docker.errors.NotFound:
        console.print(f"[bold red]Nie znaleziono kontenera: {container_name}[/bold red]")
    except docker.errors.APIError as e:
        console.print(f"[bold red]Błąd API Dockera przy inspekcji:[/bold red] {e}")
    except Exception as e:
        console.print(f"[bold red]Nieoczekiwany błąd podczas inspekcji: {e}[/bold red]")

def logs_container(container_name, tail=100):
    """Wyświetla ostatnie logi kontenera"""
    try:
        container = client.containers.get(container_name)
        logs = container.logs(tail=tail).decode()
        console.print(f"[cyan]Ostatnie {tail} linii logów kontenera {container_name}:[/cyan]")
        console.print(logs)
    except docker.errors.NotFound:
        console.print(f"[bold red]Nie znaleziono kontenera: {container_name}[/bold red]")
    except docker.errors.APIError as e:
        console.print(f"[bold red]Błąd API Dockera przy pobieraniu logów:[/bold red] {e}")

def exec_in_container(container_name, cmd):
    """Wykonaj polecenie w działającym kontenerze"""
    try:
        container = client.containers.get(container_name)
        console.print(f"[cyan]Wykonuję komendę '{cmd}' w kontenerze {container_name}...[/cyan]")
        exec_log = container.exec_run(cmd)
        output = exec_log.output.decode()
        console.print(output)
    except docker.errors.NotFound:
        console.print(f"[bold red]Nie znaleziono kontenera: {container_name}[/bold red]")
    except docker.errors.APIError as e:
        console.print(f"[bold red]Błąd API Dockera przy exec:[/bold red] {e}")

def stats_container(container_name):
    if not container_name.strip():
        console.print("[yellow]Nie podano nazwy kontenera, wracam do menu.[/yellow]")
        return
    
    try:
        container = client.containers.get(container_name)
        
        # Pobierz dwa pomiary z interwałem 1 sekundy dla dokładnego CPU
        console.print(f"[cyan]Pobieranie statystyk dla {container_name}... (to może chwilę potrwać)[/cyan]")
        
        # Pierwszy pomiar
        stats1 = container.stats(stream=False)
        time.sleep(1)  # Czekamy sekundę
        # Drugi pomiar
        stats2 = container.stats(stream=False)
        
        # Obliczenie CPU na podstawie różnicy między dwoma pomiarami
        cpu_percent = 0.0
        try:
            cpu1_total = stats1['cpu_stats']['cpu_usage']['total_usage']
            cpu1_system = stats1['cpu_stats'].get('system_cpu_usage', 0)
            
            cpu2_total = stats2['cpu_stats']['cpu_usage']['total_usage']
            cpu2_system = stats2['cpu_stats'].get('system_cpu_usage', 0)
            
            cpu_delta = cpu2_total - cpu1_total
            system_delta = cpu2_system - cpu1_system
            
            # Liczba rdzeni CPU
            online_cpus = len(stats2['cpu_stats']['cpu_usage'].get('percpu_usage', [1]))
            
            if system_delta > 0 and cpu_delta >= 0:
                cpu_percent = (cpu_delta / system_delta) * online_cpus * 100.0
            
        except (KeyError, ZeroDivisionError) as e:
            console.print(f"[yellow]Błąd obliczania CPU: {e}[/yellow]")
            cpu_percent = 0.0

        # Statystyki pamięci (używamy drugiego pomiaru)
        mem_usage = stats2['memory_stats'].get('usage', 0)
        mem_limit = stats2['memory_stats'].get('limit', 1)
        mem_percent = (mem_usage / mem_limit) * 100.0 if mem_limit > 0 else 0

        # Statystyki sieciowe (opcjonalne)
        network_stats = stats2.get('networks', {})
        rx_bytes = 0
        tx_bytes = 0
        for interface, net_data in network_stats.items():
            rx_bytes += net_data.get('rx_bytes', 0)
            tx_bytes += net_data.get('tx_bytes', 0)

        # Wyświetlenie wyników
        console.print(f"\n[bold cyan]📊 Statystyki kontenera: {container_name}[/bold cyan]")
        console.print(f"[green]🖥️  CPU użycie: {cpu_percent:.2f}%[/green]")
        console.print(f"[blue]💾 Pamięć: {mem_usage/(1024*1024):.2f} MB / {mem_limit/(1024*1024):.2f} MB ({mem_percent:.2f}%)[/blue]")
        
        if rx_bytes > 0 or tx_bytes > 0:
            console.print(f"[magenta]🌐 Sieć RX: {rx_bytes/(1024*1024):.2f} MB, TX: {tx_bytes/(1024*1024):.2f} MB[/magenta]")
        
        # Dodatkowe informacje o procesach (jeśli dostępne)
        if 'pids_stats' in stats2:
            pids = stats2['pids_stats'].get('current', 0)
            console.print(f"[yellow]⚡ Procesy: {pids}[/yellow]")
            
    except docker.errors.NotFound:
        console.print(f"[bold red]Nie znaleziono kontenera: {container_name}[/bold red]")
    except docker.errors.APIError as e:
        console.print(f"[bold red]Błąd API Dockera przy pobieraniu statystyk:[/bold red] {e}")
    except Exception as e:
        console.print(f"[bold red]Nieoczekiwany błąd podczas pobierania statystyk: {e}[/bold red]")

def monitor_container_live(container_name, duration=30):
    """Monitoruje kontener w czasie rzeczywistym przez określony czas"""
    if not container_name.strip():
        console.print("[yellow]Nie podano nazwy kontenera, wracam do menu.[/yellow]")
        return
    
    try:
        container = client.containers.get(container_name)
        console.print(f"[cyan]Monitorowanie kontenera {container_name} przez {duration} sekund...[/cyan]")
        console.print("[yellow]Naciśnij Ctrl+C aby zatrzymać monitorowanie[/yellow]")
        
        stats_stream = container.stats(stream=True)
        start_time = time.time()
        prev_stats = None
        
        for raw_stats in stats_stream:
            current_time = time.time()
            if current_time - start_time > duration:
                break
            
            try:
                # Konwertuj dane jeśli są w formacie bajtów
                if isinstance(raw_stats, bytes):
                    stats = json.loads(raw_stats.decode('utf-8'))
                elif isinstance(raw_stats, str):
                    stats = json.loads(raw_stats)
                else:
                    stats = raw_stats
                
                # Sprawdź czy stats to słownik
                if not isinstance(stats, dict):
                    console.print("[yellow]Otrzymano nieprawidłowe dane statystyk, pomijam...[/yellow]")
                    time.sleep(1)
                    continue
                
                # Oblicz CPU jeśli mamy poprzedni pomiar
                cpu_percent = 0.0
                if prev_stats and isinstance(prev_stats, dict):
                    try:
                        cpu_stats = stats.get('cpu_stats', {})
                        prev_cpu_stats = prev_stats.get('cpu_stats', {})
                        
                        if 'cpu_usage' in cpu_stats and 'cpu_usage' in prev_cpu_stats:
                            current_total = cpu_stats['cpu_usage'].get('total_usage', 0)
                            prev_total = prev_cpu_stats['cpu_usage'].get('total_usage', 0)
                            
                            current_system = cpu_stats.get('system_cpu_usage', 0)
                            prev_system = prev_cpu_stats.get('system_cpu_usage', 0)
                            
                            cpu_delta = current_total - prev_total
                            system_delta = current_system - prev_system
                            
                            online_cpus = len(cpu_stats['cpu_usage'].get('percpu_usage', [1]))
                            
                            if system_delta > 0 and cpu_delta >= 0:
                                cpu_percent = (cpu_delta / system_delta) * online_cpus * 100.0
                    except (KeyError, ZeroDivisionError, TypeError):
                        cpu_percent = 0.0
                
                # Pamięć
                mem_usage = 0.0
                mem_limit = 1.0
                mem_percent = 0.0
                
                try:
                    memory_stats = stats.get('memory_stats', {})
                    mem_usage = memory_stats.get('usage', 0) / (1024*1024)
                    mem_limit = memory_stats.get('limit', 1) / (1024*1024)
                    mem_percent = (mem_usage / mem_limit) * 100.0 if mem_limit > 0 else 0
                except (KeyError, TypeError, ZeroDivisionError):
                    mem_usage = 0.0
                    mem_limit = 1.0
                    mem_percent = 0.0
                
                # Wyczyść ekran i wyświetl aktualne statystyki
                os.system('clear' if os.name == 'posix' else 'cls')
                console.print(f"[bold cyan]📊 Live monitoring: {container_name}[/bold cyan]")
                console.print(f"[green]🖥️  CPU: {cpu_percent:.2f}%[/green]")
                console.print(f"[blue]💾 RAM: {mem_usage:.1f}MB / {mem_limit:.1f}MB ({mem_percent:.1f}%)[/blue]")
                console.print(f"[yellow]⏱️  Czas: {int(current_time - start_time)}/{duration}s[/yellow]")
                console.print(f"[dim]Naciśnij Ctrl+C aby zatrzymać[/dim]")
                
                prev_stats = stats
                
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                console.print(f"[yellow]Błąd parsowania danych: {e}[/yellow]")
                continue
            except Exception as e:
                console.print(f"[yellow]Błąd w przetwarzaniu statystyk: {e}[/yellow]")
                continue
            
            time.sleep(1)
            
    except KeyboardInterrupt:
        console.print(f"\n[yellow]Monitorowanie przerwane przez użytkownika[/yellow]")
    except docker.errors.NotFound:
        console.print(f"[bold red]Nie znaleziono kontenera: {container_name}[/bold red]")
    except Exception as e:
        console.print(f"[bold red]Błąd podczas monitorowania: {e}[/bold red]")

# --- Nowe funkcjonalności deployment ---

def health_check(port, endpoint="/health", timeout=30, max_retries=10):
    """Sprawdza czy aplikacja odpowiada na health check"""
    url = f"http://localhost:{port}{endpoint}"
    console.print(f"[cyan]Sprawdzam health check: {url}[/cyan]")
    
    for i in range(max_retries):
        try:
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                console.print(f"[green]✓ Health check OK (próba {i+1}/{max_retries})[/green]")
                return True
            else:
                console.print(f"[yellow]Health check zwrócił {response.status_code} (próba {i+1}/{max_retries})[/yellow]")
        except requests.exceptions.RequestException as e:
            console.print(f"[yellow]Health check failed (próba {i+1}/{max_retries}): {e}[/yellow]")
        
        if i < max_retries - 1:
            time.sleep(3)
    
    console.print(f"[red]✗ Health check failed po {max_retries} próbach[/red]")
    return False

def quick_deploy(dockerfile_path=".", image_tag=None, container_name=None, port_mapping=None):
    """
    Szybkie wdrożenie: build -> stop old -> remove old -> run new
    Idealne do developmentu i szybkich deployów
    """
    if not image_tag:
        image_tag = input("Tag obrazu (np. myapp:v1.2): ") or "myapp:latest"
    if not container_name:
        container_name = input("Nazwa kontenera: ") or "myapp"
    if not port_mapping:
        port_input = input("Mapowanie portów (np. 8080:8080): ")
        if port_input and ":" in port_input:
            host_port, container_port = port_input.split(":")
            port_mapping = {container_port: host_port}
    
    console.print(f"\n[bold cyan]=== QUICK DEPLOY ===[/bold cyan]")
    console.print(f"Obraz: {image_tag}")
    console.print(f"Kontener: {container_name}")
    console.print(f"Porty: {port_mapping}")
    
    # Krok 1: Build obrazu
    console.print(f"\n[cyan]1/4 - Budowanie obrazu {image_tag}...[/cyan]")
    try:
        image, logs = client.images.build(path=dockerfile_path, tag=image_tag, rm=True)
        console.print(f"[green]✓ Obraz zbudowany pomyślnie[/green]")
    except Exception as e:
        console.print(f"[bold red]✗ Błąd budowania: {e}[/bold red]")
        return False
    
    # Krok 2: Zatrzymanie starego kontenera
    console.print(f"\n[cyan]2/4 - Zatrzymywanie starego kontenera...[/cyan]")
    try:
        old_container = client.containers.get(container_name)
        if old_container.status == "running":
            old_container.stop()
            console.print(f"[green]✓ Stary kontener zatrzymany[/green]")
    except docker.errors.NotFound:
        console.print(f"[yellow]- Nie znaleziono starego kontenera (pierwsze uruchomienie)[/yellow]")
    
    # Krok 3: Usunięcie starego kontenera
    console.print(f"\n[cyan]3/4 - Usuwanie starego kontenera...[/cyan]")
    try:
        old_container = client.containers.get(container_name)
        old_container.remove()
        console.print(f"[green]✓ Stary kontener usunięty[/green]")
    except docker.errors.NotFound:
        console.print(f"[yellow]- Brak kontenera do usunięcia[/yellow]")
    
    # Krok 4: Uruchomienie nowego kontenera
    console.print(f"\n[cyan]4/4 - Uruchamianie nowego kontenera...[/cyan]")
    try:
        new_container = client.containers.run(
            image_tag, 
            name=container_name, 
            detach=True, 
            ports=port_mapping,
            restart_policy={"Name": "unless-stopped"}
        )
        console.print(f"[green]✓ Nowy kontener uruchomiony[/green]")
        
        # Opcjonalny health check
        if port_mapping:
            host_port = list(port_mapping.values())[0]
            console.print(f"\n[cyan]Sprawdzam czy aplikacja działa na porcie {host_port}...[/cyan]")
            if health_check(host_port, endpoint="/", max_retries=5):
                console.print(f"[bold green]🎉 DEPLOY ZAKOŃCZONY POMYŚLNIE![/bold green]")
            else:
                console.print(f"[yellow]⚠️  Deploy zakończony, ale health check nie przeszedł[/yellow]")
        
        return True
        
    except Exception as e:
        console.print(f"[bold red]✗ Błąd uruchamiania kontenera: {e}[/bold red]")
        return False

def blue_green_deploy(dockerfile_path=".", image_tag=None, service_name=None, port=None, health_endpoint="/health"):
    """
    Blue-Green deployment: 
    - Uruchamia nową wersję obok starej (green)
    - Sprawdza health check
    - Przełącza ruch z blue na green
    - Usuwa starą wersję
    """
    if not image_tag:
        image_tag = input("Tag nowego obrazu (np. myapp:v1.3): ") or "myapp:latest"
    if not service_name:
        service_name = input("Nazwa serwisu (np. myapp): ") or "myapp"
    if not port:
        port = input("Port aplikacji (np. 8080): ") or "8080"
    
    blue_name = f"{service_name}-blue"
    green_name = f"{service_name}-green"
    
    console.print(f"\n[bold cyan]=== BLUE-GREEN DEPLOYMENT ===[/bold cyan]")
    console.print(f"Serwis: {service_name}")
    console.print(f"Nowy obraz: {image_tag}")
    console.print(f"Port: {port}")
    console.print(f"Blue container: {blue_name}")
    console.print(f"Green container: {green_name}")
    
    # Krok 1: Build nowego obrazu
    console.print(f"\n[cyan]1/6 - Budowanie nowego obrazu...[/cyan]")
    try:
        image, logs = client.images.build(path=dockerfile_path, tag=image_tag, rm=True)
        console.print(f"[green]✓ Obraz zbudowany pomyślnie[/green]")
    except Exception as e:
        console.print(f"[bold red]✗ Błąd budowania: {e}[/bold red]")
        return False
    
    # Krok 2: Sprawdź aktualnie działającą wersję (blue)
    console.print(f"\n[cyan]2/6 - Sprawdzanie obecnej wersji...[/cyan]")
    current_container = None
    try:
        # Sprawdź czy blue jest aktywny
        current_container = client.containers.get(blue_name)
        if current_container.status == "running":
            console.print(f"[green]✓ Znaleziono działający blue container na porcie {port}[/green]")
        else:
            current_container = None
    except docker.errors.NotFound:
        console.print(f"[yellow]- Nie znaleziono blue container (pierwsze wdrożenie)[/yellow]")
    
    # Krok 3: Usuń stary green container jeśli istnieje
    console.print(f"\n[cyan]3/6 - Czyszczenie starego green container...[/cyan]")
    try:
        old_green = client.containers.get(green_name)
        old_green.stop()
        old_green.remove()
        console.print(f"[green]✓ Stary green container usunięty[/green]")
    except docker.errors.NotFound:
        console.print(f"[yellow]- Brak starego green container[/yellow]")
    
    # Krok 4: Uruchom green container na porcie tymczasowym
    temp_port = str(int(port) + 1000)  # np. 8080 -> 9080
    console.print(f"\n[cyan]4/6 - Uruchamianie green container na porcie {temp_port}...[/cyan]")
    try:
        green_container = client.containers.run(
            image_tag,
            name=green_name,
            detach=True,
            ports={port: temp_port},
            restart_policy={"Name": "unless-stopped"}
        )
        console.print(f"[green]✓ Green container uruchomiony[/green]")
        
        # Czekamy chwilę na startup
        time.sleep(3)
        
    except Exception as e:
        console.print(f"[bold red]✗ Błąd uruchamiania green container: {e}[/bold red]")
        return False
    
    # Krok 5: Health check green container
    console.print(f"\n[cyan]5/6 - Health check green container...[/cyan]")
    if not health_check(temp_port, health_endpoint, max_retries=10):
        console.print(f"[bold red]✗ Green container nie przechodzi health check![/bold red]")
        console.print(f"[yellow]Czy chcesz kontynuować mimo błędnego health check? (y/N):[/yellow]")
        if input().lower() != 'y':
            # Rollback - usuń green
            try:
                green_container.stop()
                green_container.remove()
                console.print(f"[yellow]Green container usunięty (rollback)[/yellow]")
            except:
                pass
            return False
    
    # Krok 6: Switch - zatrzymaj blue, przełącz green na główny port
    console.print(f"\n[cyan]6/6 - Przełączanie ruchu...[/cyan]")
    try:
        # Zatrzymaj blue
        if current_container:
            current_container.stop()
            console.print(f"[green]✓ Blue container zatrzymany[/green]")
        
        # Zatrzymaj green
        green_container.stop()
        green_container.remove()
        
        # Uruchom green na głównym porcie jako nowy blue
        new_blue = client.containers.run(
            image_tag,
            name=blue_name,
            detach=True,
            ports={port: port},
            restart_policy={"Name": "unless-stopped"}
        )
        
        console.print(f"[green]✓ Ruch przełączony na nową wersję[/green]")
        
        # Usuń stary blue container
        if current_container:
            try:
                current_container.remove()
                console.print(f"[green]✓ Stary blue container usunięty[/green]")
            except:
                console.print(f"[yellow]- Nie udało się usunąć starego blue container[/yellow]")
        
        # Final health check
        console.print(f"\n[cyan]Finalny health check na porcie {port}...[/cyan]")
        if health_check(port, health_endpoint, max_retries=5):
            console.print(f"[bold green]🎉 BLUE-GREEN DEPLOYMENT ZAKOŃCZONY POMYŚLNIE![/bold green]")
            console.print(f"[green]Aplikacja dostępna na http://localhost:{port}[/green]")
        else:
            console.print(f"[bold red]⚠️  UWAGA: Finalny health check nie przeszedł![/bold red]")
        
        return True
        
    except Exception as e:
        console.print(f"[bold red]✗ Błąd podczas przełączania: {e}[/bold red]")
        console.print(f"[yellow]Próba rollback...[/yellow]")
        # TODO: Implementuj rollback logic
        return False

def health_check_menu():
    """Menu do testowania health check"""
    port = input("Port aplikacji: ").strip()
    if not port:
        console.print("[yellow]Nie podano portu, wracam do menu.[/yellow]")
        return
    
    endpoint = input("Endpoint health check [/health]: ").strip() or "/health"
    max_retries = input("Maksymalna liczba prób [5]: ").strip() or "5"
    
    try:
        max_retries = int(max_retries)
        port = int(port)
    except ValueError:
        console.print("[bold red]Nieprawidłowy format liczby![/bold red]")
        return
    
    health_check(port, endpoint, max_retries=max_retries)

def main_menu():
    while True:
        console.print("\n[bold cyan]=== DOCKER PILOT ===[/bold cyan]")
        console.print("1. Lista kontenerów")
        console.print("2. Zatrzymaj kontener")
        console.print("3. Zatrzymaj i usuń kontener")
        console.print("4. Uruchom istniejący kontener")
        console.print("5. Usuń obraz")
        console.print("6. Build obrazu z Dockerfile")
        console.print("7. Uruchom nowy kontener")
        console.print("8. Lista obrazów")
        console.print("9. Ustaw restart policy kontenera")
        console.print("10. Inspekcja kontenera (json)")
        console.print("11. Pokaż logi kontenera")
        console.print("12. Wykonaj polecenie w kontenerze")
        console.print("13. Statystyki kontenera")
        console.print("17. Live monitoring kontenera (30s)")
        console.print("[bold yellow]--- Deployment ---[/bold yellow]")
        console.print("14. Quick Deploy (build + replace)")
        console.print("15. Blue-Green Deploy")
        console.print("16. Health Check aplikacji")
        console.print("0. Wyjście")

        choice = input("Wybierz opcję: ").strip()

        if choice == "1":
            list_containers()
        elif choice == "2":
            name = input("Nazwa/ID kontenera: ").strip()
            if not name:
                console.print("[yellow]Nie podano nazwy kontenera, wracam do menu.[/yellow]")
                continue
            stop_container(name)
        elif choice == "3":
            name = input("Nazwa/ID kontenera: ").strip()
            if not name:
                console.print("[yellow]Nie podano nazwy kontenera, wracam do menu.[/yellow]")
                continue
            stop_and_remove(name)
        elif choice == "4":
            name = input("Nazwa/ID kontenera: ").strip()
            if not name:
                console.print("[yellow]Nie podano nazwy kontenera, wracam do menu.[/yellow]")
                continue
            start_existing_container(name)
        elif choice == "5":
            img = input("Nazwa obrazu: ").strip()
            if not img:
                console.print("[yellow]Nie podano nazwy obrazu, wracam do menu.[/yellow]")
                continue
            remove_image(img)
        elif choice == "6":
            tag = input("Tag obrazu (np. myimage:latest): ").strip() or "myimage:latest"
            build_image(".", tag)
        elif choice == "7":
            img = input("Obraz: ").strip()
            if not img:
                console.print("[yellow]Nie podano nazwy obrazu, wracam do menu.[/yellow]")
                continue
            name = input("Nazwa kontenera: ").strip()
            if not name:
                console.print("[yellow]Nie podano nazwy kontenera, wracam do menu.[/yellow]")
                continue
            port_map = input("Mapowanie portów (np. 8080:80, 9090:90; puste = brak): ").strip()
            command = input("Polecenie do uruchomienia w kontenerze (puste = domyślne): ").strip() or None

            ports = {}
            if port_map:
                try:
                    mappings = [p.strip() for p in port_map.split(",") if p.strip()]
                    for mapping in mappings:
                        host_port, container_port = mapping.split(":")
                        ports[int(container_port)] = int(host_port)
                except Exception:
                    console.print("[bold red]Błędny format mapowania portów! Użyj formatu 'host_port:container_port', np. '8080:80, 9090:90'[/bold red]")
                    continue
            else:
                ports = None

            run_new_container(img, name, ports, command)
        elif choice == "8":
            list_images()
        elif choice == "9":
            name = input("Nazwa/ID kontenera: ").strip()
            if not name:
                console.print("[yellow]Nie podano nazwy kontenera, wracam do menu.[/yellow]")
                continue
            policy = input("Restart policy (no, on-failure, always, unless-stopped): ").strip()
            if policy not in ("no", "on-failure", "always", "unless-stopped"):
                console.print("[bold red]Nieprawidłowa polityka restartu![/bold red]")
                continue
            update_restart_policy(name, policy)
        elif choice == "10":
            name = input("Nazwa/ID kontenera: ").strip()
            if not name:
                console.print("[yellow]Nie podano nazwy kontenera, wracam do menu.[/yellow]")
                continue
            inspect_container(name)
        elif choice == "11":
            name = input("Nazwa/ID kontenera: ").strip()
            if not name:
                console.print("[yellow]Nie podano nazwy kontenera, wracam do menu.[/yellow]")
                continue
            tail = input("Ile linii logów pokazać? [domyślnie 100]: ").strip()
            try:
                tail = int(tail)
            except ValueError:
                tail = 100
            logs_container(name, tail)
        elif choice == "12":
            name = input("Nazwa/ID kontenera: ").strip()
            if not name:
                console.print("[yellow]Nie podano nazwy kontenera, wracam do menu.[/yellow]")
                continue
            cmd = input("Polecenie do wykonania (np. ls -la): ").strip()
            if not cmd:
                console.print("[yellow]Nie podano polecenia, wracam do menu.[/yellow]")
                continue
            exec_in_container(name, cmd)
        elif choice == "13":
            name = input("Nazwa/ID kontenera: ").strip()
            if not name:
                console.print("[yellow]Nie podano nazwy kontenera, wracam do menu.[/yellow]")
                continue
            stats_container(name)
        elif choice == "14":
            dockerfile_path = input("Ścieżka do katalogu z Dockerfile [.]: ").strip() or "."
            quick_deploy(dockerfile_path)
        elif choice == "15":
            dockerfile_path = input("Ścieżka do katalogu z Dockerfile [.]: ").strip() or "."
            health_endpoint = input("Health check endpoint [/health]: ").strip() or "/health"
            blue_green_deploy(dockerfile_path, health_endpoint=health_endpoint)
        elif choice == "16":
            health_check_menu()
        elif choice == "17":
            name = input("Nazwa/ID kontenera: ").strip()
            if not name:
                console.print("[yellow]Nie podano nazwy kontenera, wracam do menu.[/yellow]")
                continue
            duration = input("Czas monitorowania w sekundach [30]: ").strip()
            try:
                duration = int(duration) if duration else 30
            except ValueError:
                duration = 30
            monitor_container_live(name, duration)
        elif choice == "0":
            console.print("[green]Do widzenia![/green]")
            sys.exit(0)
        else:
            console.print("[red]Nieprawidłowa opcja[/red]")

if __name__ == "__main__":
    print_banner()
    main_menu()
