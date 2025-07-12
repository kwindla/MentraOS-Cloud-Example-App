"""Logging configuration for MentraOS SDK."""

import sys
from typing import Optional
from loguru import logger


_configured = False


def get_logger(name: Optional[str] = None):
    """Get a configured logger instance."""
    global _configured
    
    if not _configured:
        # Remove default handler
        logger.remove()
        
        # Add custom handler with structured format
        logger.add(
            sys.stdout,
            format="[{time:YYYY-MM-DD HH:mm:ss.SSS Z}] {level}: {message}",
            level="TRACE",
            colorize=True,
            serialize=False
        )
        
        _configured = True
    
    if name:
        return logger.bind(service=name)
    return logger


def configure_logger(level: str = "INFO", format: Optional[str] = None):
    """Configure the logger with custom settings."""
    global _configured
    
    logger.remove()
    
    if format is None:
        format = "[{time:YYYY-MM-DD HH:mm:ss.SSS Z}] {level}: {message}"
    
    logger.add(
        sys.stdout,
        format=format,
        level=level.upper(),
        colorize=True,
        serialize=False
    )
    
    _configured = True