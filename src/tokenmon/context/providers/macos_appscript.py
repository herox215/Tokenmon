"""AppleScript-based providers for scriptable macOS apps.

Currently covers Safari (URL + title + innerText). Chrome / Arc / Notes
will live alongside if and when they're added.

Why osascript-via-subprocess instead of PyObjC ScriptingBridge?
- Subprocess can be killed by timeout if the target app hangs; a
  ScriptingBridge call from inside our process would block the chat
  thread.
- AppleScript itself is one syntax across all scriptable apps — adding
  Chrome later is just another script string, not new bindings.
"""
from __future__ import annotations

import json
import logging

from ..snapshot import ContextSnapshot
from .base import run_subprocess

log = logging.getLogger("tokenmon.context.providers.macos_appscript")


# AppleScript snippet that reads the front Safari tab and returns
# {"url":..., "title":..., "text":...} as JSON. JSON because we'd
# otherwise lose newlines from page text. The escape helpers handle
# embedded quotes and newlines in URL/title/body — fragile but stable.
_SAFARI_SCRIPT = r"""
on replace_chars(t, search, replace)
    set AppleScript's text item delimiters to search
    set parts to text items of t
    set AppleScript's text item delimiters to replace
    set t to parts as text
    set AppleScript's text item delimiters to ""
    return t
end replace_chars

on escape_for_json(s)
    set s to my replace_chars(s, "\\", "\\\\")
    set s to my replace_chars(s, "\"", "\\\"")
    set s to my replace_chars(s, return, "\\n")
    set s to my replace_chars(s, linefeed, "\\n")
    set s to my replace_chars(s, tab, "\\t")
    return s
end escape_for_json

tell application "Safari"
    if (count of windows) is 0 then return "{}"
    set theWindow to front window
    set theTab to current tab of theWindow
    set theURL to URL of theTab
    set theTitle to name of theTab
    try
        set theBody to (do JavaScript "document.body.innerText" in theTab)
    on error
        set theBody to ""
    end try
end tell
return "{\"url\":\"" & my escape_for_json(theURL) & ¬
    "\",\"title\":\"" & my escape_for_json(theTitle) & ¬
    "\",\"text\":\"" & my escape_for_json(theBody) & "\"}"
"""


class SafariProvider:
    name = "macos_appscript_safari"

    def supports(self, app_id: str) -> bool:
        return app_id == "com.apple.Safari"

    def snapshot(self, app_id: str, pid: int) -> ContextSnapshot | None:
        result = run_subprocess(["osascript", "-e", _SAFARI_SCRIPT], timeout=2.0)
        if result is None:
            return None
        rc, stdout, stderr = result
        if rc != 0:
            # Most likely cause: Automation permission not granted yet.
            # macOS prints an explanatory message to stderr.
            log.info("Safari osascript rc=%s stderr=%s", rc, stderr.strip())
            return None
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            log.warning("Safari script returned non-JSON: %r", stdout[:200])
            return None
        if not data:
            return None
        return ContextSnapshot(
            app_name="Safari",
            app_id=app_id,
            kind="browser",
            window_title=data.get("title") or None,
            url=data.get("url") or None,
            text=(data.get("text") or None),
            source=self.name,
        )
