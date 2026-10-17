"""VRChat OSC chatbox output, independent of routing and live playback ACKs."""
from __future__ import annotations

from pythonosc.udp_client import SimpleUDPClient

CHATBOX_INPUT = "/chatbox/input"
CHATBOX_TYPING = "/chatbox/typing"


class OscDelivery:
    """Fire-and-forget UDP sink sharing the hub's acceptance points with history.

    Every accepted message (TUI submission or an inbound editor message) reaches
    OSC at most once; live playback feedback never triggers a second send.
    """

    def __init__(self, hub):
        self.hub = hub
        self.error = ""
        self.typing = False

    @property
    def enabled(self):
        return bool(self.hub.settings["osc.enable"])

    def publish(self, entries):
        """Send one chatbox input for an accepted message; empty text sends nothing."""
        if not self.enabled:
            return False
        return self.send_text("\n".join(entry.message.strip() for entry in entries if entry.message.strip()))

    def send_text(self, text):
        text = text.strip()
        if not self.enabled or not text:
            return False
        return self._send(CHATBOX_INPUT, [text, True])

    def send_typing(self, active):
        """Mirror the input hint for OSC; disabling is attempted only after a hint was shown."""
        active = bool(active)
        if self.typing == active or (active and not self.enabled):
            return False
        if not self._send(CHATBOX_TYPING, active):
            return False
        self.typing = active
        return True

    def _send(self, address, value):
        try:
            SimpleUDPClient(self.hub.settings["osc.host"], self.hub.settings["osc.port"]).send_message(address, value)
            self.error = ""
            return True
        except Exception as exc:
            self.error = str(exc)
            self.hub.log(f"OSC 发送失败：{exc}", "warn")
            return False
