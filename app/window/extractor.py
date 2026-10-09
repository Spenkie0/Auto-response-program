from dataclasses import dataclass
import re

from bs4 import BeautifulSoup, Tag

from ..parsing.source import element_path


@dataclass
class Candidate:
    index: int
    tag_name: str
    path: str
    score: float
    text: str
    html: str


@dataclass
class ExtractionResult:
    status: str
    html: str | None
    confidence: float
    reason: str
    candidates: list[Candidate]
    selected_index: int | None = None


class WindowExtractor:
    """Rule-first HTML window extractor with a deterministic heuristic fallback."""

    def __init__(self, rules: dict[str, object]) -> None:
        """Create an extractor from explicit selectors and heuristic scoring rules."""
        self.rules = rules
        self.h = rules.get("heuristic", {})

    def extract(self, html: str) -> ExtractionResult:
        """Return the selected question window or an uncertain extraction result."""
        soup = BeautifulSoup(html, "html.parser")

        # 1. Explicit selectors have priority over heuristics.
        selected = []
        for rule in self.rules.get("selectors", []):
            if isinstance(rule, dict):
                selector = str(rule.get("selector") or "").strip()
                name = str(rule.get("name") or selector)
                required = [str(x) for x in rule.get("required_selectors", []) if str(x).strip()]
                excluded = [str(x) for x in rule.get("excluded_selectors", []) if str(x).strip()]
            else:
                selector = str(rule).strip()
                name = selector
                required = []
                excluded = []
            if not selector:
                continue
            try:
                matches = soup.select(selector)
            except Exception:
                continue
            valid = []
            for tag in matches:
                if not isinstance(tag, Tag):
                    continue
                if required and not all(tag.select_one(req) for req in required):
                    continue
                if excluded and any(tag.select_one(sel) for sel in excluded):
                    continue
                valid.append(tag)
            selected.extend(valid)
            if selected:
                tag = selected[0]
                return ExtractionResult(
                    status="accepted",
                    html=str(tag),
                    confidence=1.0,
                    reason=f"Matched explicit selector: {name} ({selector})",
                    candidates=[],
                    selected_index=0,
                )

        # 2. Deterministic candidate scoring.
        candidates = self._rank_candidates(soup)
        if not candidates:
            return ExtractionResult(
                status="uncertain",
                html=None,
                confidence=0.0,
                reason="No plausible HTML window candidate was found.",
                candidates=[],
            )

        top = candidates[0]
        second = candidates[1] if len(candidates) > 1 else None
        margin = top.score - second.score if second else top.score
        min_score = float(self.h.get("min_score", 55.0))
        min_margin = float(self.h.get("min_margin", 12.0))

        # Confidence is deliberately conservative. It is a routing signal,
        # not a calibrated probability.
        confidence = max(0.0, min(1.0, (top.score / 100.0) * 0.75 + min(1.0, margin / 50.0) * 0.25))

        if top.score >= min_score and margin >= min_margin:
            return ExtractionResult(
                status="accepted",
                html=top.html,
                confidence=confidence,
                reason=f"Top deterministic candidate scored {top.score:.1f} with margin {margin:.1f}.",
                candidates=candidates,
                selected_index=top.index,
            )

        return ExtractionResult(
            status="uncertain",
            html=None,
            confidence=confidence,
            reason=f"Deterministic extraction was ambiguous: top score {top.score:.1f}, margin {margin:.1f}.",
            candidates=candidates,
        )

    def _rank_candidates(self, soup: BeautifulSoup) -> list[Candidate]:
        """Build and rank heuristic candidates, returning only the configured top results."""
        tags = set(self.h.get("candidate_tags", ["main", "article", "section", "form", "div"]))
        keywords = {str(k).lower(): float(v) for k, v in self.h.get("keyword_weights", {}).items()}
        out = []
        seen = set()

        for tag in soup.find_all(tags):
            if not isinstance(tag, Tag):
                continue
            if tag.name in {"script", "style", "noscript", "template", "svg"}:
                continue

            text = tag.get_text(" ", strip=True)
            text_len = len(text)
            if text_len < int(self.h.get("min_text_length", 80)):
                continue
            if text_len > int(self.h.get("max_text_length", 250000)):
                continue

            key = id(tag)
            if key in seen:
                continue
            seen.add(key)

            score = self._score(tag, text, keywords)
            if score <= 0:
                continue

            html = str(tag)
            out.append(Candidate(
                index=len(out),
                tag_name=tag.name,
                path=element_path(tag),
                score=score,
                text=text,
                html=html,
            ))

        out.sort(key=lambda c: c.score, reverse=True)
        top_k = int(self.h.get("top_k", 8))
        top = out[:top_k]

        # Re-number after sorting so LLM candidate references are stable.
        for i, candidate in enumerate(top):
            candidate.index = i
        return top

    def _score(self, tag: Tag, text: str, keywords: dict[str, float]) -> float:
        """Score an HTML element using semantic text, controls, and container structure."""
        attrs = " ".join([
            str(tag.get("id") or ""),
            " ".join(str(x) for x in (tag.get("class") or [])),
            str(tag.get("role") or ""),
            str(tag.get("aria-label") or ""),
            str(tag.get("data-testid") or ""),
        ]).lower()
        lower_text = text.lower()
        score = 0.0

        for keyword, weight in keywords.items():
            hits = min(3, len(re.findall(rf"\b{re.escape(keyword)}\b", lower_text)))
            if hits:
                score += weight * hits
            if keyword in attrs:
                score += float(self.h.get("class_id_bonus", 18))

        score += min(22.0, lower_text.count("?") * float(self.h.get("question_mark_weight", 2.0)))

        inputs = tag.find_all(["input", "textarea", "select"])
        if inputs:
            score += min(14.0, len(inputs) * float(self.h.get("input_bonus", 7)))

        controls = tag.find_all("input")
        radio_checkbox = sum(1 for x in controls if x.get("type") in {"radio", "checkbox"})
        score += min(24.0, radio_checkbox * float(self.h.get("radio_checkbox_bonus", 12)))

        score += min(10.0, len(tag.find_all("button")) * float(self.h.get("button_bonus", 2)))

        # Prefer useful middle-sized containers over the entire page shell.
        if tag.parent and isinstance(tag.parent, Tag):
            parent_len = len(tag.parent.get_text(" ", strip=True))
            if parent_len > 0 and text:
                ratio = len(text) / parent_len
                if 0.15 <= ratio <= 0.95:
                    score += 8.0
                elif ratio < 0.03:
                    score -= 8.0

        return max(0.0, min(100.0, score))
