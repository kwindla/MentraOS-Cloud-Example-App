"""Subscription management for MentraOS events."""

from typing import Set, List
from enum import Enum


class Subscription(str, Enum):
    """Available subscription types."""
    AUDIO_CHUNK = "audio_chunk"
    TRANSCRIPTION = "transcription:en-US"
    TRANSCRIPTION_ES = "transcription:es-ES"
    TRANSCRIPTION_FR = "transcription:fr-FR"
    TRANSCRIPTION_DE = "transcription:de-DE"
    TRANSCRIPTION_IT = "transcription:it-IT"
    TRANSCRIPTION_PT = "transcription:pt-PT"
    TRANSCRIPTION_JA = "transcription:ja-JP"
    TRANSCRIPTION_KO = "transcription:ko-KR"
    TRANSCRIPTION_ZH = "transcription:zh-CN"
    TRANSLATION = "translation"
    GLASSES_BATTERY_UPDATE = "glasses_battery_update"
    CAMERA_FRAME = "camera_frame"
    LOCATION_UPDATE = "location_update"
    
    @staticmethod
    def transcription(language: str = "en-US") -> str:
        """Create transcription subscription for specific language."""
        return f"transcription:{language}"


class SubscriptionManager:
    """Manages event subscriptions."""
    
    def __init__(self):
        self._subscriptions: Set[str] = set()
    
    def add(self, subscription: str) -> bool:
        """Add a subscription. Returns True if newly added."""
        if subscription not in self._subscriptions:
            self._subscriptions.add(subscription)
            return True
        return False
    
    def remove(self, subscription: str) -> bool:
        """Remove a subscription. Returns True if removed."""
        if subscription in self._subscriptions:
            self._subscriptions.remove(subscription)
            return True
        return False
    
    def clear(self) -> None:
        """Clear all subscriptions."""
        self._subscriptions.clear()
    
    def has(self, subscription: str) -> bool:
        """Check if subscription exists."""
        return subscription in self._subscriptions
    
    def get_all(self) -> List[str]:
        """Get all current subscriptions."""
        return list(self._subscriptions)
    
    def update(self, subscriptions: List[str]) -> None:
        """Update subscriptions to match the provided list."""
        self._subscriptions = set(subscriptions)
    
    def __len__(self) -> int:
        """Get number of subscriptions."""
        return len(self._subscriptions)
    
    def __contains__(self, subscription: str) -> bool:
        """Check if subscription exists using 'in' operator."""
        return subscription in self._subscriptions
    
    def __iter__(self):
        """Iterate over subscriptions."""
        return iter(self._subscriptions)