"""Isolated workflow tests declare capability prerequisites without inventing live proof."""
from unittest.mock import patch


def activate(engine, *args, **kwargs):
    with patch.object(engine, 'activation_blockers', return_value=()):
        return engine.activate(*args, **kwargs)
