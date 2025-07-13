"""Event type definitions for MentraOS SDK."""

from dataclasses import dataclass
from typing import Optional, Any, Dict
from datetime import datetime
from enum import Enum


class EventType(str, Enum):
    """Available event types."""
    AUDIO_CHUNK = "audio_chunk"
    TRANSCRIPTION = "transcription"
    TRANSLATION = "translation" 
    BATTERY_UPDATE = "glasses_battery_update"
    CONNECTION_STATE = "connection_state"
    SESSION_STATE = "session_state"
    APP_STOPPED = "app_stopped"
    ERROR = "error"
    CUSTOM = "custom"
    AUDIO_PLAY_RESPONSE = "audio_play_response"


@dataclass
class Event:
    """Base event class."""
    type: EventType
    timestamp: datetime
    session_id: str
    data: Dict[str, Any]
    
    @classmethod
    def from_message(cls, message: Dict[str, Any], session_id: str) -> "Event":
        """Create event from WebSocket message."""
        event_type = EventType(message.get("type", EventType.CUSTOM))
        timestamp = datetime.fromisoformat(
            message.get("timestamp", datetime.utcnow().isoformat()).replace("Z", "+00:00")
        )
        
        return cls(
            type=event_type,
            timestamp=timestamp,
            session_id=session_id,
            data=message
        )


@dataclass
class AudioChunkEvent(Event):
    """Audio chunk event containing raw audio data."""
    audio_data: bytes
    sample_rate: int = 16000
    channels: int = 1
    
    @classmethod
    def from_message(cls, message: Dict[str, Any], session_id: str) -> "AudioChunkEvent":
        """Create audio chunk event from message."""
        base = Event.from_message(message, session_id)
        
        # Extract audio data from binary message
        audio_data = message.get("data", b"")
        if isinstance(audio_data, dict) and audio_data.get("type") == "binary":
            audio_data = audio_data.get("data", b"")
        
        return cls(
            type=base.type,
            timestamp=base.timestamp,
            session_id=base.session_id,
            data=base.data,
            audio_data=audio_data,
            sample_rate=message.get("sampleRate", 16000),
            channels=message.get("channels", 1)
        )


@dataclass
class TranscriptionEvent(Event):
    """Transcription event from speech-to-text."""
    text: str
    is_final: bool
    language: str
    confidence: Optional[float] = None
    
    @classmethod
    def from_message(cls, message: Dict[str, Any], session_id: str) -> "TranscriptionEvent":
        """Create transcription event from message."""
        base = Event.from_message(message, session_id)
        
        return cls(
            type=base.type,
            timestamp=base.timestamp,
            session_id=base.session_id,
            data=base.data,
            text=message.get("text", ""),
            is_final=message.get("isFinal", False),
            language=message.get("language", "en-US"),
            confidence=message.get("confidence")
        )


@dataclass 
class TranslationEvent(Event):
    """Translation event."""
    original_text: str
    translated_text: str
    source_language: str
    target_language: str
    
    @classmethod
    def from_message(cls, message: Dict[str, Any], session_id: str) -> "TranslationEvent":
        """Create translation event from message."""
        base = Event.from_message(message, session_id)
        
        return cls(
            type=base.type,
            timestamp=base.timestamp,
            session_id=base.session_id,
            data=base.data,
            original_text=message.get("originalText", ""),
            translated_text=message.get("translatedText", ""),
            source_language=message.get("sourceLanguage", ""),
            target_language=message.get("targetLanguage", "")
        )


@dataclass
class BatteryUpdateEvent(Event):
    """Battery status update event."""
    level: int  # 0-100
    is_charging: bool
    
    @classmethod
    def from_message(cls, message: Dict[str, Any], session_id: str) -> "BatteryUpdateEvent":
        """Create battery update event from message."""
        base = Event.from_message(message, session_id)
        
        return cls(
            type=base.type,
            timestamp=base.timestamp,
            session_id=base.session_id,
            data=base.data,
            level=message.get("level", 0),
            is_charging=message.get("isCharging", False)
        )


@dataclass
class AudioPlayResponseEvent(Event):
    """Audio play response event."""
    request_id: str
    success: bool
    error: Optional[str] = None
    
    @classmethod
    def from_message(cls, message: Dict[str, Any], session_id: str) -> "AudioPlayResponseEvent":
        """Create audio play response event from message."""
        base = Event.from_message(message, session_id)
        
        return cls(
            type=base.type,
            timestamp=base.timestamp,
            session_id=base.session_id,
            data=base.data,
            request_id=message.get("requestId", ""),
            success=message.get("success", False),
            error=message.get("error")
        )