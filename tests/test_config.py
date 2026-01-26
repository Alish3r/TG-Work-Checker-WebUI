#!/usr/bin/env python3
"""
Unit tests for configuration management.
"""
import os
import unittest
from unittest.mock import patch

from config import getenv_robust, getenv_required, getenv_int, getenv_bool, ConfigError


class TestConfig(unittest.TestCase):
    """Test configuration functions."""
    
    def setUp(self):
        """Set up test environment."""
        # Clear environment
        self.old_env = dict(os.environ)
        os.environ.clear()
    
    def tearDown(self):
        """Restore environment."""
        os.environ.clear()
        os.environ.update(self.old_env)
    
    def test_getenv_robust(self):
        """Test robust environment variable retrieval."""
        os.environ["TEST_VAR"] = "test_value"
        self.assertEqual(getenv_robust("TEST_VAR"), "test_value")
        self.assertIsNone(getenv_robust("NONEXISTENT"))
    
    def test_getenv_required(self):
        """Test required environment variable."""
        os.environ["REQUIRED_VAR"] = "required_value"
        self.assertEqual(getenv_required("REQUIRED_VAR"), "required_value")
        
        with self.assertRaises(ConfigError):
            getenv_required("MISSING_VAR")
    
    def test_getenv_int(self):
        """Test integer environment variable."""
        os.environ["INT_VAR"] = "42"
        self.assertEqual(getenv_int("INT_VAR"), 42)
        
        os.environ["INT_VAR"] = "invalid"
        with self.assertRaises(ConfigError):
            getenv_int("INT_VAR")
        
        self.assertEqual(getenv_int("MISSING", default=10), 10)
        
        with self.assertRaises(ConfigError):
            getenv_int("MISSING", min_value=1)
    
    def test_getenv_bool(self):
        """Test boolean environment variable."""
        os.environ["BOOL_VAR"] = "1"
        self.assertTrue(getenv_bool("BOOL_VAR"))
        
        os.environ["BOOL_VAR"] = "true"
        self.assertTrue(getenv_bool("BOOL_VAR"))
        
        os.environ["BOOL_VAR"] = "false"
        self.assertFalse(getenv_bool("BOOL_VAR"))
        
        self.assertFalse(getenv_bool("MISSING"))
        self.assertTrue(getenv_bool("MISSING", default=True))


if __name__ == "__main__":
    unittest.main()
