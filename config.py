"""
Configuration management with validation.
"""
import os
from typing import Optional
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


class ConfigError(Exception):
    """Configuration error."""
    pass


def getenv_robust(name: str) -> Optional[str]:
    """
    Get environment variable, handling BOM issues from PowerShell-created .env files.
    """
    value = os.getenv(name)
    if value is None:
        # Try with BOM prefix (Windows PowerShell UTF-8 files)
        value = os.getenv("\ufeff" + name)
    return value


def getenv_required(name: str, description: str = None) -> str:
    """Get required environment variable or raise ConfigError."""
    value = getenv_robust(name)
    if not value:
        desc = description or name
        raise ConfigError(f"Required environment variable {name} is not set. {desc}")
    return value


def getenv_int(name: str, default: Optional[int] = None, min_value: Optional[int] = None, max_value: Optional[int] = None) -> int:
    """Get integer environment variable with validation."""
    value = getenv_robust(name)
    if value is None:
        if default is None:
            raise ConfigError(f"Required environment variable {name} is not set")
        return default
    
    try:
        int_value = int(value)
    except ValueError:
        raise ConfigError(f"Environment variable {name} must be an integer, got: {value!r}")
    
    if min_value is not None and int_value < min_value:
        raise ConfigError(f"Environment variable {name} must be >= {min_value}, got: {int_value}")
    if max_value is not None and int_value > max_value:
        raise ConfigError(f"Environment variable {name} must be <= {max_value}, got: {int_value}")
    
    return int_value


def getenv_bool(name: str, default: bool = False) -> bool:
    """Get boolean environment variable."""
    value = getenv_robust(name)
    if value is None:
        return default
    return value.lower() in ('1', 'true', 'yes', 'on')


def load_env_file():
    """Load .env file if it exists and python-dotenv is available."""
    if load_dotenv is not None and os.path.exists(".env"):
        load_dotenv(override=True)


def validate_config():
    """Validate required configuration is present."""
    load_env_file()
    
    errors = []
    
    # Required for Telegram API
    try:
        api_id = getenv_required("API_ID", "Get from https://my.telegram.org/apps")
        getenv_int("API_ID", min_value=1)  # Validate it's a positive integer
    except ConfigError as e:
        errors.append(str(e))
    
    try:
        api_hash = getenv_required("API_HASH", "Get from https://my.telegram.org/apps")
        if len(api_hash) != 32:
            errors.append("API_HASH should be 32 characters long")
    except ConfigError as e:
        errors.append(str(e))
    
    if errors:
        raise ConfigError("Configuration errors:\n" + "\n".join(f"  - {e}" for e in errors))
    
    return True
