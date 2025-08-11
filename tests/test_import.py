"""Tests for the Vitrea package."""

import logging
logging.basicConfig(level=logging.DEBUG)

def test_import():
    import src.vitrea_client.client as vitrea_client
    assert vitrea_client is not None

