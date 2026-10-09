import json
from bs4 import BeautifulSoup, Tag


def extract_html(source: str, scripts: str = "strip") -> tuple[str, dict[str, str]]:
    """Apply script-removal rules and return cleaned HTML plus mode metadata."""
    soup = BeautifulSoup(source, "html.parser")

    if scripts == "strip":
        for tag in soup.find_all("script"):
            tag.decompose()
    elif scripts == "until-first":
        first = soup.find("script")
        if first:
            for tag in list(first.find_all_next("script")):
                tag.decompose()
            first.decompose()

    return str(soup), {"script_mode": scripts}


def parse_source(html: str, page_url: str | None = None, page_title: str | None = None) -> dict[str, str | None]:
    """Build a source record containing HTML and optional page metadata."""
    return {"url": page_url, "title": page_title, "html": html}


def as_json(result: dict[str, object]) -> str:
    """Serialize a source record as formatted JSON text."""
    return json.dumps(result, ensure_ascii=False, indent=2)


def as_text(result: dict[str, object]) -> str:
    """Return the HTML field from a source record as text."""
    return result.get("html", "")


def _text_length(tag: Tag) -> int:
    """Return the visible text length of one HTML tag."""
    return len(tag.get_text(" ", strip=True))


def element_path(tag: Tag) -> str:
    """Return a compact CSS-ish path for diagnostics, not as a public selector guarantee."""
    parts = []
    current = tag
    while isinstance(current, Tag) and current.name != "[document]":
        part = current.name
        if current.get("id"):
            part += f"#{current['id']}"
        classes = current.get("class") or []
        if classes:
            safe_classes = [c for c in classes if isinstance(c, str) and c]
            part += "".join(f".{c.replace(' ', '_')}" for c in safe_classes[:2])
        parts.append(part)
        current = current.parent
    return " > ".join(reversed(parts))
