"""MentraOS webhook server that spawns Pipecat bots."""

import asyncio
import os
import sys
import subprocess
from typing import Dict, Optional
from dotenv import load_dotenv

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
import uvicorn
import aiofiles
from pathlib import Path
import numpy as np
import struct
import io

from mentraos.utils.logger import get_logger
from mentraos.utils.config import get_config

# Load environment variables
load_dotenv()

logger = get_logger("pipecat_webhook")

try:
    import lameenc
except ImportError:
    lameenc = None
    logger.warning("lameenc not installed - /sine endpoint will not work. Install with: pip install lameenc")


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


# Configurable delay for final byte (in seconds)
FINAL_BYTE_DELAY = float(os.getenv("FINAL_BYTE_DELAY", "0.1"))  # 100ms default


@app.get("/file/{filename}")
async def send_file_with_delay(filename: str, delay: Optional[float] = None):
    """Send a file with all but the final byte, delay, then send final byte.
    
    This endpoint demonstrates streaming a file with a configurable delay
    before sending the final byte. Useful for testing streaming behavior.
    
    Args:
        filename: Name of the file to send (must be in current directory)
        delay: Optional delay override in seconds (default: FINAL_BYTE_DELAY)
        
    Returns:
        StreamingResponse with the file contents
    """
    # Use provided delay or default
    final_byte_delay = delay if delay is not None else FINAL_BYTE_DELAY
    # Security: Only allow files in current directory, no path traversal
    file_path = Path(filename).name  # This strips any directory components
    if not Path(file_path).exists():
        raise HTTPException(status_code=404, detail=f"File {filename} not found")
    
    # Check if it's a regular file
    if not Path(file_path).is_file():
        raise HTTPException(status_code=400, detail=f"{filename} is not a file")
    
    async def stream_file_with_delay():
        """Generator that streams file with delay before final byte."""
        try:
            async with aiofiles.open(file_path, 'rb') as file:
                # Read entire file
                content = await file.read()
                
                if len(content) == 0:
                    # Empty file
                    logger.warning(f"File {filename} is empty")
                    return
                
                if len(content) == 1:
                    # Single byte file - wait then send it
                    logger.info(f"Sending single byte file {filename} after {final_byte_delay}s delay")
                    await asyncio.sleep(final_byte_delay)
                    yield content
                else:
                    # Send all but final byte
                    initial_content = content[:-1]
                    final_byte = content[-1:]
                    
                    logger.info(f"Sending {len(initial_content)} bytes of {filename}")
                    yield initial_content
                    
                    # Wait before sending final byte
                    logger.info(f"Waiting {final_byte_delay}s before sending final byte")
                    await asyncio.sleep(final_byte_delay)
                    
                    logger.info(f"Sending final byte of {filename}")
                    yield final_byte
                    
                logger.info(f"✅ Completed sending {filename} ({len(content)} bytes total)")
                
        except Exception as e:
            logger.error(f"Error streaming file {filename}: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    # Get file size for content-length header
    file_size = Path(file_path).stat().st_size
    
    # Determine media type based on file extension
    media_type = "application/octet-stream"  # default
    if file_path.endswith('.txt'):
        media_type = "text/plain"
    elif file_path.endswith('.json'):
        media_type = "application/json"
    elif file_path.endswith('.html'):
        media_type = "text/html"
    elif file_path.endswith('.mp3'):
        media_type = "audio/mpeg"
    elif file_path.endswith('.wav'):
        media_type = "audio/wav"
    
    return StreamingResponse(
        stream_file_with_delay(),
        media_type=media_type,
        headers={
            "Content-Length": str(file_size),
            "X-Final-Byte-Delay": str(final_byte_delay)
        }
    )


@app.get("/sine")
async def generate_sine_mp3(duration: float = 5.0):
    """Generate an MP3 stream of a 440Hz sine wave.
    
    Args:
        duration: Duration in seconds (default: 5.0)
        
    Returns:
        StreamingResponse with MP3 audio data
    """
    if lameenc is None:
        raise HTTPException(
            status_code=500, 
            detail="lameenc not installed. Run: pip install lameenc"
        )
    
    if duration <= 0 or duration > 60:
        raise HTTPException(
            status_code=400,
            detail="Duration must be between 0 and 60 seconds"
        )
    
    # Audio parameters
    sample_rate = 44100  # Standard CD quality
    frequency = 440.0    # A4 note
    channels = 1         # Mono
    
    # MP3 encoder setup
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(128)  # 128 kbps
    encoder.set_in_sample_rate(sample_rate)
    encoder.set_channels(channels)
    encoder.set_quality(2)  # High quality
    
    async def generate_mp3_stream():
        """Generator that produces MP3 data in real-time."""
        try:
            logger.info(f"Starting real-time MP3 sine wave generation: {duration}s at {frequency}Hz")
            
            # Calculate total samples
            total_samples = int(sample_rate * duration)
            
            # Generate sine wave in chunks for streaming
            chunk_duration = 0.1  # 100ms chunks
            chunk_samples = int(sample_rate * chunk_duration)
            
            samples_generated = 0
            start_time = asyncio.get_event_loop().time()
            
            while samples_generated < total_samples:
                # Track chunk generation start time
                chunk_start_time = asyncio.get_event_loop().time()
                
                # Calculate how many samples to generate in this chunk
                samples_to_generate = min(chunk_samples, total_samples - samples_generated)
                
                # Generate time array for this chunk
                t_start = samples_generated / sample_rate
                t_end = (samples_generated + samples_to_generate) / sample_rate
                t = np.linspace(t_start, t_end, samples_to_generate, endpoint=False)
                
                # Generate sine wave
                sine_wave = np.sin(2 * np.pi * frequency * t)
                
                # Scale to 16-bit PCM range
                pcm_data = (sine_wave * 32767).astype(np.int16)
                
                # Encode to MP3
                mp3_data = encoder.encode(pcm_data.tobytes())
                
                if mp3_data:
                    # Convert bytearray to bytes for Starlette
                    yield bytes(mp3_data)
                    logger.debug(f"Generated {len(mp3_data)} bytes of MP3 data")
                
                samples_generated += samples_to_generate
                
                # Calculate how long this chunk represents in real time
                chunk_real_duration = samples_to_generate / sample_rate
                
                # Calculate how long we've actually taken to generate this chunk
                chunk_generation_time = asyncio.get_event_loop().time() - chunk_start_time
                
                # Sleep for the remaining time to match real-time playback
                sleep_time = chunk_real_duration - chunk_generation_time
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
                    logger.debug(f"Paced: slept {sleep_time:.3f}s to match real-time")
                else:
                    logger.warning(f"Generation too slow: took {chunk_generation_time:.3f}s for {chunk_real_duration:.3f}s of audio")
            
            # Flush encoder
            mp3_data = encoder.flush()
            if mp3_data:
                # Convert bytearray to bytes for Starlette
                yield bytes(mp3_data)
                logger.debug(f"Flushed {len(mp3_data)} bytes of MP3 data")
            
            # Calculate total elapsed time
            total_time = asyncio.get_event_loop().time() - start_time
            logger.info(f"✅ Completed MP3 generation: {samples_generated} samples in {total_time:.2f}s (target: {duration}s)")
            
        except Exception as e:
            logger.error(f"Error generating MP3: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    return StreamingResponse(
        generate_mp3_stream(),
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "no-cache",
            "X-Audio-Duration": str(duration),
            "X-Audio-Frequency": str(frequency)
        }
    )


