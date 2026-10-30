"""Scrollable setup diagnostics without resizing the welcome form."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, RichLog, Static


class SetupDetailsScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close", "返回", priority=True)]
    DEFAULT_CSS = """
    SetupDetailsScreen { align: center middle; background: $background 80%; }
    #setup-details-dialog {
        width: 90%; max-width: 100; height: 85%; min-height: 10;
        padding: 1; border: round $primary; background: $surface;
    }
    #setup-details-title { height: 1; margin-bottom: 1; color: $primary; text-style: bold; }
    #setup-details-log {
        height: 1fr; min-height: 1; overflow: auto auto;
        scrollbar-size: 1 1; scrollbar-gutter: stable; background: $surface;
    }
    #close-setup-details { height: 1; margin-top: 1; }
    """

    def __init__(self, title: str, content: str) -> None:
        super().__init__()
        self.detail_title = title
        self.content = content
        self._ready = False

    def compose(self) -> ComposeResult:
        with Vertical(id="setup-details-dialog"):
            yield Static(self.detail_title, id="setup-details-title", markup=False)
            yield RichLog(
                id="setup-details-log", min_width=1, wrap=True, markup=False, auto_scroll=False, max_lines=2000
            )
            yield Button("返回 · Esc", id="close-setup-details", classes="compact")

    def on_mount(self) -> None:
        self.call_after_refresh(self._show_details)

    def _show_details(self) -> None:
        log = self.query_one("#setup-details-log", RichLog)
        self.query_one("#setup-details-title", Static).update(self.detail_title)
        log.write(self.content)
        self._ready = True
        log.focus(scroll_visible=False)

    def set_progress(self, heading: str) -> None:
        self.detail_title = heading
        if self._ready:
            self.query_one("#setup-details-title", Static).update(heading)

    def append_content(self, content: str, *, title: str | None = None) -> None:
        if title:
            self.set_progress(title)
        if not self._ready:
            self.content += "\n" + content
            return
        log = self.query_one("#setup-details-log", RichLog)
        follow = log.is_vertical_scroll_end
        log.write(content, scroll_end=follow)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-setup-details":
            event.stop()
            self.dismiss()

    def action_close(self) -> None:
        self.dismiss()
