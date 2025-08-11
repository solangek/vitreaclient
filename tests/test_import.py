"""Tests for the Vitrea package."""

import logging
logging.basicConfig(level=logging.DEBUG)

def test_import():
    import src.vitreaclient.client as vitreaclient
    assert vitreaclient is not None