@app.get("/audio-stream/{filename}")
async def stream_audio_file(filename: str):
    """Stream MP3 audio file directly, with support for files being generated in real-time.
    
    Args:
        filename: Stem of the file in audio-generation directory
        
    Returns:
        StreamingResponse with MP3 audio data
    """
    # Sanitize filename to prevent path traversal
    safe_filename = Path(filename).name
    audio_dir = Path("audio-generation")
    
    # Check for completed file first
    completed_file = audio_dir / safe_filename
    generating_file = audio_dir / f"{safe_filename}.generating"
    
    async def stream_mp3():
        """Generator that streams MP3 data directly."""
        bytes_read = 0
        
        try:
            # Case 1: Completed file exists
            if completed_file.exists() and completed_file.is_file():
                logger.info(f"Streaming completed MP3 file: {completed_file}")
                
                async with aiofiles.open(completed_file, 'rb') as f:
                    # Stream in chunks
                    chunk_size = 8192  # 8KB chunks
                    
                    while True:
                        mp3_data = await f.read(chunk_size)
                        if not mp3_data:
                            break
                        
                        yield mp3_data
                        
                logger.info(f"✅ Completed streaming {completed_file}")
                return
            
            # Case 2: Generating file exists
            elif generating_file.exists() and generating_file.is_file():
                logger.info(f"Streaming generating MP3 file: {generating_file}")
                
                # Stream the file as it's being generated
                file_handle = await aiofiles.open(generating_file, 'rb')
                
                try:
                    while True:
                        # Read any new data
                        await file_handle.seek(bytes_read)
                        mp3_data = await file_handle.read(8192)  # Read up to 8KB at a time
                        
                        if mp3_data:
                            bytes_read += len(mp3_data)
                            yield mp3_data
                            logger.debug(f"Sent {len(mp3_data)} MP3 bytes ({bytes_read} total)")
                        
                        # Check if generating file still exists
                        if not generating_file.exists():
                            logger.info(f"Generating file disappeared, checking for completed file")
                            break
                        
                        # Sleep to pace the streaming
                        await asyncio.sleep(0.1)
                    
                finally:
                    await file_handle.close()
                
                # Check if completed file now exists with more data
                if completed_file.exists() and completed_file.is_file():
                    file_size = completed_file.stat().st_size
                    if file_size > bytes_read:
                        logger.info(f"Reading final {file_size - bytes_read} bytes from completed file")
                        
                        async with aiofiles.open(completed_file, 'rb') as f:
                            await f.seek(bytes_read)
                            remaining_data = await f.read()
                            
                            if remaining_data:
                                yield remaining_data
                    
                logger.info(f"✅ Completed streaming {safe_filename} ({bytes_read} bytes total)")
            
            else:
                # Neither file exists
                raise HTTPException(
                    status_code=404,
                    detail=f"File not found: {safe_filename} or {safe_filename}.generating"
                )
                
        except Exception as e:
            logger.error(f"Error streaming audio file {filename}: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    return StreamingResponse(
        stream_mp3(),
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "no-cache",
            "X-Audio-Source": str(safe_filename)
        }
    )


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