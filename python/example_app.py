"""Example MentraOS app using the Python SDK."""

import asyncio
import os
from dotenv import load_dotenv

from mentraos import AppServer, AppSession
from mentraos.layouts import ViewType
from mentraos.protocol.subscriptions import Subscription

# Load environment variables
load_dotenv()


class ExampleMentraOSApp(AppServer):
    """Example app demonstrating MentraOS Python SDK usage."""
    
    async def on_session(
        self,
        session: AppSession,
        session_id: str,
        user_id: str
    ) -> None:
        """Handle new session."""
        print(f"Session started: {session_id}")
        
        # Show welcome message
        await session.layouts.show_text_wall("Python SDK Ready!")
        
        # Subscribe to events
        await session.subscribe(Subscription.AUDIO_CHUNK)
        await session.subscribe(Subscription.TRANSCRIPTION)
        await session.subscribe(Subscription.GLASSES_BATTERY_UPDATE)
        
        # Handle audio chunks
        @session.events.on_audio_chunk
        async def handle_audio(event):
            print(f"Audio chunk received: {len(event.audio_data)} bytes")
        
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


async def main():
    """Run the example app."""
    # Create and start the app
    app = ExampleMentraOSApp()
    
    try:
        await app.start()
    except KeyboardInterrupt:
        print("\nShutting down...")
        await app.stop()


if __name__ == "__main__":
    # Run the app
    asyncio.run(main())