"""MentraOS webhook server that spawns Pipecat bots."""

import asyncio
import os
import sys
import subprocess
from typing import Dict, Optional
from dotenv import load_dotenv

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import uvicorn

from mentraos.utils.logger import get_logger
from mentraos.utils.config import get_config

# Load environment variables
load_dotenv()

logger = get_logger("pipecat_webhook")


class PipecatBotManager:
    """Manages Pipecat bot subprocesses for MentraOS sessions."""
    
    def __init__(self):
        self._processes: Dict[str, subprocess.Popen] = {}
        self._config = get_config()
        
    async def start_bot(
        self,
        session_id: str,
        websocket_url: str
    ) -> None:
        """Start a Pipecat bot subprocess for a session."""
        # Check if bot already running for this session
        if session_id in self._processes:
            logger.warning(f"Bot already running for session {session_id}")
            return
            
        # Build command to run bot
        cmd = [
            sys.executable,
            "test_mentraos_bot.py",
            "--websocket-url", websocket_url,
            "--session-id", session_id,
            "--api-key", self._config.api_key,
            "--package-name", self._config.package_name
        ]
        
        logger.info(f"🚀 Starting bot subprocess for session {session_id}")
        logger.debug(f"Command: {' '.join(cmd)}")
        
        try:
            # Set environment for unbuffered output
            env = os.environ.copy()
            env['PYTHONUNBUFFERED'] = '1'
            
            # Start subprocess - inherit stdout/stderr so it logs directly to console
            process = subprocess.Popen(
                cmd,
                env=env
                # No stdout/stderr redirection - inherits parent's streams
            )
            
            self._processes[session_id] = process
            logger.info(f"✅ Bot started with PID {process.pid} for session {session_id}")
            
            # Start task to monitor process lifecycle (not output)
            asyncio.create_task(self._monitor_process(session_id, process))
            
        except Exception as e:
            logger.error(f"❌ Failed to start bot for session {session_id}: {e}")
            raise
            
    async def stop_bot(self, session_id: str) -> None:
        """Stop a Pipecat bot subprocess."""
        if session_id not in self._processes:
            logger.warning(f"No bot running for session {session_id}")
            return
            
        process = self._processes[session_id]
        logger.info(f"🛑 Stopping bot for session {session_id} (PID {process.pid})")
        
        try:
            # Try graceful termination first
            process.terminate()
            
            # Wait up to 5 seconds for process to end
            try:
                await asyncio.wait_for(
                    asyncio.create_task(self._wait_for_process(process)),
                    timeout=5.0
                )
            except asyncio.TimeoutError:
                logger.warning(f"Bot didn't terminate gracefully, killing PID {process.pid}")
                process.kill()
                
            del self._processes[session_id]
            logger.info(f"✅ Bot stopped for session {session_id}")
            
        except Exception as e:
            logger.error(f"Error stopping bot for session {session_id}: {e}")
            
    async def stop_all(self) -> None:
        """Stop all running bots."""
        session_ids = list(self._processes.keys())
        for session_id in session_ids:
            await self.stop_bot(session_id)
            
    async def _wait_for_process(self, process: subprocess.Popen) -> None:
        """Wait for a process to complete."""
        while process.poll() is None:
            await asyncio.sleep(0.1)
            
    async def _monitor_process(self, session_id: str, process: subprocess.Popen) -> None:
        """Monitor process lifecycle and cleanup when it exits."""
        try:
            # Wait for process to complete
            while process.poll() is None:
                await asyncio.sleep(0.5)
            
            # Process has exited
            exit_code = process.returncode
            if exit_code == 0:
                logger.info(f"✅ Bot for session {session_id} exited normally")
            else:
                logger.error(f"❌ Bot for session {session_id} exited with code {exit_code}")
            
            # Cleanup
            if session_id in self._processes:
                del self._processes[session_id]
                
        except Exception as e:
            logger.error(f"Error monitoring bot process for {session_id}: {e}")


# Create FastAPI app
app = FastAPI(title="MentraOS Pipecat Webhook Server")

# Create bot manager
bot_manager = PipecatBotManager()


@app.post("/webhook")
async def webhook_handler(request: Request):
    """Handle MentraOS webhook requests."""
    try:
        body = await request.json()
        logger.info(f"🗣️ Received webhook: {body}")
        
        # Extract fields
        webhook_type = body.get("type", "")
        user_id = body.get("userId", "")
        
        if webhook_type == "stop_request":
            # Handle disconnect/stop request
            session_id = body.get("sessionId", f"{user_id}-{bot_manager._config.package_name}")
            reason = body.get("reason", "unknown")
            logger.info(f"Received stop request for session {session_id}, reason: {reason}")
            
            # Stop bot for this session
            await bot_manager.stop_bot(session_id)
            
            return JSONResponse({"status": "ok"})
            
        else:
            # Handle connection request
            session_id = body.get("sessionId", f"{user_id}-{bot_manager._config.package_name}")
            websocket_url = body.get("augmentOSWebsocketUrl") or body.get("websocketUrl", "")
            
            if not websocket_url:
                raise HTTPException(status_code=400, detail="Missing websocketUrl/augmentOSWebsocketUrl")
                
            logger.info(f"Starting bot for user {user_id}, session {session_id}")
            
            # Start bot subprocess
            await bot_manager.start_bot(session_id, websocket_url)
            
            return JSONResponse({"status": "ok"})
            
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "active_bots": len(bot_manager._processes),
        "package_name": bot_manager._config.package_name
    }


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown."""
    logger.info("🛑 Shutting down webhook server")
    await bot_manager.stop_all()


async def main():
    """Run the webhook server."""
    config = get_config()
    config.validate()
    
    logger.info(f"🎯 Starting MentraOS Pipecat Webhook Server")
    logger.info(f"📦 Package: {config.package_name}")
    logger.info(f"🌐 Port: {config.port}")
    logger.info(f"🤖 Bot script: test_mentraos_bot.py")
    
    # Check if bot script exists
    if not os.path.exists("test_mentraos_bot.py"):
        logger.error("❌ test_mentraos_bot.py not found in current directory")
        sys.exit(1)
        
    # Check for Deepgram API key
    if not os.getenv("DEEPGRAM_API_KEY"):
        logger.warning("⚠️  DEEPGRAM_API_KEY not set - bots will fail to start")
    
    # Run the server
    config = uvicorn.Config(
        app=app,
        host="0.0.0.0",
        port=config.port,
        log_level="info"
    )
    server = uvicorn.Server(config)
    
    try:
        await server.serve()
    finally:
        await bot_manager.stop_all()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("\n🛑 Shutdown complete")
        pass