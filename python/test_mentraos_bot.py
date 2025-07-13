#!/usr/bin/env python
"""Pipecat test bot for MentraOS WebSocket Transport."""

import asyncio
import os
import sys
import argparse

from loguru import logger
from dotenv import load_dotenv

from pipecat.processors.filters.stt_mute_filter import STTMuteConfig, STTMuteFilter, STTMuteStrategy
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
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.processors.aggregators.openai_llm_context import (
    OpenAILLMContext,
    OpenAILLMContextFrame,
)


# Add parent directory to path to import mentraos module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mentraos.pipecat import MentraOSWebSocketTransport
from mentraos.pipecat.transport import TextWallFrame

# Load environment variables
load_dotenv()

# Configure logger
logger.remove()
# logger.add(sys.stderr, level="TRACE")
logger.add(sys.stderr, level="DEBUG")


class UserTranscriptionHandler(FrameProcessor):
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


class AssistantResponseHandler(FrameProcessor):
    """Log assistant responses to console and sends to MentraOS display."""

    def __init__(self, app_session):
        """Initialize with app session for audio access."""
        super().__init__()
        self._app_session = app_session

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Process incoming frames."""
        await super().process_frame(frame, direction)

        if isinstance(frame, OpenAILLMContextFrame):
            last_message = frame.context.messages[-1]
            logger.info(f"📝 Assistant: {last_message['content']}")
            await self.push_frame(
                TextWallFrame(f"Assistant said: {last_message['content']}"), direction
            )

            # Don't speak - the MentraOSOutputTransport will handle TTS audio
            # if last_message.get("role") == "assistant" and last_message.get("content"):
            #     await self._app_session.audio.speak(last_message["content"])

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
        cartesia_api_key = os.getenv("CARTESIA_API_KEY")

        if not deepgram_api_key:
            logger.error("❌ DEEPGRAM_API_KEY not set in environment")
            return
        if not openai_api_key:
            logger.error("❌ OPENAI_API_KEY not set in environment")
            return
        if not cartesia_api_key:
            logger.error("❌ CARTESIA_API_KEY not set in environment")
            return

        # Create MentraOS transport
        transport = MentraOSWebSocketTransport(
            websocket_url=websocket_url,
            session_id=session_id,
            api_key=mentra_api_key,
            package_name=package_name,
            vad_analyzer=SileroVADAnalyzer(),
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

        stt_mute_filter = STTMuteFilter(config=STTMuteConfig(strategies={STTMuteStrategy.ALWAYS}))

        llm = OpenAILLMService(api_key=openai_api_key)
        tts = CartesiaTTSService(
            api_key=cartesia_api_key,
            voice_id="71a7ad14-091c-4e8e-a314-022ece01c121",  # British Reading Lady
        )
        user_transcription_handler = UserTranscriptionHandler()
        # Create a placeholder for the handler - will set app_session after transport is ready
        assistant_response_handler = AssistantResponseHandler(None)
        context = OpenAILLMContext(messages)
        context_aggregator = llm.create_context_aggregator(
            context,
            # need to set expect_stripped_words if the assistant aggregator is before the transport output.
            # assistant_params=LLMAssistantAggregatorParams(expect_stripped_words=False),
        )

        # Build pipeline
        pipeline = Pipeline(
            [
                transport.input(),
                stt_mute_filter,
                stt,
                user_transcription_handler,
                context_aggregator.user(),
                llm,
                tts,
                transport.output(),
                context_aggregator.assistant(),
                assistant_response_handler,
            ]
        )

        task = PipelineTask(
            pipeline,
            params=PipelineParams(
                enable_metrics=True,
                enable_usage_metrics=True,
                observers=[
                    # LMLogObserver()
                ],
            ),
        )

        @transport.event_handler("on_client_connected")
        async def on_client_connected(transport, app_session):
            logger.info("🎉 Client connected to MentraOS")
            # Set the app_session on the assistant response handler
            assistant_response_handler._app_session = app_session

            # Register audio play response handler
            @app_session.events.on_audio_play_response
            async def handle_audio_response(event):
                logger.info(
                    f"🔊 AUDIO RESPONSE EVENT: requestId={event.request_id}, success={event.success}"
                )
                if not event.success and event.error:
                    logger.error(f"  Error: {event.error}")

            # Don't send initial context frame - wait for user to speak first
            # await task.queue_frames([context_aggregator.user().get_context_frame()])

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
