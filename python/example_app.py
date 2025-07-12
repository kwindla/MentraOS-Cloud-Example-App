"""Example MentraOS app using the Python SDK."""

import asyncio
import os
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

from mentraos import AppServer, AppSession
from mentraos.layouts import ViewType
from mentraos.protocol.subscriptions import Subscription

# Load environment variables
load_dotenv()


class ExampleMentraOSApp(AppServer):
    """Example app demonstrating MentraOS Python SDK usage."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._audio_files = {}  # Track open audio files by session_id
    
    async def stop(self):
        """Stop the app and cleanup resources."""
        # Close any remaining audio files
        for session_id, audio_file in list(self._audio_files.items()):
            try:
                audio_file.close()
                print(f"Closed audio file for session {session_id} during shutdown")
            except Exception as e:
                print(f"Error closing audio file for {session_id}: {e}")
        self._audio_files.clear()
        
        # Call parent stop method
        await super().stop()
    
    async def on_session(
        self,
        session: AppSession,
        session_id: str,
        user_id: str
    ) -> None:
        """Handle new session."""
        print(f"Session started: {session_id}")
        
        # Create audio output directory
        audio_dir = Path("audio_captures")
        audio_dir.mkdir(exist_ok=True)
        
        # Create timestamped audio file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        audio_filename = audio_dir / f"mentraos_audio_{user_id}_{timestamp}.raw"
        audio_file = open(audio_filename, "wb")
        self._audio_files[session_id] = audio_file
        print(f"Recording audio to: {audio_filename}")
        print("Note: Raw audio format is likely 16kHz, 16-bit PCM, mono")
        print("To play: ffplay -f s16le -ar 16000 -ac 1 <filename>")
        print("To convert: ffmpeg -f s16le -ar 16000 -ac 1 -i <filename> output.wav")
        
        # Track audio stats
        total_bytes = 0
        chunk_count = 0
        
        # Show welcome message
        await session.layouts.show_text_wall("Python SDK Ready! Recording audio...")
        
        # Subscribe to events
        await session.subscribe(Subscription.AUDIO_CHUNK)
        await session.subscribe(Subscription.TRANSCRIPTION)
        await session.subscribe(Subscription.GLASSES_BATTERY_UPDATE)
        
        # Handle audio chunks
        @session.events.on_audio_chunk
        async def handle_audio(event):
            nonlocal total_bytes, chunk_count
            chunk_count += 1
            total_bytes += len(event.audio_data)
            
            # Write raw audio data to file
            audio_file.write(event.audio_data)
            audio_file.flush()  # Ensure data is written immediately
            
            # Log progress every 10 chunks
            if chunk_count % 10 == 0:
                print(f"Audio chunks: {chunk_count}, Total bytes: {total_bytes}, Avg chunk size: {total_bytes/chunk_count:.1f}")
            else:
                print(f"Audio chunk received: {len(event.audio_data)} bytes")
        
        # Debug: Log all events
        @session.events.on_event
        async def handle_any_event(event):
            print(f"Event received: {event.type} at {event.timestamp}")
        
        # Handle transcriptions
        @session.events.on_transcription
        async def handle_transcription(event):
            print(f"Transcription: {event.text} (final: {event.is_final})")
            
            if event.is_final:
                # Display transcription on glasses
                await session.layouts.show_text_wall(
                    f"You said: {event.text}",
                    view=ViewType.MAIN,
                    duration_ms=3000
                )
        
        # Handle battery updates
        @session.events.on_battery_update
        async def handle_battery(event):
            print(f"Battery: {event.level}% (charging: {event.is_charging})")
            
            # Show low battery warning
            if event.level < 20 and not event.is_charging:
                await session.layouts.show_notification(
                    "Low Battery",
                    f"Battery at {event.level}%",
                    duration_ms=5000
                )
    
    async def on_session_end(
        self,
        session: AppSession,
        session_id: str,
        user_id: str
    ) -> None:
        """Handle session end."""
        print(f"Session ended: {session_id}")
        
        # Close audio file if open
        if session_id in self._audio_files:
            audio_file = self._audio_files[session_id]
            audio_file.close()
            del self._audio_files[session_id]
            print(f"Closed audio file for session {session_id}")


async def main():
    """Run the example app."""
    # Create and start the app
    app = ExampleMentraOSApp()
    
    # Uvicorn handles shutdown gracefully, so we just need to start it
    await app.start()


if __name__ == "__main__":
    # Run the app
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        # These are expected during shutdown
        print("\nShutdown complete")
        pass