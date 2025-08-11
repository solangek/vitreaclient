"""Tests for the Vitrea package."""
import asyncio
import pytest

import logging
logging.basicConfig(level=logging.DEBUG)

def test_import():
    import vitrea_client
    assert vitrea_client is not None

