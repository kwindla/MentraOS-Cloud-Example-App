"""MentraOS Frame Serializer for Pipecat integration."""

import base64
import json
from typing import Any, Dict, Optional
from dataclasses import dataclass

from pipecat.frames.frames import (
    AudioRawFrame,
    CancelFrame, 
    EndFrame,
    Frame,
    StartInterruptionFrame,
    TransportMessageFrame,
    TextFrame,
    TranscriptionFrame,
    SystemFrame
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.transports.base_transport import TransportParams
from loguru import logger


@dataclass
class MentraOSInputParams(TransportParams):
    """Input parameters for MentraOS transport."""
    session_id: str
    sample_rate: int = 16000
    num_channels: int = 1


@dataclass  
class MentraOSOutputParams(TransportParams):
    """Output parameters for MentraOS transport."""
    pass


class MentraOSFrameSerializer(FrameSerializer):
    """Serializer for MentraOS WebSocket messages.
    
    Handles conversion between MentraOS message format and Pipecat frames.
    Audio format: 16kHz, mono, 16-bit PCM (little-endian signed)
    """
    
    def __init__(self, params: MentraOSInputParams):
        super().__init__()
        self._params = params
        
    async def serialize(self, frame: Frame) -> str | bytes | None:
        """Convert Pipecat frame to MentraOS WebSocket message.
        
        Args:
            frame: The Pipecat frame to serialize
            
        Returns:
            JSON string for the WebSocket or None if frame not handled
        """
        if isinstance(frame, TextFrame):
            # Send text to be displayed on glasses
            message = {
                "type": "display_event",
                "sessionId": self._params.session_id,
                "view": "main",
                "layout": {
                    "layoutType": "text_wall",
                    "text": frame.text
                }
            }
            return json.dumps(message)
            
        elif isinstance(frame, TranscriptionFrame):
            # Send transcription result to glasses
            message = {
                "type": "display_event", 
                "sessionId": self._params.session_id,
                "view": "main",
                "layout": {
                    "layoutType": "text_wall",
                    "text": f"Heard: {frame.text}"
                }
            }
            return json.dumps(message)
            
        elif isinstance(frame, StartInterruptionFrame):
            # Could send a visual indicator of interruption
            logger.debug("Interruption started")
            return None
            
        elif isinstance(frame, TransportMessageFrame):
            # Pass through transport control messages
            return frame.message
            
        elif isinstance(frame, (CancelFrame, EndFrame)):
            # Handle end of stream
            logger.debug("Stream ending")
            return None
            
        # We don't send audio back to MentraOS in this basic implementation
        # Future: Could support TTS audio playback on glasses
        
        return None
        
    async def deserialize(self, data: str | bytes) -> Frame | None:
        """Convert MentraOS WebSocket message to Pipecat frame.
        
        Args:
            data: The WebSocket message (JSON string or binary audio)
            
        Returns:
            Pipecat frame or None if message not handled
        """
        # Handle binary audio data
        if isinstance(data, bytes):
            # MentraOS sends raw PCM audio: 16kHz, mono, 16-bit signed little-endian
            # This matches Pipecat's expected format
            return AudioRawFrame(
                audio=data,
                sample_rate=self._params.sample_rate,
                num_channels=self._params.num_channels
            )
            
        # Handle JSON messages
        try:
            message = json.loads(data)
            msg_type = message.get("type", "")
            
            if msg_type == "audio_chunk":
                # Audio might come as base64 in JSON (alternative format)
                audio_data = message.get("data", "")
                if isinstance(audio_data, str):
                    audio_bytes = base64.b64decode(audio_data)
                else:
                    audio_bytes = audio_data
                    
                return AudioRawFrame(
                    audio=audio_bytes,
                    sample_rate=self._params.sample_rate, 
                    num_channels=self._params.num_channels
                )
                
            elif msg_type == "transcription":
                # MentraOS might send its own transcriptions
                text = message.get("text", "")
                is_final = message.get("isFinal", False)
                
                return TranscriptionFrame(
                    text=text,
                    user_id=self._params.session_id,
                    timestamp=message.get("timestamp"),
                    final=is_final
                )
                
            else:
                # Other message types as transport messages
                return TransportMessageFrame(
                    message=data,
                    direction=FrameDirection.DOWNSTREAM
                )
                
        except json.JSONDecodeError:
            logger.error(f"Failed to parse JSON message: {data[:100]}")
            return None
        except Exception as e:
            logger.error(f"Error deserializing message: {e}")
            return None