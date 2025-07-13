"""MentraOS WebSocket Transport for Pipecat."""

import asyncio
import os
import time
from typing import Optional, Callable
from concurrent.futures import ThreadPoolExecutor

from pipecat.frames.frames import (
    Frame,
    DataFrame,
    InputAudioRawFrame,
    StartFrame,
    EndFrame,
    TranscriptionFrame,
    InterimTranscriptionFrame,
    ErrorFrame,
    CancelFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    OutputAudioRawFrame,
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    TTSTextFrame,
)
from dataclasses import dataclass
from pipecat.processors.frame_processor import FrameDirection
from pipecat.audio.vad.vad_analyzer import VADAnalyzer, VADState
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import BaseTransport, TransportParams
from loguru import logger

from ..core.app_session import AppSession
from ..events.event_types import EventType, AudioChunkEvent, TranscriptionEvent
from ..protocol.subscriptions import Subscription
from .mp3_encoder import MP3StreamEncoder


@dataclass
class TextWallFrame(DataFrame):
    """Frame for displaying text on MentraOS glasses text wall.

    This frame type is specifically for text that should be displayed
    on the MentraOS glasses display using the text wall layout.
    """

    text: str


class MentraOSInputTransport(BaseInputTransport):
    """Input transport for receiving frames from MentraOS."""

    def __init__(self, transport: "MentraOSWebSocketTransport", params: TransportParams):
        super().__init__(params)
        self._transport = transport
        self._executor = ThreadPoolExecutor(max_workers=1)

        # VAD state tracking
        self._vad_state: VADState = VADState.QUIET

    async def _vad_analyze(self, audio_frame: InputAudioRawFrame) -> VADState:
        """Analyze audio frame for voice activity."""
        if not self._params.vad_analyzer:
            return VADState.QUIET

        # Run VAD analysis in executor to avoid blocking
        state = await self.get_event_loop().run_in_executor(
            self._executor, self._params.vad_analyzer.analyze_audio, audio_frame.audio
        )
        return state

    async def _handle_vad(self, audio_frame: InputAudioRawFrame, vad_state: VADState):
        """Handle VAD state changes and emit appropriate frames."""
        if vad_state != self._vad_state:
            if vad_state == VADState.SPEAKING and self._vad_state != VADState.SPEAKING:
                # User started speaking
                await self.push_frame(UserStartedSpeakingFrame())
            elif vad_state != VADState.SPEAKING and self._vad_state == VADState.SPEAKING:
                # User stopped speaking
                await self.push_frame(UserStoppedSpeakingFrame())

            self._vad_state = vad_state

    async def start(self, frame: StartFrame):
        """Start the input transport and trigger parent connection."""
        # Call parent start() to initialize base transport
        await super().start(frame)
        
        # Trigger the parent transport's connection logic
        await self._transport.start(frame)

    async def cleanup(self):
        """Clean up resources."""
        await super().cleanup()
        if hasattr(self, "_executor"):
            self._executor.shutdown(wait=True)


