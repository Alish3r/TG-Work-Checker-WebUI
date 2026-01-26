#!/usr/bin/env python3
"""
Unit tests for scraping functions.
"""
import unittest
from unittest.mock import patch, MagicMock

from scrape_telegram import parse_chat_identifier, _topic_id_norm


class TestScrapeTelegram(unittest.TestCase):
    """Test scraping utility functions."""
    
    def test_parse_chat_identifier_username(self):
        """Test parsing plain username."""
        identifier, topic_id = parse_chat_identifier("cyprusithr")
        self.assertEqual(identifier, "cyprusithr")
        self.assertIsNone(topic_id)
    
    def test_parse_chat_identifier_url(self):
        """Test parsing t.me URL."""
        identifier, topic_id = parse_chat_identifier("https://t.me/cyprusithr/46679")
        self.assertEqual(identifier, "cyprusithr")
        self.assertEqual(topic_id, 46679)
    
    def test_parse_chat_identifier_with_topic_env(self):
        """Test parsing with topic_id from env."""
        identifier, topic_id = parse_chat_identifier("cyprusithr", topic_id_env=123)
        self.assertEqual(identifier, "cyprusithr")
        self.assertEqual(topic_id, 123)
    
    def test_topic_id_norm(self):
        """Test topic ID normalization."""
        self.assertEqual(_topic_id_norm(123), 123)
        self.assertEqual(_topic_id_norm(None), -1)
        self.assertEqual(_topic_id_norm(0), 0)


if __name__ == "__main__":
    unittest.main()
