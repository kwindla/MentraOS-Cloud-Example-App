"""MentraOS WebSocket Transport for Pipecat."""

import asyncio
from typing import Optional, Any, Dict, Callable

from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    StartFrame,
    EndFrame,
    TextFrame,
    TranscriptionFrame,
    InterimTranscriptionFrame,
    ErrorFrame,
    CancelFrame,
)
from dataclasses import dataclass
from pipecat.processors.frame_processor import FrameDirection
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import BaseTransport, TransportParams
from loguru import logger

from ..core.app_session import AppSession
from ..events.event_types import EventType, AudioChunkEvent, TranscriptionEvent
from ..protocol.subscriptions import Subscription


@dataclass
class TextWallFrame(TextFrame):
    """Frame for displaying text on MentraOS glasses text wall.
    
    This frame type is specifically for text that should be displayed
    on the MentraOS glasses display using the text wall layout.
    """
    pass


class MentraOSInputTransport(BaseInputTransport):
    """Input transport for receiving frames from MentraOS."""
    
    def __init__(self, transport: "MentraOSWebSocketTransport", params: TransportParams):
        super().__init__(params)
        self._transport = transport
        

class MentraOSOutputTransport(BaseOutputTransport):
    """Output transport for sending frames to MentraOS."""
    
    def __init__(self, transport: "MentraOSWebSocketTransport", params: TransportParams):
        super().__init__(params)
        self._transport = transport
        
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Process frames to be sent to MentraOS."""
        await super().process_frame(frame, direction)
        
        # Only send TextWallFrame to MentraOS
        if isinstance(frame, TextWallFrame):
            await self._transport._write_frame(frame)
        elif isinstance(frame, (EndFrame, CancelFrame)):
            await self._transport._write_frame(frame)
            
        # Push frame downstream
        await self.push_frame(frame, direction)
    
    async def _handle_frame(self, frame: Frame):
        """Override to prevent base class warning about unregistered destinations."""
        # We handle frames in process_frame, so just pass here
        pass


class MentraOSWebSocketTransport(BaseTransport):
    """Transport for MentraOS WebSocket connections.
    
    This transport:
    1. Connects to MentraOS using AppSession
    2. Converts MentraOS events to Pipecat frames
    3. Sends Pipecat frames back to MentraOS as display events
    """
    
    def __init__(
        self,
        websocket_url: str,
        session_id: str,
        api_key: str,
        package_name: str,
        enable_transcription: bool = False,
        input_name: str | None = None,
        output_name: str | None = None,
    ):
        super().__init__(input_name=input_name, output_name=output_name)
        
        self._websocket_url = websocket_url
        self._session_id = session_id
        self._api_key = api_key  
        self._package_name = package_name
        self._enable_transcription = enable_transcription
        self._app_session: Optional[AppSession] = None
        
        # Create transport params
        params = TransportParams()
        
        # Create input/output transports
        self._input_transport = MentraOSInputTransport(self, params)
        self._output_transport = MentraOSOutputTransport(self, params)
        
        # Tasks
        self._receive_task: Optional[asyncio.Task] = None
        self._running = False
        
        # Event callbacks (following FastAPIWebsocketTransport pattern)
        self._on_session_connected: Optional[Callable[[], None]] = None
        self._on_session_disconnected: Optional[Callable[[], None]] = None
        
    def input(self) -> MentraOSInputTransport:
        """Return the input transport."""
        return self._input_transport
        
    def output(self) -> MentraOSOutputTransport:
        """Return the output transport."""
        return self._output_transport
        
    async def start(self, frame: StartFrame):
        """Start the transport and connect to MentraOS."""
        logger.debug(f"MentraOSWebSocketTransport.start() called for session {self._session_id}")
        
        # Start input/output transports
        await self._input_transport.start(frame)
        await self._output_transport.start(frame)
        
        # Create and start AppSession
        await self._connect_session()
        
    async def stop(self, frame: EndFrame | None = None):
        """Stop the transport and disconnect from MentraOS."""
        
        self._running = False
        
        # Cancel receive task
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
                
        # Stop AppSession
        if self._app_session:
            try:
                await self._app_session.stop()
            except Exception as e:
                logger.error(f"Error stopping session: {e}")
            self._app_session = None
            
        # Stop input/output transports
        await self._input_transport.stop(frame)
        await self._output_transport.stop(frame)
        
        # Call disconnected callback
        if self._on_session_disconnected:
            await self._on_session_disconnected()
            
    async def _connect_session(self):
        """Connect to MentraOS via AppSession."""
        logger.debug(f"_connect_session called for {self._session_id}")
        
        # Extract user_id from session_id (format: userId-packageName)
        parts = self._session_id.split('-')
        user_id = parts[0] if parts else "unknown"
        
        # Create AppSession
        self._app_session = AppSession(
            session_id=self._session_id,
            user_id=user_id,
            package_name=self._package_name,
            api_key=self._api_key,
            websocket_url=self._websocket_url,
            server=None  # No server needed for standalone session
        )
        
        # Set up event handlers
        self._setup_event_handlers()
        
        # Start the session
        try:
            await self._app_session.start()
            logger.info(f"✅ MentraOS transport connected for session {self._session_id}")
            
            # Subscribe to audio
            await self._app_session.subscribe(Subscription.AUDIO_CHUNK)
            
            # Only subscribe to transcription if enabled
            if self._enable_transcription:
                await self._app_session.subscribe(Subscription.TRANSCRIPTION)
            
            # Show ready message
            await self._app_session.layouts.show_text_wall("AI Assistant Ready")
            
            # Start receiving
            self._running = True
            
            # Log subscription status
            if self._enable_transcription:
                logger.info("📱 Subscribed to audio chunks and transcriptions")
            else:
                logger.info("📱 Subscribed to audio chunks only (transcription disabled)")
            logger.info("🎤 Make sure microphone permission is enabled in MentraOS Console")
            
            # Call connected callback
            if self._on_session_connected:
                await self._on_session_connected()
                
        except Exception as e:
            logger.error(f"❌ Failed to start MentraOS session: {e}")
            await self._push_error(f"Connection failed: {e}")
            raise
            
    def _setup_event_handlers(self) -> None:
        """Set up event handlers for AppSession."""
        if not self._app_session:
            return
            
        @self._app_session.events.on_audio_chunk
        async def handle_audio(event: AudioChunkEvent):
            """Convert audio chunks to Pipecat frames."""
            if self._running:
                frame = InputAudioRawFrame(
                    audio=event.audio_data,
                    sample_rate=16000,
                    num_channels=1
                )
                await self._input_transport.push_frame(frame)
                
        @self._app_session.events.on_transcription
        async def handle_transcription(event: TranscriptionEvent):
            """Convert MentraOS transcriptions to Pipecat frames."""
            # Only push transcription frames if transcription is enabled
            if self._running and self._enable_transcription:
                if event.is_final:
                    # Final transcription
                    frame = TranscriptionFrame(
                        text=event.text,
                        user_id=self._session_id,
                        timestamp=event.timestamp.isoformat()
                    )
                    await self._input_transport.push_frame(frame)
                else:
                    # Interim transcription
                    frame = InterimTranscriptionFrame(
                        text=event.text,
                        user_id=self._session_id,
                        timestamp=event.timestamp.isoformat()
                    )
                    await self._input_transport.push_frame(frame)
                    
        @self._app_session.events.on_event
        async def handle_any_event(event):
            """Handle other events."""
            if event.type == EventType.APP_STOPPED.value:
                logger.info("App stopped event received")
                await self._push_error("Session stopped by MentraOS")
                # Stop the transport
                await self.stop()
                
    async def _write_frame(self, frame: Frame) -> None:
        """Write a frame to MentraOS."""
        if not self._app_session or not self._app_session.is_connected:
            logger.warning("Cannot send frame - session not connected")
            return
            
        try:
            if isinstance(frame, TextWallFrame):
                # Display text on glasses
                await self._app_session.layouts.show_text_wall(frame.text)
                    
            elif isinstance(frame, (EndFrame, CancelFrame)):
                # Session ending
                await self._app_session.layouts.show_text_wall("Session ending...")
                
        except Exception as e:
            logger.error(f"Error sending frame: {e}")
            await self._push_error(f"Send error: {e}")
            
    async def _push_error(self, error: str) -> None:
        """Push an error frame to the pipeline."""
        error_frame = ErrorFrame(error=error)
        await self._input_transport.push_frame(error_frame)
        
    # Event handler setters (following FastAPIWebsocketTransport pattern)
    def on_session_connected(self, handler: Callable[[], None]):
        """Set handler for session connected event."""
        self._on_session_connected = handler
        
    def on_session_disconnected(self, handler: Callable[[], None]):
        """Set handler for session disconnected event."""
        self._on_session_disconnected = handler