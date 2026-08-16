"""PLAIN_SUBMIT_RX accepts only whole-label submit phrases — this is what
keeps a JS-wired plain button usable without resurrecting the decoy-button
bug ('Search jobs', cookie banners)."""
from common import PLAIN_SUBMIT_RX


def test_accepts_submit_phrases():
    for label in ("Submit", "Submit application", "Apply", "Apply now",
                  "Send application", "  Submit  ", "SUBMIT NOW"):
        assert PLAIN_SUBMIT_RX.match(label.strip()), label


def test_rejects_decoys():
    for label in ("Search jobs", "Apply filters", "Submit feedback",
                  "Apply to see more results", "Send us a message",
                  "Accept cookies", "Sign in"):
        assert not PLAIN_SUBMIT_RX.match(label.strip()), label
