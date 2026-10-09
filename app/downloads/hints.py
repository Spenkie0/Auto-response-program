from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup


_DOWNLOAD_WORDS = (
    "télécharger",
    "telecharger",
    "download",
    "download file",
    "download document",
    "fichier à télécharger",
    "document à télécharger",
)

_EXPLICIT_DOWNLOAD_PHRASES = (
    "télécharger",
    "telecharger",
    "download",
    "fichier à télécharger",
    "document à télécharger",
    "téléchargez le document",
    "téléchargez le fichier",
    "télécharger le document",
    "télécharger le fichier",
)
_URL_ATTRS = ("data-download-url", "data-download-href", "data-url", "data-href", "href")


@dataclass(frozen=True)
class DownloadCandidate:
    """Represent one likely downloadable URL found inside a cleaned question window."""

    url: str
    label: str
    score: int
    reason: str


def _normalise_label(value: str) -> str:
    """Collapse whitespace so candidate labels are short and comparable."""
    return " ".join(value.split())[:240]


def _is_http_url(url: str) -> bool:
    """Return whether a candidate URL uses HTTP or HTTPS."""
    return urlsplit(url).scheme.lower() in {"http", "https"}


def _looks_like_accepted_extension(url: str, accepted_extensions: list[str]) -> bool:
    """Return whether the URL path ends with one of the configured accepted extensions."""
    path = urlsplit(url).path.lower()
    return any(path.endswith(ext.lower()) for ext in accepted_extensions)


def _extract_onclick_url(value: str) -> str | None:
    """Extract a quoted HTTP(S) URL from a simple onclick/data handler when present."""
    match = re.search(r"['\"]((?:https?://)[^'\"]+)['\"]", value or "")
    return match.group(1) if match else None


def find_download_candidates(
    clean_html: str,
    page_url: str,
    accepted_extensions: list[str],
) -> list[DownloadCandidate]:
    """Find and rank likely direct-download URLs without making any network request."""
    soup = BeautifulSoup(clean_html, "html.parser")
    candidates: dict[str, DownloadCandidate] = {}

    def add(raw_url: str | None, label: str, score: int, reason: str) -> None:
        """Add or upgrade one candidate after resolving and validating its URL."""
        if not raw_url:
            return
        absolute = urljoin(page_url, raw_url) if page_url else raw_url
        if not _is_http_url(absolute):
            return
        label_clean = _normalise_label(label)
        existing = candidates.get(absolute)
        candidate = DownloadCandidate(absolute, label_clean, score, reason)
        if existing is None or candidate.score > existing.score:
            candidates[absolute] = candidate

    for tag in soup.find_all(["a", "button"]):
        label = tag.get_text(" ", strip=True)
        attrs = tag.attrs
        attr_url = None
        attr_reason = "link attribute"
        for attr in _URL_ATTRS:
            value = attrs.get(attr)
            if isinstance(value, str) and value.strip():
                attr_url = value.strip()
                if attr != "href":
                    attr_reason = attr
                break
        if attr_url is None:
            onclick = attrs.get("onclick")
            if isinstance(onclick, str):
                attr_url = _extract_onclick_url(onclick)
                if attr_url:
                    attr_reason = "onclick URL"
        if not attr_url:
            continue

        lowered = label.lower()
        score = 0
        reasons: list[str] = []
        if "download" in attrs or attrs.get("data-download") is not None:
            score += 90
            reasons.append("download attribute")
        if any(word in lowered for word in _DOWNLOAD_WORDS):
            score += 70
            reasons.append("download wording")
        if _looks_like_accepted_extension(urljoin(page_url, attr_url), accepted_extensions):
            score += 80
            reasons.append("accepted extension in URL")
        if "download" in urlsplit(urljoin(page_url, attr_url)).path.lower():
            score += 20
            reasons.append("download-like URL")
        if score:
            add(attr_url, label, score, ", ".join(reasons) or attr_reason)

    for tag in soup.select("[data-download-url], [data-download-href]"):
        url = tag.get("data-download-url") or tag.get("data-download-href")
        label = tag.get_text(" ", strip=True)
        if isinstance(url, str):
            add(url, label, 100, "data download URL")

    return sorted(candidates.values(), key=lambda item: (-item.score, item.url))


def select_download_candidate(candidates: list[DownloadCandidate], minimum_score: int = 70) -> DownloadCandidate | None:
    """Select the safest obvious candidate, refusing ambiguous low-confidence choices."""
    if not candidates or candidates[0].score < minimum_score:
        return None
    if len(candidates) == 1:
        return candidates[0]
    if candidates[0].score - candidates[1].score >= 15:
        return candidates[0]
    return None


def page_may_require_download(clean_html: str) -> bool:
    """Return whether the page has an explicit signal that a resource must be downloaded.

    Generic mentions of words such as ``fichier`` or ``document`` are deliberately
    ignored: many questions discuss files without asking the user/program to download
    anything. File inputs are upload controls, not download controls, so they are not a
    download signal either.
    """
    soup = BeautifulSoup(clean_html, "html.parser")

    # Strong structural signals: the page exposes an explicit download affordance or
    # metadata that identifies a downloadable resource.
    if soup.select("a[download], button[data-download], [data-download-url], [data-download-href]"):
        return True

    # Interactive controls can trigger downloads through JavaScript even when no URL is
    # present in the HTML. Restrict this textual check to controls rather than scanning
    # every div/p/span on the page, which caused false positives for normal prose such as
    # "deux fichiers" or "un document".
    for tag in soup.find_all(["a", "button", "[role='button']"]):
        text = _normalise_label(tag.get_text(" ", strip=True)).lower()
        if text and any(keyword in text for keyword in _EXPLICIT_DOWNLOAD_PHRASES):
            return True

        # An anchor pointing at an accepted-looking download URL is also a strong hint.
        if tag.name == "a":
            href = tag.get("href")
            if isinstance(href, str) and href.strip():
                href_lower = href.lower()
                if "download" in href_lower and _is_http_url(urljoin("https://placeholder.invalid/", href)):
                    return True

    # Explicit download instructions in question prose are useful when the actual
    # download button/link is generated later by JavaScript. Do not treat generic file or
    # document mentions as download requirements.
    for tag in soup.find_all(["p", "li", "label"]):
        text = _normalise_label(tag.get_text(" ", strip=True)).lower()
        if text and any(phrase in text for phrase in _EXPLICIT_DOWNLOAD_PHRASES):
            return True

    return False
