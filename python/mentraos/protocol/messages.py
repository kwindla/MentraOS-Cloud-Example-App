"""WebSocket protocol message definitions for MentraOS."""

from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List, Optional, Union
from datetime import datetime
from enum import Enum


class MessageType(str, Enum):
    """WebSocket message types."""
    # Connection
    TPA_CONNECTION_INIT = "tpa_connection_init"
    TPA_CONNECTION_ACK = "tpa_connection_ack"
    
    # Subscriptions
    SUBSCRIPTION_UPDATE = "subscription_update"
    
    # Display
    DISPLAY_EVENT = "display_event"
    
    # Data streams
    DATA_STREAM = "data_stream"
    
    # Audio
    AUDIO_CHUNK = "audio_chunk"
    
    # Transcription
    TRANSCRIPTION = "transcription"
    TRANSLATION = "translation"
    
    # Device
    GLASSES_BATTERY_UPDATE = "glasses_battery_update"
    
    # Errors
    ERROR = "error"


class LayoutType(str, Enum):
    """Display layout types."""
    TEXT_WALL = "text_wall"
    NOTIFICATION = "notification"
    MENU = "menu"


class ViewType(str, Enum):
    """Display view types."""
    MAIN = "main"
    OVERLAY = "overlay"


@dataclass
class Message:
    """Base message class."""
    type: str = ""
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert message to dictionary for sending."""
        data = asdict(self)
        # Remove None values
        return {k: v for k, v in data.items() if v is not None}


@dataclass
class ConnectionInitMessage:
    """Connection initialization message."""
    sessionId: str
    packageName: str
    apiKey: str
    type: str = field(default=MessageType.TPA_CONNECTION_INIT, init=False)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert message to dictionary for sending."""
        data = asdict(self)
        return {k: v for k, v in data.items() if v is not None}


@dataclass
class ConnectionAckMessage:
    """Connection acknowledgment message."""
    sessionId: str
    type: str = field(default=MessageType.TPA_CONNECTION_ACK, init=False)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    success: bool = True
    mentraosSettings: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert message to dictionary for sending."""
        data = asdict(self)
        return {k: v for k, v in data.items() if v is not None}


@dataclass
class SubscriptionUpdateMessage:
    """Subscription update message."""
    packageName: str
    subscriptions: List[str]
    sessionId: str
    type: str = field(default=MessageType.SUBSCRIPTION_UPDATE, init=False)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert message to dictionary for sending."""
        data = asdict(self)
        return {k: v for k, v in data.items() if v is not None}


@dataclass
class TextWallLayout:
    """Text wall layout configuration."""
    text: str
    layoutType: str = LayoutType.TEXT_WALL


@dataclass
class DisplayEventMessage:
    """Display event message."""
    sessionId: str
    packageName: str
    type: str = field(default=MessageType.DISPLAY_EVENT, init=False)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    view: str = ViewType.MAIN
    layout: Union[TextWallLayout, Dict[str, Any]] = field(default_factory=dict)
    durationMs: Optional[int] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict, handling layout conversion."""
        data = asdict(self)
        if isinstance(data["layout"], TextWallLayout):
            data["layout"] = asdict(data["layout"])
        return {k: v for k, v in data.items() if v is not None}


@dataclass
class AudioChunkMessage:
    """Audio chunk message (incoming)."""
    sessionId: str
    type: str = field(default=MessageType.AUDIO_CHUNK, init=False)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    # Audio data is sent as binary, not in JSON
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert message to dictionary for sending."""
        data = asdict(self)
        return {k: v for k, v in data.items() if v is not None}


@dataclass
class TranscriptionMessage:
    """Transcription message (incoming)."""
    sessionId: str
    text: str
    isFinal: bool
    type: str = field(default=MessageType.TRANSCRIPTION, init=False)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    language: str = "en-US"
    confidence: Optional[float] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert message to dictionary for sending."""
        data = asdict(self)
        return {k: v for k, v in data.items() if v is not None}


@dataclass
class TranslationMessage:
    """Translation message (incoming)."""
    sessionId: str
    originalText: str
    translatedText: str
    sourceLanguage: str
    targetLanguage: str
    type: str = field(default=MessageType.TRANSLATION, init=False)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert message to dictionary for sending."""
        data = asdict(self)
        return {k: v for k, v in data.items() if v is not None}


@dataclass
class BatteryUpdateMessage:
    """Battery update message (incoming)."""
    sessionId: str
    level: int  # 0-100
    isCharging: bool
    type: str = field(default=MessageType.GLASSES_BATTERY_UPDATE, init=False)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert message to dictionary for sending."""
        data = asdict(self)
        return {k: v for k, v in data.items() if v is not None}


@dataclass
class ErrorMessage:
    """Error message."""
    error: str
    type: str = field(default=MessageType.ERROR, init=False)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    sessionId: Optional[str] = None
    code: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert message to dictionary for sending."""
        data = asdict(self)
        return {k: v for k, v in data.items() if v is not None}


def parse_message(data: Dict[str, Any]) -> Message:
    """Parse a message from dictionary data."""
    msg_type = data.get("type", "")
    
    # Map message types to classes
    message_classes = {
        MessageType.TPA_CONNECTION_INIT: ConnectionInitMessage,
        MessageType.TPA_CONNECTION_ACK: ConnectionAckMessage,
        MessageType.SUBSCRIPTION_UPDATE: SubscriptionUpdateMessage,
        MessageType.DISPLAY_EVENT: DisplayEventMessage,
        MessageType.AUDIO_CHUNK: AudioChunkMessage,
        MessageType.TRANSCRIPTION: TranscriptionMessage,
        MessageType.TRANSLATION: TranslationMessage,
        MessageType.GLASSES_BATTERY_UPDATE: BatteryUpdateMessage,
        MessageType.ERROR: ErrorMessage,
    }
    
    message_class = message_classes.get(msg_type, Message)
    
    # Filter data to only include fields that exist in the dataclass
    if message_class != Message:
        # Get field names from dataclass
        field_names = {f.name for f in message_class.__dataclass_fields__.values()}
        filtered_data = {k: v for k, v in data.items() if k in field_names}
        return message_class(**filtered_data)
    
    return Message(**data)