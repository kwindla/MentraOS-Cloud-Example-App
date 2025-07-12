"""Layout type definitions for MentraOS displays."""

from enum import Enum


class ViewType(str, Enum):
    """Display view types."""
    MAIN = "main"
    OVERLAY = "overlay"


class LayoutType(str, Enum):
    """Available layout types."""
    TEXT_WALL = "text_wall"
    NOTIFICATION = "notification"
    MENU = "menu"
    CUSTOM = "custom"


class TextWallLayout:
    """Text wall layout configuration."""
    
    def __init__(self, text: str):
        self.layout_type = LayoutType.TEXT_WALL
        self.text = text
    
    def to_dict(self):
        """Convert to dictionary for message sending."""
        return {
            "layoutType": self.layout_type,
            "text": self.text
        }