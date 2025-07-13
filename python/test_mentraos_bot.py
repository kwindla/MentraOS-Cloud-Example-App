#!/usr/bin/env python
"""Pipecat test bot for MentraOS WebSocket Transport."""

import asyncio
import os
import sys
import argparse

from loguru import logger
from dotenv import load_dotenv

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    Frame,
    TranscriptionFrame,
    InterimTranscriptionFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask, PipelineParams
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.openai import OpenAILLMService
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext

# Add parent directory to path to import mentraos module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mentraos.pipecat import MentraOSWebSocketTransport
from mentraos.pipecat.transport import TextWallFrame

# Load environment variables
load_dotenv()

# Configure logger
logger.remove()
logger.add(sys.stderr, level="DEBUG")


class TranscriptionLogger(FrameProcessor):
    """Log transcriptions to console and sends to MentraOS display."""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Process incoming frames."""
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame):
            logger.info(f"📝 Final Transcription: {frame.text}")
            await self.push_frame(TextWallFrame(f"You said: {frame.text}"), direction)
        elif isinstance(frame, InterimTranscriptionFrame):
            logger.debug(f"📝 Interim: {frame.text}")

        await self.push_frame(frame, direction)


async def run_bot(
    websocket_url: str, session_id: str, mentra_api_key: str, package_name: str
) -> None:
    """Run the Pipecat bot for a MentraOS session."""
    logger.info(f"🚀 Starting bot for session: {session_id}")
    logger.info(f"🔌 WebSocket URL: {websocket_url}")

    try:
        # Get API keys
        deepgram_api_key = os.getenv("DEEPGRAM_API_KEY")
        openai_api_key = os.getenv("OPENAI_API_KEY")

        if not deepgram_api_key:
            logger.error("❌ DEEPGRAM_API_KEY not set in environment")
            return
        if not openai_api_key:
            logger.error("❌ OPENAI_API_KEY not set in environment")
            return

        # Create MentraOS transport
        transport = MentraOSWebSocketTransport(
            websocket_url=websocket_url,
            session_id=session_id,
            api_key=mentra_api_key,
            package_name=package_name,
            vad_analyzer=SileroVADAnalyzer(),
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

        # Set up LLM with initial system message
        messages = [
            {
                "role": "system",
                "content": "You are a helpful AI assistant on smart glasses. "
                "Keep your responses brief and conversational. "
                "Maximum 2-3 sentences per response.",
            }
        ]

        llm = OpenAILLMService(api_key=openai_api_key)
        transcription_logger = TranscriptionLogger()
        context = OpenAILLMContext(messages)
        context_aggregator = llm.create_context_aggregator(context)

        # Build pipeline
        pipeline = Pipeline(
            [
                transport.input(),
                stt,
                transcription_logger,
                context_aggregator.user(),
                llm,
                context_aggregator.assistant(),
                transport.output(),
            ]
        )

        task = PipelineTask(
            pipeline,
            params=PipelineParams(
                enable_metrics=True,
                enable_usage_metrics=True,
            ),
        )

        @transport.event_handler("on_client_connected")
        async def on_client_connected(transport, app_session):
            logger.info("🎉 Client connected to MentraOS")
            # Initialize the conversation context
            await task.queue_frames([context_aggregator.user().get_context_frame()])

        logger.info(f"🎯 Pipeline running for session {session_id}")
        runner = PipelineRunner(handle_sigint=True)
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
    if not os.getenv("OPENAI_API_KEY"):
        logger.error("❌ Please set OPENAI_API_KEY in your .env file")
        sys.exit(1)

    # Run the bot
    try:
        asyncio.run(
            run_bot(
                websocket_url=args.websocket_url,
                session_id=args.session_id,
                mentra_api_key=args.api_key,
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
