"""Service for generating PDF from CV JSON using WeasyPrint and Jinja2"""

import logging
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import CSS, HTML
from weasyprint.text.fonts import FontConfiguration

logger = logging.getLogger(__name__)


class PDFGenerator:
    """Generates professional PDF resumes from CV JSON data using WeasyPrint"""

    SUPPORTED_TEMPLATES = ["modern", "classic", "minimal", "compact", "profile-card"]
    DEFAULT_TEMPLATE = "modern"

    def __init__(
        self,
        template_dir: str | Path = "src/templates/cv",
        template_name: str = DEFAULT_TEMPLATE,
        font_config: FontConfiguration | None = None,
    ):
        """
        Initialize PDF Generator with template configuration

        Args:
            template_dir: Directory containing CV templates
            template_name: Name of template theme to use (modern/classic/minimal)
            font_config: Optional WeasyPrint font configuration

        Raises:
            ValueError: If template directory or theme doesn't exist
        """
        self.template_dir = Path(template_dir)
        self.template_name = template_name
        self.font_config = font_config or FontConfiguration()

        # Validate template exists
        template_path = self.template_dir / template_name
        if not template_path.exists():
            raise ValueError(f"Template '{template_name}' not found at {template_path}")

        # Setup Jinja2 environment
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(self.template_dir / template_name)),
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )

        # Register custom filters
        self.jinja_env.filters["format_date"] = self._format_date

        # Cache CSS at initialization to avoid repeated file reads
        self._cached_css: str | None = None
        self._load_and_cache_css()

        logger.info(f"PDFGenerator initialized with template: {template_name}")

    def _load_and_cache_css(self) -> None:
        """Load and cache CSS file for current template"""
        css_path = self.template_dir / self.template_name / "style.css"
        with open(css_path, encoding="utf-8") as f:
            self._cached_css = f.read()
        logger.debug(f"Cached CSS from {css_path}")

    def generate_pdf(
        self,
        cv_json: dict,
        output_path: str | Path,
        metadata: dict | None = None,
    ) -> str:
        """
        Generate PDF from CV JSON data

        Args:
            cv_json: CV data as dictionary (matches CV Pydantic model)
            output_path: Where to save the generated PDF
            metadata: Optional PDF metadata (title, author, etc.)

        Returns:
            Absolute path to generated PDF file

        Raises:
            ValueError: If CV data is invalid
            IOError: If PDF generation fails
        """
        full_name = cv_json.get("contact", {}).get("full_name", "Unknown")
        logger.info(f"Generating PDF for {full_name}")

        try:
            # Step 1: Convert CV JSON to HTML
            html_content = self._cv_to_html(cv_json)

            # Step 2: Load CSS
            css_content = self._load_css()

            # Step 3: Generate PDF using WeasyPrint
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Create HTML and CSS objects
            base_url = str(self.template_dir / self.template_name)
            html = HTML(string=html_content, base_url=base_url)
            css = CSS(string=css_content, font_config=self.font_config)

            # Set PDF metadata
            pdf_metadata = self._build_metadata(cv_json, metadata)

            # Generate PDF
            document = html.render(stylesheets=[css], font_config=self.font_config)

            # Set metadata attributes (WeasyPrint DocumentMetadata doesn't have update())
            for key, value in pdf_metadata.items():
                setattr(document.metadata, key, value)

            document.write_pdf(str(output_path))

            logger.info(f"PDF generated successfully: {output_path}")
            return str(output_path.absolute())

        except Exception as e:
            logger.error(f"PDF generation failed: {e}", exc_info=True)
            raise OSError(f"Failed to generate PDF: {e}") from e

    def _cv_to_html(self, cv_json: dict) -> str:
        """
        Convert CV JSON to HTML using Jinja2 template

        Args:
            cv_json: CV data dictionary

        Returns:
            Rendered HTML string
        """
        template = self.jinja_env.get_template("template.html.j2")
        html = template.render(cv=cv_json)
        return html

    def render_html(self, cv_json: dict) -> str:
        """
        Render CV data to standalone HTML with embedded CSS.

        This method returns a complete HTML document with styles embedded,
        suitable for browser preview (without PDF conversion).

        Args:
            cv_json: CV data dictionary

        Returns:
            Complete HTML string with embedded CSS
        """
        # Get the base HTML content
        html_content = self._cv_to_html(cv_json)
        css_content = self._load_css()

        # Embed CSS into HTML for standalone rendering
        # Wrap in a complete HTML document structure
        full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{cv_json.get('contact', {}).get('full_name', 'CV')} - Resume</title>
    <style>
{css_content}
    </style>
</head>
<body>
{html_content}
</body>
</html>"""
        return full_html

    def _load_css(self) -> str:
        """Return cached CSS for current template"""
        if self._cached_css is None:
            self._load_and_cache_css()
        return self._cached_css

    def _build_metadata(self, cv_json: dict, custom_metadata: dict | None) -> dict:
        """Build PDF metadata dictionary"""
        full_name = cv_json.get("contact", {}).get("full_name", "Unknown")
        metadata = {
            "title": f"{full_name} - Resume",
            "author": full_name,
            "subject": "Professional Resume",
            "creator": "LinkedIn Job Application Agent",
        }

        if custom_metadata:
            metadata.update(custom_metadata)

        return metadata

    @staticmethod
    def _format_date(date_value: str | date | None) -> str:
        """
        Format date for display in resume

        Args:
            date_value: Date as string (YYYY-MM-DD), date object, or None

        Returns:
            Formatted date string (e.g., "Jan 2020") or "Present" for None
        """
        if date_value is None:
            return "Present"

        if isinstance(date_value, str):
            if not date_value or date_value.lower() in ["present", "current"]:
                return "Present"
            try:
                date_obj = date.fromisoformat(date_value)
            except ValueError:
                return date_value
        elif isinstance(date_value, date):
            date_obj = date_value
        else:
            return str(date_value)

        return date_obj.strftime("%b %Y")
