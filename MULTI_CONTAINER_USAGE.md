# Obsługa wielu kontenerów/obrazów jednocześnie

## Opis

DockerPilot teraz obsługuje wykonywanie poleceń dla wielu kontenerów lub obrazów jednocześnie, przekazując je jako listę oddzieloną przecinkami.

## Składnia

Aby wykonać polecenie dla wielu kontenerów/obrazów, podaj ich nazwy lub ID oddzielone przecinkami:
```
nazwa1,nazwa2,nazwa3
```
lub
```
id1,id2,id3
```

Spacje wokół przecinków są ignorowane, więc można też używać:
```
nazwa1, nazwa2, nazwa3
```

## Wspierane polecenia

### 1. Start kontenerów
```bash
# CLI
dockerpilot container start app1,app2,app3

# Tryb interaktywny
> start
Container name(s) or ID(s) (comma-separated for multiple, e.g., app1,app2): app1,app2,app3
```

### 2. Stop kontenerów
```bash
# CLI
dockerpilot container stop app1,app2 --timeout 15

# Tryb interaktywny
> stop
Container name(s) or ID(s) (comma-separated for multiple, e.g., app1,app2): app1,app2
Timeout seconds [10]: 15
```

### 3. Restart kontenerów
```bash
# CLI
dockerpilot container restart app1,app2,app3

# Tryb interaktywny
> restart
Container name(s) or ID(s) (comma-separated for multiple, e.g., app1,app2): app1,app2,app3
```

### 4. Usuwanie kontenerów
```bash
# CLI
dockerpilot container remove app1,app2 --force

# Tryb interaktywny
> remove
Container name(s) or ID(s) (comma-separated for multiple, e.g., app1,app2): app1,app2
Force removal? [y/N]: y
```

### 5. Pause/Unpause kontenerów
```bash
# CLI
dockerpilot container pause app1,app2
dockerpilot container unpause app1,app2

# Tryb interaktywny
> pause
Container name(s) or ID(s) (comma-separated for multiple, e.g., app1,app2): app1,app2
```

### 6. Exec w kontenerach
```bash
# CLI - wykonuje komendę w każdym kontenerze po kolei
dockerpilot container exec app1,app2 --command "ls -la"

# Tryb interaktywny
> exec
Container name(s) or ID(s) (comma-separated for multiple, e.g., app1,app2): app1,app2
Command to execute [/bin/bash]: ls -la
```

**Uwaga:** Polecenia exec są wykonywane sekwencyjnie (jeden po drugim), co pozwala na interakcję z każdym kontenerem.

### 7. Logs z kontenerów
```bash
# CLI
dockerpilot container logs app1,app2,app3 --tail 100

# Tryb interaktywny
> logs
Container name(s) or ID(s) (comma-separated for multiple, empty for interactive select): app1,app2
```

Logi z każdego kontenera są wyświetlane po kolei z wyraźnym separatorem.

### 8. Usuwanie obrazów
```bash
# CLI
dockerpilot container remove-image nginx:latest,redis:alpine,postgres:13 --force

# Tryb interaktywny
> remove-image
Image name(s) or ID(s) to remove (comma-separated for multiple, e.g., img1:tag,img2:tag): nginx:latest,redis:alpine
Force removal? [y/N]: n
```

## Przykłady użycia

### Przykład 1: Restart wielu kontenerów aplikacji
```bash
dockerpilot container restart backend-api,frontend-web,worker-queue
```

### Przykład 2: Zatrzymanie wszystkich kontenerów microservices
```bash
dockerpilot container stop auth-service,user-service,payment-service,notification-service --timeout 20
```

### Przykład 3: Usunięcie starych obrazów
```bash
dockerpilot container remove-image myapp:v1.0,myapp:v1.1,myapp:v1.2 --force
```

### Przykład 4: Wykonanie polecenia w wielu kontenerach
```bash
dockerpilot container exec web1,web2,web3 --command "nginx -s reload"
```

### Przykład 5: Wyświetlenie logów z wielu kontenerów
```bash
dockerpilot container logs app1,app2,app3 --tail 50
```

### Przykład 6: Użycie ID kontenerów
```bash
dockerpilot container stop fa90f84e0007,5c867ecaebaf
```

## Obsługa błędów

- Jeśli operacja nie powiedzie się dla jednego z kontenerów, pozostałe będą nadal przetwarzane
- Po zakończeniu wszystkich operacji zostanie wyświetlone podsumowanie:
  - ✅ Wszystkie operacje zakończone sukcesem
  - ⚠️ Niektóre operacje nie powiodły się

## Tryb interaktywny vs CLI

Obie wersje (CLI i interaktywny) wspierają tę samą funkcjonalność. Wybór zależy od preferencji użytkownika:

- **CLI**: Szybkie, nadaje się do skryptowania
- **Interaktywny**: Przyjazny interfejs z podpowiedziami

## Dodatkowe informacje

- Nazwy kontenerów i ID mogą być mieszane w jednej liście
- Kolejność operacji odpowiada kolejności podanych kontenerów/obrazów
- Operacje są wykonywane synchronicznie (jedna po drugiej)
- Dla exec i logs, każdy kontener jest przetwarzany osobno z wyraźnym oznaczeniem

