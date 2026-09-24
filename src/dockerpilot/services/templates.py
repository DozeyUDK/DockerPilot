"""Template-backed documentation and checklist generation."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def create_production_checklist(
    console: Any,
    logger: Any,
    output_file: str = "production-checklist.md",
    *,
    templates_dir: Path | None = None,
) -> bool:
    """Generate a production deployment checklist from the packaged template."""
    try:
        root = templates_dir or (Path(__file__).resolve().parents[1] / "configs")
        template_path = root / "production-checklist.md.template"

        if not template_path.exists():
            logger.error(f"Template not found: {template_path}")
            console.print(f"[red]Template file not found: {template_path}[/red]")
            return False

        with open(template_path, "r", encoding="utf-8") as source:
            checklist_content = source.read()

        with open(output_file, "w", encoding="utf-8") as target:
            target.write(checklist_content)

        console.print(f"[green]Production checklist created: {output_file}[/green]")
        return True
    except Exception as exc:
        logger.error(f"Failed to create production checklist: {exc}")
        return False


def generate_documentation(
    console: Any,
    logger: Any,
    output_dir: str = "docs",
    *,
    templates_dir: Path | None = None,
) -> bool:
    """Generate project documentation from packaged templates."""
    try:
        docs_path = Path(output_dir)
        docs_path.mkdir(exist_ok=True)
        root = templates_dir or (Path(__file__).resolve().parents[1] / "configs")

        doc_files = [
            ("docs-readme.md.template", "README.md"),
            ("docs-api.md.template", "API.md"),
            ("docs-troubleshooting.md.template", "TROUBLESHOOTING.md"),
        ]

        for template_name, output_name in doc_files:
            template_path = root / template_name
            if not template_path.exists():
                logger.warning(f"Template not found: {template_path}")
                continue

            with open(template_path, "r", encoding="utf-8") as source:
                content = source.read()
            with open(docs_path / output_name, "w", encoding="utf-8") as target:
                target.write(content)
            logger.info(f"Generated {output_name}")

        console.print(f"[green]Documentation generated in {output_dir}/[/green]")
        return True
    except Exception as exc:
        logger.error(f"Failed to generate documentation: {exc}")
        return False
