"""Environment features, independent of individual endpoint capabilities."""
from enum import IntFlag


class Capabilities(IntFlag):
    NONE = 0
    HAS_ECHO_LIVE = 1 << 0
