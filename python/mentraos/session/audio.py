"""Audio module for MentraOS sessions."""

import uuid
from typing import Optional
from dataclasses import dataclass
from urllib.parse import urlencode

from typing import TYPE_CHECKING
from loguru import logger

from ..protocol.cloud_messages import AppToCloudMessageType

if TYPE_CHECKING:
    from ..core.app_session import AppSession


@dataclass
class VoiceSettings:
    """Voice settings for text-to-speech."""

    stability: Optional[float] = None
    similarity_boost: Optional[float] = None
    style: Optional[float] = None
    use_speaker_boost: Optional[bool] = None
    speed: Optional[float] = None


@dataclass
class SpeakOptions:
    """Options for the speak method."""

    voice_id: Optional[str] = None
    model_id: Optional[str] = None
    voice_settings: Optional[VoiceSettings] = None
    volume: float = 1.0


@dataclass
class AudioPlayResult:
    """Result from audio playback request."""

    request_id: str
    success: bool
    error: Optional[str] = None


class AudioManager:
    """Manages audio functionality for MentraOS sessions."""

    def __init__(self, session: "AppSession"):
        """Initialize the audio manager.

        Args:
            session: The app session instance
        """
        self._session = session

    async def speak(self, text: str, options: Optional[SpeakOptions] = None) -> AudioPlayResult:
        """Convert text to speech and play it on the glasses.

        Args:
            text: The text to speak
            options: Optional speaking configuration

        Returns:
            AudioPlayResult with request ID and status

        Example:
            ```python
            # Basic usage
            result = await session.audio.speak("Hello, world!")

            # With custom voice settings
            result = await session.audio.speak(
                "This uses custom voice settings",
                SpeakOptions(
                    voice_id="your_elevenlabs_voice_id",
                    model_id="eleven_flash_v2_5",
                    voice_settings=VoiceSettings(
                        stability=0.7,
                        similarity_boost=0.8,
                        style=0.3,
                        speed=0.9
                    ),
                    volume=0.8
                )
            )
            ```
        """
        if not self._session.is_connected:
            return AudioPlayResult(request_id="", success=False, error="Session not connected")

        # Use default options if not provided
        if options is None:
            options = SpeakOptions()

        # Generate request ID
        request_id = str(uuid.uuid4())

        try:
            # Build TTS URL parameters
            params = {
                "text": text,
            }

            # Add optional parameters
            if options.voice_id:
                params["voiceId"] = options.voice_id

            if options.model_id:
                params["modelId"] = options.model_id
            else:
                params["modelId"] = "eleven_flash_v2_5"  # Default model

            # Add voice settings
            if options.voice_settings:
                settings = options.voice_settings
                if settings.stability is not None:
                    params["stability"] = str(settings.stability)
                if settings.similarity_boost is not None:
                    params["similarityBoost"] = str(settings.similarity_boost)
                if settings.style is not None:
                    params["style"] = str(settings.style)
                if settings.use_speaker_boost is not None:
                    params["useSpeakerBoost"] = str(settings.use_speaker_boost).lower()
                if settings.speed is not None:
                    params["speed"] = str(settings.speed)

            # Build TTS URL
            # Note: In production, this should use the actual server URL
            # The server URL is typically available from the session configuration
            server_url = (
                self._session.server_url
                if hasattr(self._session, "server_url")
                else "https://api.mentraos.com"
            )
            tts_url = f"{server_url}/api/tts?{urlencode(params)}"

            # Log the TTS URL for debugging
            logger.info(f"🔊 TTS Request URL: {tts_url}")

            # Send audio play request - format matches JavaScript SDK
            message = {
                "type": AppToCloudMessageType.AUDIO_PLAY_REQUEST.value,
                "packageName": self._session.package_name,
                "sessionId": self._session.session_id,
                "requestId": request_id,
                "audioUrl": tts_url,
                "volume": options.volume,
                "stopOtherAudio": True,
            }

            # Send the message
            await self._session.send(message)

            logger.debug(f"📤 Sent TTS message: {message}")
            logger.info(
                f'🎤 Speaking: "{text[:100]}{"..." if len(text) > 100 else ""}" (request_id: {request_id})'
            )

            return AudioPlayResult(request_id=request_id, success=True)

        except Exception as e:
            logger.error(f"Failed to send speak request: {e}")
            return AudioPlayResult(request_id=request_id, success=False, error=str(e))

    async def play_audio(
        self, audio_url: str, volume: float = 1.0, stop_other_audio: bool = True
    ) -> AudioPlayResult:
        """Play audio from a URL on the glasses.

        Args:
            audio_url: URL of the audio file to play
            volume: Playback volume (0.0-1.0)
            stop_other_audio: Whether to stop other audio first

        Returns:
            AudioPlayResult with request ID and status
        """
        if not self._session.is_connected:
            return AudioPlayResult(request_id="", success=False, error="Session not connected")

        request_id = str(uuid.uuid4())

        try:
            message = {
                "type": AppToCloudMessageType.AUDIO_PLAY_REQUEST.value,
                "packageName": self._session.package_name,
                "sessionId": self._session.session_id,
                "requestId": request_id,
                "audioUrl": audio_url,
                "volume": volume,
                "stopOtherAudio": stop_other_audio,
            }

            await self._session.send(message)

            logger.debug(f"Sent audio play request: {audio_url} (request_id: {request_id})")

            return AudioPlayResult(request_id=request_id, success=True)

        except Exception as e:
            logger.error(f"Failed to send audio play request: {e}")
            return AudioPlayResult(request_id=request_id, success=False, error=str(e))
