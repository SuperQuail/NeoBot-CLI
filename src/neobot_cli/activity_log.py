"""Keep hidden-page output as text until it has a real rendering width."""

from collections import deque

from textual.widgets import RichLog


class ActivityLog(RichLog):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._pending: deque[str] = deque(maxlen=1000)
        self._following = True
        self._reading_y = 0.0
        self._visible_ready = False
        self._dropped = False

    def on_hide(self) -> None:
        self.pause_output()

    def pause_output(self) -> None:
        if self._visible_ready:
            self._following = self.is_vertical_scroll_end
            self._reading_y = self.scroll_y
        self._visible_ready = False

    def on_show(self) -> None:
        self.call_after_refresh(self._resume_output)

    def _resume_output(self) -> None:
        if self.is_on_screen and self.content_size.width > 0:
            self._visible_ready = True
            self._flush_output()

    def append(self, content: str) -> None:
        if self._visible_ready and self.is_on_screen:
            self._following = self.is_vertical_scroll_end
            self._reading_y = self.scroll_y
        self._dropped |= len(self._pending) == self._pending.maxlen
        self._pending.append(content)
        if self._visible_ready and self.is_on_screen and self.content_size.width > 0:
            self._flush_output()

    def _flush_output(self) -> None:
        if self._dropped:
            self.write("（离页期间输出较多，已省略部分中间内容）", scroll_end=False)
            self._dropped = False
        while self._pending:
            self.write(self._pending.popleft(), scroll_end=False)
        if self._following:
            self.scroll_end(animate=False, immediate=True)
        else:
            self.scroll_to(y=self._reading_y, animate=False, immediate=True)
