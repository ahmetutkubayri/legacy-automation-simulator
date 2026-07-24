"""Screen and control definitions for the simulated legacy system.

Every screen exposes only the controls that exist in that state, mirroring
how terminal-style enterprise applications behave. All identifiers are
invented for this project.

Screens and their controls
--------------------------
* ``main_menu`` — entry point; leads to the query screen.
* ``query`` — filter fields plus a run button; a query with no matches
  raises a modal popup instead of navigating.
* ``results`` — a paginated table with a fixed visible window, a row
  selector, and a button to open one record's detail screen.
* ``detail`` — a read-only record summary plus an editable status field.
* A modal ``popup`` overlay (not a screen) that blocks every other
  control until dismissed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ScreenId(StrEnum):
    MAIN_MENU = "main_menu"
    QUERY = "query"
    RESULTS = "results"
    DETAIL = "detail"


class ControlType(StrEnum):
    TEXT_FIELD = "text_field"
    BUTTON = "button"
    TABLE = "table"
    STATUS_TEXT = "status_text"
    POPUP_TEXT = "popup_text"


@dataclass(frozen=True)
class Control:
    control_id: str
    control_type: ControlType
    label: str


@dataclass(frozen=True)
class ScreenDefinition:
    screen_id: ScreenId
    title: str
    controls: tuple[Control, ...]

    def control_ids(self) -> tuple[str, ...]:
        return tuple(control.control_id for control in self.controls)

    def find(self, control_id: str) -> Control | None:
        for control in self.controls:
            if control.control_id == control_id:
                return control
        return None


SCREENS: dict[ScreenId, ScreenDefinition] = {
    ScreenId.MAIN_MENU: ScreenDefinition(
        screen_id=ScreenId.MAIN_MENU,
        title="Legacy System - Main Menu",
        controls=(
            Control("open_query", ControlType.BUTTON, "Open Record Query"),
            Control("menu_status", ControlType.STATUS_TEXT, "Menu Status"),
        ),
    ),
    ScreenId.QUERY: ScreenDefinition(
        screen_id=ScreenId.QUERY,
        title="Record Query",
        controls=(
            Control("external_ref_input", ControlType.TEXT_FIELD, "External Reference"),
            Control("period_input", ControlType.TEXT_FIELD, "Reporting Period"),
            Control("run_query", ControlType.BUTTON, "Run Query"),
            Control("back_to_menu", ControlType.BUTTON, "Back to Main Menu"),
            Control("query_status", ControlType.STATUS_TEXT, "Query Status"),
        ),
    ),
    ScreenId.RESULTS: ScreenDefinition(
        screen_id=ScreenId.RESULTS,
        title="Query Results",
        controls=(
            Control("results_table", ControlType.TABLE, "Result Rows"),
            Control("row_selector", ControlType.TEXT_FIELD, "Row Number"),
            Control("open_detail", ControlType.BUTTON, "Open Selected Record"),
            Control("back_to_query", ControlType.BUTTON, "Back to Query"),
            Control("results_status", ControlType.STATUS_TEXT, "Results Status"),
        ),
    ),
    ScreenId.DETAIL: ScreenDefinition(
        screen_id=ScreenId.DETAIL,
        title="Record Detail",
        controls=(
            Control("record_summary", ControlType.STATUS_TEXT, "Record Summary"),
            Control("status_input", ControlType.TEXT_FIELD, "New Status"),
            Control("apply_status", ControlType.BUTTON, "Apply Status Change"),
            Control("back_to_results", ControlType.BUTTON, "Back to Results"),
            Control("detail_status", ControlType.STATUS_TEXT, "Detail Status"),
        ),
    ),
}

#: The modal popup overlay; while open, only these controls are reachable.
POPUP_SCREEN = ScreenDefinition(
    screen_id=ScreenId.MAIN_MENU,  # overlay; the underlying screen is retained
    title="Message",
    controls=(
        Control("popup_message", ControlType.POPUP_TEXT, "Message Text"),
        Control("dismiss", ControlType.BUTTON, "Dismiss"),
    ),
)
