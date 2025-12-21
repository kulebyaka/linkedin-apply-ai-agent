"""Manual test script for PDF generation

Run this script in Docker or on Linux/macOS with GTK+ libraries installed:
    python scripts/test_pdf_generation.py

Or in Docker:
    docker-compose exec app python scripts/test_pdf_generation.py
"""

import json
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.pdf_generator import PDFGenerator

def test_pdf_generation():
    """Test PDF generation with master_cv.example.json"""

    # Load example CV
    cv_path = "data/cv/master_cv.example.json"
    print(f"Loading CV from: {cv_path}")

    with open(cv_path, "r") as f:
        cv_json = json.load(f)

    # Initialize PDF generator
    generator = PDFGenerator(
        template_dir="src/templates/cv",
        template_name="modern"
    )

    # Generate PDF
    output_path = "data/generated_cvs/test_resume_john_doe.pdf"

    print(f"\nGenerating PDF for: {cv_json['contact']['full_name']}")
    print(f"Template: modern")
    print(f"Output path: {output_path}")

    try:
        pdf_path = generator.generate_pdf(cv_json, output_path)
        print(f"\n[SUCCESS] PDF generated at: {pdf_path}")

        file_size = Path(pdf_path).stat().st_size
        print(f"File size: {file_size:,} bytes ({file_size / 1024:.1f} KB)")

        return True
    except Exception as e:
        print(f"\n[ERROR] {e}")
        print("\nNote: PDF generation requires GTK+ libraries.")
        print("If running on Windows, use Docker:")
        print("  docker-compose exec app python scripts/test_pdf_generation.py")

        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_pdf_generation()
    exit(0 if success else 1)
