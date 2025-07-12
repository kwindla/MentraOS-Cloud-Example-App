"""Async WebSocket client with auto-reconnect capability."""

import asyncio
import json
from typing import Optional, Callable, Any, Dict
from datetime import datetime
import websockets
from websockets.client import WebSocketClientProtocol
from websockets.exceptions import WebSocketException

from ..utils.logger import get_logger
from .exceptions import ConnectionError


logger = get_logger("websocket_client")


class WebSocketClient:
    """Async WebSocket client with automatic reconnection."""
    
    def __init__(
        self,
        url: str,
        on_message: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_connect: Optional[Callable[[], None]] = None,
        on_disconnect: Optional[Callable[[], None]] = None,
        reconnect_interval: float = 5.0,
        max_reconnect_interval: float = 60.0,
        reconnect_decay: float = 1.5
    ):
        self.url = url
        self.on_message = on_message
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.reconnect_interval = reconnect_interval
        self.max_reconnect_interval = max_reconnect_interval
        self.reconnect_decay = reconnect_decay
        
        self._websocket: Optional[WebSocketClientProtocol] = None
        self._running = False
        self._reconnect_task: Optional[asyncio.Task] = None
        self._receive_task: Optional[asyncio.Task] = None
        self._send_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        self._current_reconnect_interval = reconnect_interval
        self._connection_lock = asyncio.Lock()
    
    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._websocket is not None and not self._websocket.closed
    
    async def connect(self) -> None:
        """Connect to WebSocket server."""
        async with self._connection_lock:
            if self.is_connected:
                logger.debug("Already connected")
                return
            
            try:
                logger.info(f"Connecting to {self.url}")
                self._websocket = await websockets.connect(self.url)
                self._running = True
                self._current_reconnect_interval = self.reconnect_interval
                
                # Start receive task
                self._receive_task = asyncio.create_task(self._receive_loop())
                
                # Start send task
                asyncio.create_task(self._send_loop())
                
                logger.info("Connected successfully")
                
                if self.on_connect:
                    await self.on_connect()
                    
            except Exception as e:
                logger.error(f"Connection failed: {e}")
                raise ConnectionError(f"Failed to connect to {self.url}: {e}")
    
    async def disconnect(self) -> None:
        """Disconnect from WebSocket server."""
        logger.info("Disconnecting...")
        self._running = False
        
        # Cancel reconnect task if running
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        
        # Cancel receive task if running
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
        
        # Close WebSocket connection
        if self._websocket:
            await self._websocket.close()
            self._websocket = None
        
        if self.on_disconnect:
            await self.on_disconnect()
        
        logger.info("Disconnected")
    
    async def send(self, message: Dict[str, Any]) -> None:
        """Send a message through WebSocket."""
        # Add timestamp if not present
        if "timestamp" not in message:
            message["timestamp"] = datetime.utcnow().isoformat() + "Z"
        
        await self._send_queue.put(message)
    
    async def _send_loop(self) -> None:
        """Process messages from send queue."""
        while self._running:
            try:
                # Wait for message with timeout to check running status
                message = await asyncio.wait_for(
                    self._send_queue.get(),
                    timeout=1.0
                )
                
                if self.is_connected:
                    message_str = json.dumps(message)
                    logger.trace(f"WS → {message}")
                    await self._websocket.send(message_str)
                else:
                    # Put message back in queue if not connected
                    await self._send_queue.put(message)
                    await asyncio.sleep(0.1)
                    
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Send error: {e}")
    
    async def _receive_loop(self) -> None:
        """Receive messages from WebSocket."""
        try:
            async for message in self._websocket:
                try:
                    # Handle both text and binary messages
                    if isinstance(message, bytes):
                        # For audio chunks and other binary data
                        logger.trace(f"WS ← <Binary data: {len(message)} bytes>")
                        if self.on_message:
                            await self.on_message({
                                "type": "binary",
                                "data": message
                            })
                    else:
                        # Parse JSON messages
                        data = json.loads(message)
                        logger.trace(f"WS ← {data}")
                        if self.on_message:
                            await self.on_message(data)
                            
                except json.JSONDecodeError:
                    logger.error(f"Failed to parse message: {message}")
                except Exception as e:
                    logger.error(f"Message handling error: {e}")
                    
        except WebSocketException as e:
            logger.error(f"WebSocket error: {e}")
        except Exception as e:
            logger.error(f"Receive loop error: {e}")
        finally:
            # Trigger reconnection if still running
            if self._running:
                logger.info("Connection lost, attempting to reconnect...")
                self._reconnect_task = asyncio.create_task(self._reconnect())
    
    async def _reconnect(self) -> None:
        """Attempt to reconnect with exponential backoff."""
        while self._running and not self.is_connected:
            logger.info(f"Reconnecting in {self._current_reconnect_interval} seconds...")
            await asyncio.sleep(self._current_reconnect_interval)
            
            try:
                await self.connect()
                return  # Successfully reconnected
            except Exception as e:
                logger.error(f"Reconnection failed: {e}")
                # Exponential backoff
                self._current_reconnect_interval = min(
                    self._current_reconnect_interval * self.reconnect_decay,
                    self.max_reconnect_interval
                )