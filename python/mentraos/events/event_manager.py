"""Async event manager for handling MentraOS events."""

import asyncio
from typing import Dict, List, Callable, Any, Optional, Union
from collections import defaultdict

from ..utils.logger import get_logger
from .event_types import Event, EventType, AudioChunkEvent, TranscriptionEvent, TranslationEvent, BatteryUpdateEvent


logger = get_logger("event_manager")

EventHandler = Callable[[Event], Union[None, asyncio.Future]]


class EventManager:
    """Manages event subscriptions and dispatching."""
    
    def __init__(self):
        self._handlers: Dict[str, List[EventHandler]] = defaultdict(list)
        self._running = True
        self._event_queue: asyncio.Queue[Event] = asyncio.Queue()
        self._dispatch_task: Optional[asyncio.Task] = None
    
    async def start(self) -> None:
        """Start the event dispatcher."""
        if self._dispatch_task is None or self._dispatch_task.done():
            self._running = True
            self._dispatch_task = asyncio.create_task(self._dispatch_loop())
            logger.debug("Event manager started")
    
    async def stop(self) -> None:
        """Stop the event dispatcher."""
        self._running = False
        if self._dispatch_task and not self._dispatch_task.done():
            # Put a sentinel event to unblock the queue
            await self._event_queue.put(None)
            await self._dispatch_task
        logger.debug("Event manager stopped")
    
    def on(self, event_type: Union[str, EventType], handler: EventHandler) -> Callable[[], None]:
        """Register an event handler.
        
        Returns a function to unregister the handler.
        """
        event_key = str(event_type)
        self._handlers[event_key].append(handler)
        logger.debug(f"Registered handler for {event_key}")
        
        def unregister():
            if handler in self._handlers[event_key]:
                self._handlers[event_key].remove(handler)
                logger.debug(f"Unregistered handler for {event_key}")
        
        return unregister
    
    def once(self, event_type: Union[str, EventType], handler: EventHandler) -> Callable[[], None]:
        """Register a one-time event handler."""
        unregister = None
        
        async def wrapper(event: Event):
            if unregister:
                unregister()
            await handler(event) if asyncio.iscoroutinefunction(handler) else handler(event)
        
        unregister = self.on(event_type, wrapper)
        return unregister
    
    async def emit(self, event: Event) -> None:
        """Emit an event to be processed."""
        await self._event_queue.put(event)
    
    async def emit_from_message(self, message: Dict[str, Any], session_id: str) -> None:
        """Create and emit an event from a WebSocket message."""
        try:
            event_type = message.get("type", "")
            
            # Create specific event types
            if event_type == EventType.AUDIO_CHUNK:
                event = AudioChunkEvent.from_message(message, session_id)
            elif event_type == EventType.TRANSCRIPTION:
                event = TranscriptionEvent.from_message(message, session_id)
            elif event_type == EventType.TRANSLATION:
                event = TranslationEvent.from_message(message, session_id)
            elif event_type == EventType.BATTERY_UPDATE:
                event = BatteryUpdateEvent.from_message(message, session_id)
            else:
                # Generic event for unknown types
                event = Event.from_message(message, session_id)
            
            await self.emit(event)
            
        except Exception as e:
            logger.error(f"Failed to create event from message: {e}")
    
    async def _dispatch_loop(self) -> None:
        """Process events from the queue."""
        while self._running:
            try:
                # Wait for event with timeout
                event = await asyncio.wait_for(
                    self._event_queue.get(),
                    timeout=1.0
                )
                
                if event is None:  # Sentinel value to stop
                    break
                
                # Dispatch to handlers
                event_key = str(event.type)
                handlers = self._handlers.get(event_key, [])
                
                if handlers:
                    # Create tasks for all handlers
                    tasks = []
                    for handler in handlers:
                        if asyncio.iscoroutinefunction(handler):
                            tasks.append(asyncio.create_task(handler(event)))
                        else:
                            # Run sync handlers in executor
                            loop = asyncio.get_event_loop()
                            tasks.append(loop.run_in_executor(None, handler, event))
                    
                    # Wait for all handlers to complete
                    if tasks:
                        results = await asyncio.gather(*tasks, return_exceptions=True)
                        for i, result in enumerate(results):
                            if isinstance(result, Exception):
                                logger.error(f"Handler error for {event_key}: {result}")
                
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Dispatch loop error: {e}")
    
    def clear(self, event_type: Optional[Union[str, EventType]] = None) -> None:
        """Clear event handlers."""
        if event_type:
            event_key = str(event_type)
            self._handlers[event_key].clear()
            logger.debug(f"Cleared handlers for {event_key}")
        else:
            self._handlers.clear()
            logger.debug("Cleared all handlers")
    
    # Convenience methods for common events
    def on_audio_chunk(self, handler: Callable[[AudioChunkEvent], Any]) -> Callable[[], None]:
        """Register handler for audio chunk events."""
        return self.on(EventType.AUDIO_CHUNK, handler)
    
    def on_transcription(self, handler: Callable[[TranscriptionEvent], Any]) -> Callable[[], None]:
        """Register handler for transcription events."""
        return self.on(EventType.TRANSCRIPTION, handler)
    
    def on_translation(self, handler: Callable[[TranslationEvent], Any]) -> Callable[[], None]:
        """Register handler for translation events."""
        return self.on(EventType.TRANSLATION, handler)
    
    def on_battery_update(self, handler: Callable[[BatteryUpdateEvent], Any]) -> Callable[[], None]:
        """Register handler for battery update events."""
        return self.on(EventType.BATTERY_UPDATE, handler)