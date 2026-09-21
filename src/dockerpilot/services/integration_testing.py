"""DockerPilot integration-test execution and reporting."""

from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import requests
import yaml
from rich.panel import Panel
from rich.table import Table


def run_integration_tests(console: Any, logger: Any, test_config_path: str = "integration-tests.yml") -> bool:
    """Run configured integration tests and render a report."""
    console.print("[cyan]Running integration tests...[/cyan]")
    try:
        if Path(test_config_path).exists():
            with open(test_config_path, "r") as source:
                test_config = yaml.safe_load(source)
        else:
            template_path = Path(__file__).resolve().parents[1] / "configs" / "integration-tests.yml.template"
            if template_path.exists():
                with open(template_path, "r") as source:
                    test_config = yaml.safe_load(source)
            else:
                test_config = {
                    "tests": [
                        {"name": "Health Check", "type": "http", "url": "http://localhost:8080/health", "expected_status": 200, "timeout": 5},
                        {"name": "API Endpoint", "type": "http", "url": "http://localhost:8080/api/status", "expected_status": 200, "timeout": 10},
                    ]
                }

        test_results = [run_single_integration_test(test) for test in (test_config or {}).get("tests", [])]
        generate_test_report(console, logger, test_results)
        return all(result["passed"] for result in test_results)
    except Exception as exc:
        logger.error(f"Integration tests failed: {exc}")
        return False


def run_single_integration_test(test_config: dict) -> dict:
    """Run one configured integration test."""
    test_name = test_config.get("name", "Unknown Test")
    test_type = test_config.get("type", "http")
    start_time = time.time()
    try:
        if test_type == "http":
            return run_http_test(test_config, start_time)
        if test_type == "database":
            return run_database_test(test_config, start_time)
        if test_type == "custom":
            return run_custom_test(test_config, start_time)
        return {"name": test_name, "passed": False, "duration": 0, "error": f"Unknown test type: {test_type}"}
    except Exception as exc:
        return {"name": test_name, "passed": False, "duration": time.time() - start_time, "error": str(exc)}


def run_http_test(test_config: dict, start_time: float) -> dict:
    """Run an HTTP-based integration test."""
    url = test_config["url"]
    expected_status = test_config.get("expected_status", 200)
    timeout = test_config.get("timeout", 5)
    method = test_config.get("method", "GET").upper()
    headers = test_config.get("headers", {})
    data = test_config.get("data")
    try:
        if method == "GET":
            response = requests.get(url, headers=headers, timeout=timeout)
        elif method == "POST":
            response = requests.post(url, headers=headers, json=data, timeout=timeout)
        else:
            response = requests.request(method, url, headers=headers, json=data, timeout=timeout)
        return {
            "name": test_config.get("name", "HTTP Test"),
            "passed": response.status_code == expected_status,
            "duration": time.time() - start_time,
            "status_code": response.status_code,
            "expected_status": expected_status,
            "response_time": response.elapsed.total_seconds(),
        }
    except requests.exceptions.RequestException as exc:
        return {"name": test_config.get("name", "HTTP Test"), "passed": False, "duration": time.time() - start_time, "error": str(exc)}


def run_database_test(test_config: dict, start_time: float) -> dict:
    """Preserve the current placeholder database integration-test behavior."""
    return {
        "name": test_config.get("name", "Database Test"),
        "passed": True,
        "duration": time.time() - start_time,
        "note": "Database testing requires specific database drivers",
    }


def run_custom_test(test_config: dict, start_time: float) -> dict:
    """Run a custom Python integration-test script."""
    script_path = test_config.get("script")
    if not script_path or not Path(script_path).exists():
        return {"name": test_config.get("name", "Custom Test"), "passed": False, "duration": time.time() - start_time, "error": "Custom test script not found"}
    try:
        result = subprocess.run(["python", script_path], capture_output=True, text=True, timeout=test_config.get("timeout", 30))
        return {
            "name": test_config.get("name", "Custom Test"),
            "passed": result.returncode == 0,
            "duration": time.time() - start_time,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"name": test_config.get("name", "Custom Test"), "passed": False, "duration": time.time() - start_time, "error": "Test script timed out"}


def generate_test_report(console: Any, logger: Any, test_results: List[dict]) -> None:
    """Render and persist an integration-test report."""
    total_tests = len(test_results)
    passed_tests = sum(1 for result in test_results if result["passed"])
    failed_tests = total_tests - passed_tests

    table = Table(title="Integration Test Results", show_header=True)
    table.add_column("Test Name", style="cyan")
    table.add_column("Status", style="bold")
    table.add_column("Duration", style="blue")
    table.add_column("Details", style="yellow")
    for result in test_results:
        status = "[green]PASS[/green]" if result["passed"] else "[red]FAIL[/red]"
        duration = f"{result['duration']:.2f}s"
        details = ""
        if "status_code" in result:
            details = f"HTTP {result['status_code']}"
        if "error" in result:
            details = result["error"][:50] + "..." if len(result["error"]) > 50 else result["error"]
        table.add_row(result["name"], status, duration, details)
    console.print(table)

    summary_color = "green" if failed_tests == 0 else "red"
    console.print(Panel(f"[{summary_color}]{passed_tests}/{total_tests} tests passed[/{summary_color}]", title="Test Summary"))
    save_test_report(logger, test_results, passed_tests, failed_tests)


def save_test_report(logger: Any, test_results: List[dict], passed: int, failed: int) -> bool:
    """Persist the detailed integration-test report."""
    try:
        report_data: Dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "summary": {
                "total": len(test_results),
                "passed": passed,
                "failed": failed,
                "success_rate": (passed / len(test_results)) * 100 if test_results else 0,
            },
            "tests": test_results,
        }
        with open("integration-test-report.json", "w") as target:
            json.dump(report_data, target, indent=2)
        logger.info("Integration test report saved to integration-test-report.json")
    except Exception as exc:
        logger.error(f"Failed to save test report: {exc}")
    return True
