"""Test script for CV template PDF generation

This script can test one or multiple CV templates.

Usage:
    # Test single template
    python scripts/test_cv_template.py compact

    # Test multiple templates
    python scripts/test_cv_template.py modern compact

    # Test all available templates
    python scripts/test_cv_template.py --all

Run in Docker on Windows:
    docker-compose exec app python scripts/test_cv_template.py compact
"""

import argparse
import json
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.pdf_generator import PDFGenerator


def test_template(template_name: str, cv_json: dict, verbose: bool = True) -> bool:
    """Test PDF generation with specified template

    Args:
        template_name: Name of template to test
        cv_json: CV data dictionary
        verbose: Whether to print detailed output

    Returns:
        True if successful, False otherwise
    """
    if verbose:
        print(f"\n{'=' * 60}")
        print(f"Testing template: {template_name}")
        print(f"{'=' * 60}")

    try:
        # Initialize PDF generator
        generator = PDFGenerator(template_dir="src/templates/cv", template_name=template_name)

        # Generate PDF
        output_path = f"data/generated_cvs/test_{template_name}_resume.pdf"

        if verbose:
            print(f"Generating PDF for: {cv_json['contact']['full_name']}")
            print(f"Output path: {output_path}")

        pdf_path = generator.generate_pdf(cv_json, output_path)

        file_size = Path(pdf_path).stat().st_size

        if verbose:
            print(f"\n[SUCCESS] PDF generated at: {pdf_path}")
            print(f"File size: {file_size:,} bytes ({file_size / 1024:.1f} KB)")

        return True

    except Exception as e:
        if verbose:
            print(f"\n[ERROR] Failed to generate PDF with '{template_name}' template")
            print(f"Error: {e}")
            import traceback

            traceback.print_exc()
        else:
            print(f"[ERROR] {template_name}: {e}")

        return False


def get_available_templates() -> list[str]:
    """Get list of available templates from PDFGenerator

    Returns:
        List of template names
    """
    return PDFGenerator.SUPPORTED_TEMPLATES


def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description="Test CV template PDF generation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s compact                    # Test single template
  %(prog)s modern compact             # Test multiple templates
  %(prog)s --all                      # Test all available templates
  %(prog)s --list                     # List available templates
        """,
    )

    parser.add_argument("templates", nargs="*", help="Template name(s) to test")

    parser.add_argument("--all", action="store_true", help="Test all available templates")

    parser.add_argument("--list", action="store_true", help="List available templates and exit")

    parser.add_argument(
        "--cv",
        default="data/cv/master_cv.example.json",
        help="Path to CV JSON file (default: data/cv/master_cv.example.json)",
    )

    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress detailed output")

    return parser.parse_args()


def main():
    """Main test function"""
    args = parse_args()

    # List templates and exit
    if args.list:
        available = get_available_templates()
        print("Available CV templates:")
        for template in available:
            print(f"  • {template}")
        return True

    # Determine which templates to test
    if args.all:
        templates_to_test = get_available_templates()
        if not args.quiet:
            print(f"Testing all {len(templates_to_test)} available templates...")
    elif args.templates:
        templates_to_test = args.templates
    else:
        # No templates specified
        print("Error: No templates specified. Use --help for usage information.")
        print("\nAvailable templates:")
        for template in get_available_templates():
            print(f"  • {template}")
        return False

    # Load CV data
    cv_path = args.cv
    if not args.quiet:
        print(f"Loading CV from: {cv_path}")

    try:
        with open(cv_path, "r", encoding="utf-8") as f:
            cv_json = json.load(f)
    except FileNotFoundError:
        print(f"[ERROR] CV file not found: {cv_path}")
        return False
    except json.JSONDecodeError as e:
        print(f"[ERROR] Invalid JSON in CV file: {e}")
        return False

    # Test templates
    results = {}
    verbose = not args.quiet

    for template in templates_to_test:
        results[template] = test_template(template, cv_json, verbose=verbose)

    # Summary
    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")

    for template, success in results.items():
        status = "SUCCESS" if success else "FAILED"
        status_symbol = "[OK]" if success else "[FAIL]"
        print(f"{template:20} - {status_symbol} {status}")

    all_success = all(results.values())
    success_count = sum(results.values())
    total_count = len(results)

    print(f"\nResults: {success_count}/{total_count} templates generated successfully")

    if all_success:
        print("\n[SUCCESS] All templates generated successfully!")
        print(f"\nGenerated PDFs saved to: data/generated_cvs/test_<template>_resume.pdf")
    else:
        print("\n[WARNING] Some templates failed to generate.")
        print("If running on Windows, use Docker:")
        print("  docker-compose exec app python scripts/test_cv_template.py <template>")

    return all_success


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
