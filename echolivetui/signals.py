"""Keep process signals consistent with the live Ctrl+C setting."""
import signal
import threading


class InterruptPolicy:
    def __init__(self, settings, exit_callback):
        self.settings = settings
        self.exit_callback = exit_callback
        self.previous = {}

    def handle(self, signum, frame=None):
        if signum == signal.SIGINT and self.settings["input.interrupt_guard"]:
            return
        self.exit_callback()

    def install(self):
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                self.previous[signum] = signal.getsignal(signum)
                signal.signal(signum, self.handle)
        return self

    def restore(self):
        for signum, handler in self.previous.items():
            signal.signal(signum, handler)
        self.previous.clear()
