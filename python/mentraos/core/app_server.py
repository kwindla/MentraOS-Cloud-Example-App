"""App server implementation for MentraOS SDK."""

import asyncio
from typing import Dict, Optional, Any, Set
from abc import ABC, abstractmethod

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import uvicorn

from .app_session import AppSession
from ..utils.config import Config, get_config
from ..utils.logger import get_logger
from .exceptions import MentraOSError

logger = get_logger("app_server")


class AppServer(ABC):
    """Base class for MentraOS app servers."""
    
    def __init__(self, config: Optional[Config] = None):
        self.config = config or get_config()
        self.config.validate()
        
        # FastAPI app
        self.app = FastAPI(title=f"MentraOS App: {self.config.package_name}")
        
        # Active sessions
        self._sessions: Dict[str, AppSession] = {}
        self._session_tasks: Dict[str, asyncio.Task] = {}
        
        # Setup routes
        self._setup_routes()
        
        logger.info(f"🎯 App server initialized for {self.config.package_name}")
    
    def _setup_routes(self):
        """Setup FastAPI routes."""
        
        @self.app.post("/webhook")
        async def webhook_handler(request: Request):
            """Handle webhook from MentraOS."""
            try:
                body = await request.json()
                logger.info(f"🗣️ Received webhook: {body}")
                
                # Extract common fields
                user_id = body.get("userId", "")
                webhook_type = body.get("type", "")
                
                # Handle different webhook types
                if webhook_type == "stop_request":
                    # Handle disconnect/stop request
                    session_id = body.get("sessionId", f"{user_id}-{self.config.package_name}")
                    reason = body.get("reason", "unknown")
                    logger.info(f"Received stop request for session {session_id}, reason: {reason}")
                    
                    # Cleanup session if exists
                    if session_id in self._sessions:
                        await self._cleanup_session(session_id)
                    
                    return JSONResponse({"status": "ok"})
                
                else:
                    # Handle connection request (default behavior)
                    session_id = f"{user_id}-{self.config.package_name}"
                    # Try both field names for compatibility
                    websocket_url = body.get("augmentOSWebsocketUrl") or body.get("websocketUrl", "")
                    
                    if not websocket_url:
                        raise HTTPException(status_code=400, detail="Missing websocketUrl/augmentOSWebsocketUrl")
                    
                    logger.info(
                        f"Received session request for user {user_id}, "
                        f"session {session_id}"
                    )
                    
                    # Create and start session
                    await self._create_session(
                        session_id=session_id,
                        user_id=user_id,
                        websocket_url=websocket_url
                    )
                
                return JSONResponse({"status": "ok"})
                
            except Exception as e:
                logger.error(f"Webhook error: {e}")
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.get("/health")
        async def health_check():
            """Health check endpoint."""
            return {
                "status": "healthy",
                "package_name": self.config.package_name,
                "active_sessions": len(self._sessions)
            }
        
        @self.app.on_event("shutdown")
        async def shutdown_event():
            """Cleanup on shutdown."""
            await self.stop()
    
    async def _create_session(
        self,
        session_id: str,
        user_id: str,
        websocket_url: str
    ) -> AppSession:
        """Create and start a new session."""
        # Check if session already exists
        if session_id in self._sessions:
            logger.warning(f"Session {session_id} already exists")
            return self._sessions[session_id]
        
        # Create session
        session = AppSession(
            session_id=session_id,
            user_id=user_id,
            package_name=self.config.package_name,
            api_key=self.config.api_key,
            websocket_url=websocket_url,
            server=self
        )
        
        # Store session
        self._sessions[session_id] = session
        
        # Start session in background
        task = asyncio.create_task(self._run_session(session))
        self._session_tasks[session_id] = task
        
        return session
    
    async def _run_session(self, session: AppSession) -> None:
        """Run a session."""
        try:
            # Start session
            await session.start()
            
            # Call user's session handler
            await self.on_session(session, session.session_id, session.user_id)
            
            # Keep session alive
            while session.is_connected:
                await asyncio.sleep(1)
                
        except Exception as e:
            logger.error(f"Session error: {e}")
        finally:
            # Cleanup
            await self._cleanup_session(session.session_id)
    
    async def _cleanup_session(self, session_id: str) -> None:
        """Cleanup a session."""
        logger.info(f"👋 Closing session {session_id}")
        
        # Stop session
        if session_id in self._sessions:
            session = self._sessions[session_id]
            await session.stop()
            
            # Call cleanup handler
            try:
                await self.on_session_end(session, session_id, session.user_id)
            except Exception as e:
                logger.error(f"Error in on_session_end: {e}")
            
            del self._sessions[session_id]
        
        # Cancel task
        if session_id in self._session_tasks:
            task = self._session_tasks[session_id]
            if not task.done():
                task.cancel()
            del self._session_tasks[session_id]
        
        logger.info(f"Session {session_id} disconnected")
    
    @abstractmethod
    async def on_session(
        self,
        session: AppSession,
        session_id: str,
        user_id: str
    ) -> None:
        """Handle new session. Override this method in subclasses.
        
        Args:
            session: The app session instance
            session_id: Unique session identifier
            user_id: User identifier
        """
        pass
    
    async def on_session_end(
        self,
        session: AppSession,
        session_id: str,
        user_id: str
    ) -> None:
        """Handle session end. Override this method if needed.
        
        Args:
            session: The app session instance
            session_id: Unique session identifier
            user_id: User identifier
        """
        pass
    
    async def start(self) -> None:
        """Start the app server."""
        logger.info(
            f"🎯 App server running at http://localhost:{self.config.port}"
        )
        
        config = uvicorn.Config(
            app=self.app,
            host="0.0.0.0",
            port=self.config.port,
            log_level="info" if not self.config.debug else "debug"
        )
        server = uvicorn.Server(config)
        await server.serve()
    
    async def stop(self) -> None:
        """Stop the app server and cleanup."""
        logger.info("🛑 Shutting down...")
        
        # Stop all sessions
        session_ids = list(self._sessions.keys())
        for session_id in session_ids:
            await self._cleanup_session(session_id)
        
        logger.info("👋 Shutdown complete")
    
    def get_session(self, session_id: str) -> Optional[AppSession]:
        """Get a session by ID."""
        return self._sessions.get(session_id)
    
    def get_active_sessions(self) -> Set[str]:
        """Get all active session IDs."""
        return set(self._sessions.keys())