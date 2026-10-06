from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "static" / "index.html"
UX_CSS = ROOT / "static" / "css" / "ux.css"


class _IdAttributeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.attributes: dict[str, dict[str, str | None]] = {}

    def handle_starttag(self, _tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        element_id = values.get("id")
        if element_id:
            self.attributes[element_id] = values


def _attributes_by_id() -> dict[str, dict[str, str | None]]:
    parser = _IdAttributeParser()
    parser.feed(INDEX.read_text(encoding="utf-8"))
    return parser.attributes


def test_empty_workspace_has_a_direct_file_action() -> None:
    attributes = _attributes_by_id()

    assert attributes["emptyState"]["role"] == "region"
    assert attributes["emptyState"]["aria-labelledby"] == "emptyStateTitle"
    assert attributes["emptyUploadBtn"]["type"] == "button"


def test_initial_transport_controls_are_not_actionable() -> None:
    attributes = _attributes_by_id()

    for element_id in ("t-stop", "t-play", "t-loop", "t-export-btn"):
        assert "disabled" in attributes[element_id]
        assert attributes[element_id]["aria-disabled"] == "true"

    scrub = attributes["footer-scrub"]
    assert scrub["aria-disabled"] == "true"
    assert scrub["tabindex"] == "-1"
    assert scrub["aria-valuemin"] == "0"
    assert scrub["aria-valuemax"] == "100"
    assert scrub["aria-valuenow"] == "0"


def test_modal_triggers_reference_their_dialogs() -> None:
    attributes = _attributes_by_id()

    assert attributes["aboutBtn"]["aria-controls"] == "aboutDialog"
    assert attributes["friendsBtn"]["aria-controls"] == "friendsDialog"
    assert attributes["logsBtn"]["aria-controls"] == "logsDialog"


def test_ui_styles_cover_keyboard_focus_and_mobile_navigation() -> None:
    css = UX_CSS.read_text(encoding="utf-8")

    assert ".daw :where(button, a, input, select, [role=\"button\"], [role=\"slider\"]):focus-visible" in css
    assert ".daw-empty-state" in css
    assert ".daw.no-track .daw-empty-state" in css
    assert ".daw .sidebar-rail-mobile" in css
