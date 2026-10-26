"""OpenCode has no engine and no recorded stream format here (gates.md G33): every input is refused."""
from engine.normalize import NormalizeError


def normalize(text, scenario_id, agent=None):
    raise NormalizeError("opencode: engine not available (gates.md G33); no recorded stream format")
