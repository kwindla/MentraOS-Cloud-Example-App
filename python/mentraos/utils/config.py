"""Configuration management for MentraOS SDK."""

import os
from typing import Optional
from dataclasses import dataclass


@dataclass
class Config:
    """Configuration for MentraOS application."""
    
    package_name: str
    api_key: str
    port: int = 3000
    websocket_url: str = "wss://prod.augmentos.cloud/app-ws"
    debug: bool = False
    
    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        package_name = os.getenv("PACKAGE_NAME")
        if not package_name:
            raise ValueError("PACKAGE_NAME environment variable is required")
            
        api_key = os.getenv("MENTRAOS_API_KEY")
        if not api_key:
            raise ValueError("MENTRAOS_API_KEY environment variable is required")
            
        port = int(os.getenv("PORT", "3000"))
        websocket_url = os.getenv("MENTRAOS_WS_URL", "wss://prod.augmentos.cloud/app-ws")
        debug = os.getenv("DEBUG", "").lower() in ("true", "1", "yes")
        
        return cls(
            package_name=package_name,
            api_key=api_key,
            port=port,
            websocket_url=websocket_url,
            debug=debug
        )
    
    def validate(self) -> None:
        """Validate the configuration."""
        if not self.package_name:
            raise ValueError("package_name cannot be empty")
        if not self.api_key:
            raise ValueError("api_key cannot be empty")
        if self.port < 1 or self.port > 65535:
            raise ValueError("port must be between 1 and 65535")
        if not self.websocket_url.startswith(("ws://", "wss://")):
            raise ValueError("websocket_url must start with ws:// or wss://")


_default_config: Optional[Config] = None


def get_config() -> Config:
    """Get the default configuration instance."""
    global _default_config
    if _default_config is None:
        _default_config = Config.from_env()
        _default_config.validate()
    return _default_config


def set_config(config: Config) -> None:
    """Set the default configuration instance."""
    global _default_config
    config.validate()
    _default_config = config