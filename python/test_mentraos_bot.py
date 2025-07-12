#!/usr/bin/env python
"""Pipecat bot for MentraOS integration.

This bot:
1. Receives websocket URL from command line
2. Connects to MentraOS using MentraOSWebSocketTransport
3. Processes audio with Deepgram STT
4. Logs transcriptions and sends them back to glasses
"""

import asyncio
import os
import sys
import argparse

from loguru import logger
from dotenv import load_dotenv

from pipecat.frames.frames import (
    Frame,
    LLMMessagesFrame,
    TextFrame,
    TranscriptionFrame,
    InterimTranscriptionFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.deepgram.stt import DeepgramSTTService

# Add parent directory to path to import mentraos module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mentraos.pipecat import MentraOSWebSocketTransport
from mentraos.pipecat.transport import TextWallFrame

# Load environment variables
load_dotenv()

# Configure logger
logger.remove()
logger.add(sys.stderr, level="DEBUG")


class TranscriptionProcessor(FrameProcessor):
    """Process transcriptions and generate responses."""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Process incoming frames."""
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame):
            # Final transcription
            logger.info(f"📝 Final Transcription: {frame.text}")

            if frame.text.strip():
                # Send transcription to glasses display
                await self.push_frame(TextWallFrame(f"You said: {frame.text}"), direction)

                # Also generate an LLM message frame as per the plan
                messages = [{"role": "user", "content": frame.text}]
                await self.push_frame(LLMMessagesFrame(messages=messages), direction)

        elif isinstance(frame, InterimTranscriptionFrame):
            # Interim transcription - just log it
            logger.debug(f"📝 Interim: {frame.text}")

        await self.push_frame(frame, direction)


async def run_bot(websocket_url: str, session_id: str, api_key: str, package_name: str) -> None:
    """Run the Pipecat bot for a MentraOS session."""
    logger.info(f"🚀 Starting bot for session: {session_id}")
    logger.info(f"🔌 WebSocket URL: {websocket_url}")

    try:
        # Get Deepgram API key
        deepgram_api_key = os.getenv("DEEPGRAM_API_KEY")
        if not deepgram_api_key:
            logger.error("❌ DEEPGRAM_API_KEY not set in environment")
            return

        # Create MentraOS transport
        transport = MentraOSWebSocketTransport(
            websocket_url=websocket_url,
            session_id=session_id,
            api_key=api_key,
            package_name=package_name,
        )

        # Create STT service
        stt = DeepgramSTTService(
            api_key=deepgram_api_key,
            language="en-US",
            model="nova-2",
            sample_rate=16000,
            channels=1,
            interim_results=True,
            endpointing=300,
            smart_format=True,
            encoding="linear16",
        )

        # Create transcription processor
        processor = TranscriptionProcessor()

        # Build pipeline: transport -> STT -> processor -> transport
        pipeline = Pipeline(
            [
                transport.input(),  # Receive audio from MentraOS
                stt,  # Transcribe with Deepgram
                processor,  # Process transcriptions
                transport.output(),  # Send results back to MentraOS
            ]
        )

        # Create pipeline task
        task = PipelineTask(pipeline)

        # Run the pipeline
        logger.info(f"🎯 Pipeline running for session {session_id}")
        runner = PipelineRunner()

        # Start the transport manually since it's the source
        from pipecat.frames.frames import StartFrame

        await transport.start(StartFrame())

        await runner.run(task)
        logger.info(f"✅ Pipeline completed for session {session_id}")

    except Exception as e:
        logger.error(f"❌ Bot error: {e}")
        raise
    finally:
        logger.info(f"👋 Bot shutting down for session {session_id}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="MentraOS Pipecat Bot")
    parser.add_argument(
        "--websocket-url", required=True, help="MentraOS WebSocket URL (augmentOSWebsocketUrl)"
    )
    parser.add_argument("--session-id", required=True, help="Session ID")
    parser.add_argument("--api-key", required=True, help="MentraOS API key")
    parser.add_argument("--package-name", required=True, help="Package name")

    args = parser.parse_args()

    # Check for required environment variables
    if not os.getenv("DEEPGRAM_API_KEY"):
        logger.error("❌ Please set DEEPGRAM_API_KEY in your .env file")
        sys.exit(1)

    # Run the bot
    try:
        asyncio.run(
            run_bot(
                websocket_url=args.websocket_url,
                session_id=args.session_id,
                api_key=args.api_key,
                package_name=args.package_name,
            )
        )
    except KeyboardInterrupt:
        logger.info("🛑 Bot interrupted")
    except Exception as e:
        logger.error(f"❌ Bot failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
