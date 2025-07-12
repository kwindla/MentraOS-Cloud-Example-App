"""App session management for MentraOS SDK."""

import asyncio
from datetime import datetime
from typing import Dict, Any, Optional, TYPE_CHECKING

from .websocket_client import WebSocketClient
from ..events.event_manager import EventManager
from ..events.event_types import EventType
from ..layouts.layout_manager import LayoutManager
from ..protocol.messages import (
    ConnectionInitMessage,
    SubscriptionUpdateMessage,
    MessageType,
    parse_message
)
from ..protocol.subscriptions import SubscriptionManager, Subscription
from ..utils.logger import get_logger
from .exceptions import SessionError, AuthenticationError

if TYPE_CHECKING:
    from .app_server import AppServer

logger = get_logger("app_session")


class AppSession:
    """Represents a MentraOS app session with WebSocket connection."""
    
    def __init__(
        self,
        session_id: str,
        user_id: str,
        package_name: str,
        api_key: str,
        websocket_url: str,
        server: Optional["AppServer"] = None
    ):
        self.session_id = session_id
        self.user_id = user_id
        self.package_name = package_name
        self.api_key = api_key
        self.websocket_url = websocket_url
        self.server = server
        
        # Components
        self.events = EventManager()
        self.layouts = LayoutManager(self)
        self._subscriptions = SubscriptionManager()
        
        # WebSocket client
        self._ws_client = WebSocketClient(
            url=websocket_url,
            on_message=self._handle_message,
            on_connect=self._on_connect,
            on_disconnect=self._on_disconnect
        )
        
        # State
        self._connected = False
        self._authenticated = False
        self._device_info: Dict[str, Any] = {}
        
        logger.debug(f"[{package_name}] App Session initialized")
    
    async def start(self) -> None:
        """Start the session and connect to WebSocket."""
        try:
            # Start event manager
            await self.events.start()
            
            # Connect WebSocket
            await self._ws_client.connect()
            
        except Exception as e:
            logger.error(f"Failed to start session: {e}")
            raise SessionError(f"Failed to start session: {e}")
    
    async def stop(self) -> None:
        """Stop the session and disconnect."""
        logger.info(f"Stopping session {self.session_id}")
        
        # Clear subscriptions
        self._subscriptions.clear()
        
        # Stop components
        await self.events.stop()
        await self._ws_client.disconnect()
        
        self._connected = False
        self._authenticated = False
    
    async def send(self, message: Dict[str, Any]) -> None:
        """Send a message through WebSocket."""
        if not self._connected:
            logger.warning("Attempted to send message while not connected")
            return
            
        await self._ws_client.send(message)
    
    async def subscribe(self, subscription: str) -> None:
        """Add a subscription."""
        if self._subscriptions.add(subscription):
            await self._update_subscriptions()
    
    async def unsubscribe(self, subscription: str) -> None:
        """Remove a subscription."""
        if self._subscriptions.remove(subscription):
            await self._update_subscriptions()
    
    async def _update_subscriptions(self) -> None:
        """Send subscription update to server."""
        if not self._connected:
            return
            
        message = SubscriptionUpdateMessage(
            packageName=self.package_name,
            subscriptions=self._subscriptions.get_all(),
            sessionId=self.session_id
        )
        
        # Convert message to dict and ensure enums are converted to strings
        msg_dict = message.to_dict()
        
        await self.send(msg_dict)
        logger.info(f"Updated subscriptions: {self._subscriptions.get_all()}")
    
    async def _on_connect(self) -> None:
        """Handle WebSocket connection."""
        logger.info(f"WebSocket connected for session {self.session_id}")
        self._connected = True
        
        # Send connection init
        init_message = ConnectionInitMessage(
            sessionId=self.session_id,
            packageName=self.package_name,
            apiKey=self.api_key
        )
        
        await self.send(init_message.to_dict())
    
    async def _on_disconnect(self) -> None:
        """Handle WebSocket disconnection."""
        logger.info(f"WebSocket disconnected for session {self.session_id}")
        self._connected = False
        self._authenticated = False
    
    async def _handle_message(self, data: Dict[str, Any]) -> None:
        """Handle incoming WebSocket message."""
        try:
            # Handle binary messages (audio chunks)
            if data.get("type") == "binary":
                await self._handle_audio_chunk(data.get("data", b""))
                return
            
            # Handle data_stream messages (transcriptions, etc.)
            if data.get("type") == "data_stream":
                stream_type = data.get("streamType", "")
                stream_data = data.get("data", {})
                
                if stream_type == "transcription":
                    # Extract transcription data and emit event
                    transcription_event = {
                        "type": "transcription",
                        "sessionId": self.session_id,
                        "text": stream_data.get("text", ""),
                        "isFinal": stream_data.get("isFinal", False),
                        "language": stream_data.get("transcribeLanguage", "en-US"),
                        "timestamp": data.get("timestamp", datetime.utcnow().isoformat() + "Z")
                    }
                    await self.events.emit_from_message(transcription_event, self.session_id)
                return
            
            # Handle connection acknowledgment
            if data.get("type") == MessageType.TPA_CONNECTION_ACK:
                await self._handle_connection_ack(data)
                return
            
            # Handle app_stopped event
            if data.get("type") == "app_stopped":
                logger.info(f"Received app_stopped event for session {self.session_id}")
                # Emit the event
                await self.events.emit_from_message(data, self.session_id)
                # Trigger disconnect
                await self.stop()
                return
            
            # For other message types, emit as event
            await self.events.emit_from_message(data, self.session_id)
            
        except Exception as e:
            logger.error(f"Error handling message: {e}")
    
    async def _handle_connection_ack(self, data: Dict[str, Any]) -> None:
        """Handle connection acknowledgment."""
        # If we receive a connection ack with error, handle it
        if "error" in data:
            error = data.get("error", "Authentication failed")
            logger.error(f"Connection failed: {error}")
            raise AuthenticationError(error)
        
        # Otherwise, connection is successful
        self._authenticated = True
        
        # Get device info from augmentosSettings (new format) or mentraosSettings (old format)
        self._device_info = data.get("augmentosSettings", data.get("mentraosSettings", {}))
        
        # Get capabilities
        capabilities = data.get("capabilities", {})
        if capabilities:
            device_model = capabilities.get("modelName", "Unknown")
            logger.info(f"Device capabilities loaded for model: {device_model}")
        
        # Initialize default subscriptions
        await self._initialize_subscriptions()
    
    async def _handle_audio_chunk(self, audio_data: bytes) -> None:
        """Handle binary audio chunk."""
        # Create audio chunk event
        await self.events.emit_from_message({
            "type": EventType.AUDIO_CHUNK.value,
            "data": audio_data,
            "sessionId": self.session_id
        }, self.session_id)
    
    async def _initialize_subscriptions(self) -> None:
        """Initialize default subscriptions after connection."""
        # Override in subclasses or let the app handle this
        pass
    
    @property
    def is_connected(self) -> bool:
        """Check if session is connected."""
        return self._connected and self._ws_client.is_connected
    
    @property
    def is_authenticated(self) -> bool:
        """Check if session is authenticated."""
        return self._authenticated
    
    @property
    def device_info(self) -> Dict[str, Any]:
        """Get device information."""
        return self._device_info.copy()