class MentraOSOutputTransport(BaseOutputTransport):
    """Output transport for sending frames to MentraOS."""

    def __init__(self, transport: "MentraOSWebSocketTransport", params: TransportParams, bot_stopped_speaking_delay: float = 2.5):
        super().__init__(params)
        self._transport = transport
        # Use absolute path for audio generation directory
        audio_dir = os.path.join(os.path.dirname(__file__), "..", "..", "audio-generation")
        self._mp3_encoder = MP3StreamEncoder(output_dir=audio_dir)
        self._current_tts_filename: Optional[str] = None
        self._public_url = os.getenv("MENTRAOS_PUBLIC_URL", "https://khk-mentra.ngrok.app")
        self._tts_active = False  # Track if we're between TTSStartedFrame and TTSStoppedFrame
        
        # Audio duration tracking for silent frame generation
        self._audio_start_time: Optional[float] = None
        self._total_audio_samples: int = 0
        self._audio_sample_rate: int = 24000  # Default, will be updated from frames
        self._silent_frames_task: Optional[asyncio.Task] = None
        self._bot_stopped_speaking_delay = bot_stopped_speaking_delay  # MentraOS audio startup delay
        
        logger.info(f"🎵 MentraOSOutputTransport initialized with MP3 streaming (URL: {self._public_url})")

    async def start(self, frame: StartFrame):
        """Start the output transport."""
        logger.info("📌 MentraOSOutputTransport.start() called")
        await super().start(frame)
        
        # Initialize MediaSenders for audio routing
        # This creates the default MediaSender (destination=None) and any configured destinations
        await self.set_transport_ready(frame)
        
        logger.info(f"🚀 MentraOSOutputTransport started, media_senders: {list(self._media_senders.keys())}")
        # Transport initialized with write_audio_frame support

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Process frames to be sent to MentraOS."""
        # Handle TTSTextFrame specially to prevent duplication
        # TTSTextFrame is already pushed by the TTS service, so we just need to
        # forward it downstream without going through the parent's audio queue
        if isinstance(frame, TTSTextFrame):
            await self.push_frame(frame, direction)
            return
        
        # Log all frames for debugging
        # if not isinstance(frame, (InputAudioRawFrame, OutputAudioRawFrame)) or isinstance(frame, TTSAudioRawFrame):
        #     logger.debug(f"📊 MentraOSOutputTransport.process_frame: {type(frame).__name__}")
        
        # Also log bot speaking frames
        # if isinstance(frame, (BotStartedSpeakingFrame, BotStoppedSpeakingFrame)):
        #     logger.info(f"🤖 MentraOSOutputTransport.process_frame: {type(frame).__name__} (direction: {direction})")
        
        # Handle TTS state tracking
        if isinstance(frame, TTSStartedFrame):
            logger.info("🎤 Received TTSStartedFrame - TTS is now active")
            self._tts_active = True
        elif isinstance(frame, TTSStoppedFrame):
            logger.info("🔇 Received TTSStoppedFrame - finalizing MP3 stream")
            self._tts_active = False
            
            # Calculate remaining playback time and start silent frames if needed
            if self._audio_start_time and self._total_audio_samples > 0:
                audio_duration = self._total_audio_samples / self._audio_sample_rate
                elapsed_time = time.time() - self._audio_start_time
                remaining_time = audio_duration - elapsed_time + 0.5  # Add 500ms buffer for network/playback delays
                
                # Add MentraOS audio startup delay
                total_silent_duration = remaining_time + self._bot_stopped_speaking_delay
                
                logger.info(f"⏱️ Audio stats: Total duration={audio_duration:.3f}s, Elapsed={elapsed_time:.3f}s, Remaining={remaining_time:.3f}s")
                logger.info(f"🎯 Adding {self._bot_stopped_speaking_delay}s MentraOS startup delay = {total_silent_duration:.3f}s total silent frames")
                
                if total_silent_duration > 0:
                    # Cancel any existing silent frames task
                    if self._silent_frames_task and not self._silent_frames_task.done():
                        self._silent_frames_task.cancel()
                        pass  # Silent frames task cancelled
                    
                    # Start new silent frames task with the extended duration
                    self._silent_frames_task = asyncio.create_task(self._send_silent_frames(total_silent_duration))
                    # Started silent frames task
                else:
                    pass  # Audio generation was slower than playback - no silent frames needed
            
            # Finalize the current MP3 stream
            if self._current_tts_filename:
                if self._mp3_encoder.finalize_stream():
                    logger.info(f"✅ Finalized TTS audio file: {self._current_tts_filename}")
                else:
                    logger.error(f"❌ Failed to finalize TTS audio file: {self._current_tts_filename}")
                self._current_tts_filename = None
            else:
                pass  # TTSStoppedFrame received with no active stream
        
        # if isinstance(frame, TTSAudioRawFrame):
        #     logger.info(f"🎵 MentraOSOutputTransport received TTSAudioRawFrame: {len(frame.audio)} bytes, tts_active={self._tts_active}")
        #     # Debug: Check transport_destination
        #     logger.debug(f"🔍 TTSAudioRawFrame transport_destination: {getattr(frame, 'transport_destination', 'NOT SET')}")
        #     logger.debug(f"🔍 Available media_senders: {list(self._media_senders.keys())}")
        
        # Call parent's process_frame to handle audio routing through MediaSenders
        await super().process_frame(frame, direction)
        
        # Only send TextWallFrame to MentraOS
        if isinstance(frame, TextWallFrame):
            await self._transport._write_frame(frame)
        elif isinstance(frame, (EndFrame, CancelFrame)):
            await self._transport._write_frame(frame)
        
        # Note: We do NOT push frames downstream here because:
        # 1. SystemFrames are already pushed by the parent's process_frame
        # 2. Non-system frames that go through _handle_frame -> handle_sync_frame
        #    are pushed downstream by the MediaSender's _audio_task_handler
        # 3. Pushing here would cause duplicate frame delivery

    
    async def stop(self, frame: EndFrame):
        """Stop the output transport and finalize any active streams."""
        # Reset TTS state
        self._tts_active = False
        
        # Cancel silent frames task if running
        if self._silent_frames_task and not self._silent_frames_task.done():
            self._silent_frames_task.cancel()
            pass  # Silent frames task cancelled on stop
        
        # Finalize any active MP3 stream
        if self._current_tts_filename:
            if self._mp3_encoder.finalize_stream():
                logger.info(f"✅ Finalized TTS audio file on stop: {self._current_tts_filename}")
            else:
                logger.error(f"❌ Failed to finalize TTS audio file on stop: {self._current_tts_filename}")
            self._current_tts_filename = None
        
        await super().stop(frame)
    
    async def write_audio_frame(self, frame: OutputAudioRawFrame):
        """Override to intercept audio frames and write to MP3."""
        
        if isinstance(frame, TTSAudioRawFrame):
            # Only process TTS audio if we're between TTSStartedFrame and TTSStoppedFrame
            if not self._tts_active:
                return
            
            # Processing TTSAudioRawFrame
            try:
                # Track audio duration - start timing and reset samples when new stream starts
                if not self._current_tts_filename:
                    self._audio_start_time = time.time()
                    self._total_audio_samples = 0
                    self._audio_sample_rate = frame.sample_rate
                    logger.info(f"⏱️ Starting audio duration tracking at {self._audio_start_time:.3f}, sample_rate={self._audio_sample_rate}")
                
                # Count samples in this frame (assuming 16-bit audio = 2 bytes per sample)
                num_samples = len(frame.audio) // 2
                self._total_audio_samples += num_samples
                
                # Start new stream if needed
                if not self._current_tts_filename:
                    self._current_tts_filename = self._mp3_encoder.start_new_stream(
                        frame.sample_rate,
                        frame.num_channels
                    )
                    
                    if not self._current_tts_filename:
                        logger.error("Failed to start MP3 stream")
                        return
                    
                    # Write first chunk
                    if self._mp3_encoder.write_audio_chunk(frame.audio):
                        
                        # Send play_audio request to MentraOS
                        audio_url = f"{self._public_url}/audio-stream/{self._current_tts_filename}"
                        logger.info(f"🔊 Sending play_audio request: {audio_url}")
                        
                        try:
                            result = await self._transport._app_session.audio.play_audio(
                                audio_url=audio_url,
                                volume=1.0,
                                stop_other_audio=True
                            )
                            logger.info(f"📢 Play audio result: {result}")
                        except Exception as e:
                            logger.error(f"❌ Failed to send play_audio request: {e}")
                else:
                    # Write subsequent chunks
                    if not self._mp3_encoder.write_audio_chunk(frame.audio):
                        logger.error(f"❌ Failed to write audio chunk")
                        
            except Exception as e:
                logger.error(f"❌ Error handling TTS audio: {e}")
        
        # else:
        #     logger.info(f"📤 Non-TTS audio frame: {type(frame).__name__}")
        
        # Note: We do NOT forward frames from write_audio_frame - this is the final
        # destination. The MediaSender tracks bot speaking state when it processes
        # TTSAudioRawFrame frames through the audio task.
    
    async def _send_silent_frames(self, duration: float):
        """Send silent audio frames to keep MediaSender active during audio playback."""
        # Starting to send silent frames to maintain bot speaking state
        
        # Send frames every 20ms (typical audio frame duration)
        frame_duration = 0.02  # 20ms
        num_frames = int(duration / frame_duration)
        
        # Create silent audio (480 samples at 24kHz = 20ms)
        samples_per_frame = int(self._audio_sample_rate * frame_duration)
        silent_audio = b'\x00' * (samples_per_frame * 2)  # 16-bit samples = 2 bytes per sample
        
        # Silent frame config: {num_frames} frames, {samples_per_frame} samples/frame
        
        start_time = time.time()
        frames_sent = 0
        
        try:
            for i in range(num_frames):
                # Create silent OutputAudioRawFrame (not TTSAudioRawFrame!)
                silent_frame = OutputAudioRawFrame(
                    audio=silent_audio,
                    sample_rate=self._audio_sample_rate,
                    num_channels=1
                )
                
                # Route through parent's process_frame to reach MediaSender
                # This will keep the MediaSender's audio task active
                await super().process_frame(silent_frame, FrameDirection.DOWNSTREAM)
                
                frames_sent += 1
                
                # Progress tracking
                # if frames_sent % 50 == 0:  # 50 frames = 1 second at 20ms/frame
                #     elapsed = time.time() - start_time
                
                # Wait for next frame timing
                await asyncio.sleep(frame_duration)
            
            total_time = time.time() - start_time
            # Finished sending silent frames
            
        except asyncio.CancelledError:
            # Silent frames task cancelled
            raise
        except Exception as e:
            logger.error(f"❌ Error in silent frames task: {e}")
        finally:
            # Reset audio tracking
            self._audio_start_time = None
            self._total_audio_samples = 0
    
    async def cancel(self, frame: CancelFrame):
        """Handle cancellation/interruption."""
        logger.info("🛑 Handling interruption - finalizing audio stream")
        
        # Reset TTS state
        self._tts_active = False
        
        # Cancel silent frames task if running
        if self._silent_frames_task and not self._silent_frames_task.done():
            self._silent_frames_task.cancel()
            pass  # Silent frames task cancelled on interruption
        
        # Finalize any active stream on interruption
        if self._current_tts_filename:
            if self._mp3_encoder.finalize_stream():
                logger.info(f"✅ Finalized interrupted TTS audio: {self._current_tts_filename}")
            else:
                logger.error(f"❌ Failed to finalize interrupted TTS audio: {self._current_tts_filename}")
            self._current_tts_filename = None
        
        await super().cancel(frame)


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
        vad_analyzer: Optional[VADAnalyzer] = None,
        input_name: str | None = None,
        output_name: str | None = None,
        bot_stopped_speaking_delay: float = 2.5,
    ):
        super().__init__(input_name=input_name, output_name=output_name)

        self._websocket_url = websocket_url
        self._session_id = session_id
        self._api_key = api_key
        self._package_name = package_name
        self._enable_transcription = enable_transcription
        self._app_session: Optional[AppSession] = None
        self._bot_stopped_speaking_delay = bot_stopped_speaking_delay

        # Create transport params
        params = TransportParams(
            vad_analyzer=vad_analyzer,
            audio_out_enabled=True  # Enable audio output
        )

        # Create input/output transports
        self._input_transport = MentraOSInputTransport(self, params)
        self._output_transport = MentraOSOutputTransport(self, params, bot_stopped_speaking_delay=bot_stopped_speaking_delay)

        # Tasks
        self._receive_task: Optional[asyncio.Task] = None
        self._running = False

        # Register supported event handlers
        self._register_event_handler("on_client_connected")
        self._register_event_handler("on_client_disconnected")
        self._register_event_handler("on_session_timeout")

    def input(self) -> MentraOSInputTransport:
        """Return the input transport."""
        return self._input_transport

    def output(self) -> MentraOSOutputTransport:
        """Return the output transport."""
        return self._output_transport

    async def start(self, frame: StartFrame):
        """Start the transport and connect to MentraOS."""
        # Prevent multiple starts
        if hasattr(self, '_started') and self._started:
            return
        self._started = True
        
        logger.debug(f"MentraOSWebSocketTransport.start() called for session {self._session_id}")

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

        # Call disconnected event handler
        await self._call_event_handler("on_client_disconnected", self._app_session)

    async def _connect_session(self):
        """Connect to MentraOS via AppSession."""
        logger.debug(f"_connect_session called for {self._session_id}")

        # Extract user_id from session_id (format: userId-packageName)
        parts = self._session_id.split("-")
        user_id = parts[0] if parts else "unknown"

        # Create AppSession
        self._app_session = AppSession(
            session_id=self._session_id,
            user_id=user_id,
            package_name=self._package_name,
            api_key=self._api_key,
            websocket_url=self._websocket_url,
            server=None,  # No server needed for standalone session
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

            # Call connected event handler
            await self._call_event_handler("on_client_connected", self._app_session)

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
                    audio=event.audio_data, sample_rate=16000, num_channels=1
                )

                # Perform VAD analysis if analyzer is available
                if self._input_transport._params.vad_analyzer:
                    vad_state = await self._input_transport._vad_analyze(frame)
                    await self._input_transport._handle_vad(frame, vad_state)

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
                        timestamp=event.timestamp.isoformat(),
                    )
                    await self._input_transport.push_frame(frame)
                else:
                    # Interim transcription
                    frame = InterimTranscriptionFrame(
                        text=event.text,
                        user_id=self._session_id,
                        timestamp=event.timestamp.isoformat(),
                    )
                    await self._input_transport.push_frame(frame)

        @self._app_session.events.on_event
        async def handle_any_event(event):
            """Handle other events."""
            if event.type == EventType.APP_STOPPED.value:
                logger.info("App stopped event received")
                await self._push_error("Session stopped by MentraOS")
                # Call timeout event handler
                await self._call_event_handler("on_session_timeout", self._app_session)
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
    
    async def write_audio_frame(self, frame: OutputAudioRawFrame):
        """Write audio frame - this is called by the MediaSender in BaseOutputTransport.
        
        We delegate to our output transport which will handle MP3 encoding for TTS frames.
        """
        # Our MentraOSOutputTransport has the write_audio_frame method that handles TTS
        await self._output_transport.write_audio_frame(frame)
