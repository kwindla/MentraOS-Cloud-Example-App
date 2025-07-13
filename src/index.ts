import { AppServer, AppSession, ViewType } from '@mentra/sdk';
import WebSocket from 'ws';


const PACKAGE_NAME = process.env.PACKAGE_NAME ?? (() => { throw new Error('PACKAGE_NAME is not set in .env file'); })();
const MENTRAOS_API_KEY = process.env.MENTRAOS_API_KEY ?? (() => { throw new Error('MENTRAOS_API_KEY is not set in .env file'); })();
const PORT = parseInt(process.env.PORT || '3000');

class ExampleMentraOSApp extends AppServer {

  constructor() {
    // Call base constructor with debug logging enabled

    super({
      packageName: PACKAGE_NAME,
      apiKey: MENTRAOS_API_KEY,
      port: PORT,
    });

    // Patch websocket send for logging
    this.patchSend();
  }

  protected async onSession(session: AppSession, sessionId: string, userId: string): Promise<void> {
    console.log('Session started:', sessionId);



    // Show welcome message
    session.layouts.showTextWall("Example App is ready (audio?)!");

    session.events.onAudioChunk((data) => {
      // console.log('Audio chunk');
    })

    // Handle real-time transcription
    // requires microphone permission to be set in the developer console
    session.events.onTranscription(async (data) => {
      console.log('Transcription event:', data);
      if (data.isFinal) {
        session.layouts.showTextWall("You said: " + data.text, {
          view: ViewType.MAIN,
          durationMs: 3000
        });

        // session.audio.speak(data.text);

        if (data.text.toLowerCase().startsWith("hello")) {
          console.log("!!! PLAYING TEST FILE !!!");
          try {
            const result = await session.audio.playAudio({
              audioUrl: "https://36bb7859725a.ngrok.app/audio-stream/sine-15"
            });
            console.log('Audio played:', result);
          } catch (error) {
            console.error('Error playing audio:', error);
          }
        }
      }
    })

    session.events.onGlassesBattery((data) => {
      console.log('Glasses battery:', data);
    })
  }

  /**
   * Monkey-patch AppSession.send so every outbound WS message is printed.
   */
  private patchSend(): void {
    const proto = AppSession.prototype as any;
    if (proto.__sendPatched) return;

    const original = proto.send;
    proto.send = function patchedSend(this: AppSession, msg: unknown) {
      console.debug('WS →', msg);
      return original.call(this, msg);
    };

    proto.__sendPatched = true;

    // Patch WebSocket 'message' events for inbound logging
    const wsProto = (WebSocket as any).prototype;
    if (!wsProto.__messagePatched) {
      const originalEmit = wsProto.emit;
      wsProto.emit = function patchedEmit(this: WebSocket, event: string, ...args: unknown[]) {
        if (event === 'message') {
          console.debug('WS ←', args[0]);
        }
        return originalEmit.call(this, event, ...args);
      };
      wsProto.__messagePatched = true;
    }
  }
}

// Start the server
// DEV CONSOLE URL: https://console.mentra.glass/
// Get your webhook URL from ngrok (or whatever public URL you have)
const app = new ExampleMentraOSApp();

app.start().catch(console.error);