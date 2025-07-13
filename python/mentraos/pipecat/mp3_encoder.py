"""MP3 encoder for TTS audio streaming."""

import os
from pathlib import Path
from datetime import datetime
from typing import Optional
import numpy as np

try:
    import lameenc
except ImportError:
    lameenc = None

from loguru import logger


class MP3StreamEncoder:
    """Handles MP3 encoding for TTS audio streams."""
    
    def __init__(self, output_dir: str = "audio-generation"):
        """Initialize MP3 encoder.
        
        Args:
            output_dir: Directory to write audio files
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        logger.info(f"🎵 MP3StreamEncoder initialized with output_dir: {self.output_dir.absolute()}")
        
        self.encoder: Optional[lameenc.Encoder] = None
        self.current_file: Optional[Path] = None
        self.file_handle = None
        self.sample_rate: Optional[int] = None
        self.num_channels: Optional[int] = None
        
        if lameenc is None:
            logger.error("lameenc not installed - MP3 encoding will not work!")
    
    def start_new_stream(self, sample_rate: int, num_channels: int) -> Optional[str]:
        """Start a new MP3 stream with a timestamped filename.
        
        Args:
            sample_rate: Audio sample rate in Hz
            num_channels: Number of audio channels
            
        Returns:
            The filename stem (without extension) if successful, None otherwise
        """
        if lameenc is None:
            logger.error("Cannot start MP3 stream - lameenc not installed")
            return None
            
        try:
            # Generate timestamp-based filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]  # Include milliseconds
            filename_stem = f"tts_{timestamp}"
            self.current_file = self.output_dir / f"{filename_stem}.generating"
            
            logger.info(f"🎵 Starting new MP3 stream: {self.current_file}")
            
            # Initialize encoder
            self.encoder = lameenc.Encoder()
            self.encoder.set_bit_rate(64)  # 64 kbps as per requirements
            self.encoder.set_in_sample_rate(sample_rate)
            self.encoder.set_channels(num_channels)
            self.encoder.set_quality(2)  # High quality
            
            self.sample_rate = sample_rate
            self.num_channels = num_channels
            
            # Open file for writing
            self.file_handle = open(self.current_file, 'wb')
            logger.info(f"📂 Opened file for writing: {self.current_file.absolute()}")
            
            return filename_stem
            
        except Exception as e:
            logger.error(f"Failed to start MP3 stream: {e}")
            self.cleanup()
            return None
    
    def write_audio_chunk(self, audio_data: bytes) -> bool:
        """Write audio chunk to the current stream.
        
        Args:
            audio_data: Raw PCM audio bytes (16-bit signed)
            
        Returns:
            True if successful, False otherwise
        """
        if not self.encoder or not self.file_handle:
            logger.error("No active MP3 stream")
            return False
            
        try:
            # Ensure even number of bytes for 16-bit samples
            if len(audio_data) % 2 != 0:
                logger.warning(f"Odd number of audio bytes ({len(audio_data)}), truncating")
                audio_data = audio_data[:-1]
            
            # Encode to MP3
            mp3_data = self.encoder.encode(audio_data)
            
            if mp3_data:
                self.file_handle.write(bytes(mp3_data))
                self.file_handle.flush()  # Ensure data is written immediately
                logger.info(f"✍️ Wrote {len(mp3_data)} MP3 bytes ({len(audio_data)} PCM bytes) to {self.current_file}")
                return True
            else:
                logger.warning(f"⚠️ No MP3 data returned from encoder for {len(audio_data)} PCM bytes")
            
            return False
            
        except Exception as e:
            logger.error(f"Failed to write audio chunk: {e}")
            return False
    
    def finalize_stream(self) -> bool:
        """Finalize the current stream and rename the file.
        
        Returns:
            True if successful, False otherwise
        """
        if not self.encoder or not self.file_handle or not self.current_file:
            logger.warning("No active stream to finalize")
            return False
            
        try:
            # Flush encoder
            mp3_data = self.encoder.flush()
            if mp3_data:
                self.file_handle.write(bytes(mp3_data))
                logger.debug(f"Wrote {len(mp3_data)} final MP3 bytes")
            
            # Close file
            self.file_handle.close()
            self.file_handle = None
            
            # Rename file to remove .generating extension
            final_file = self.current_file.with_suffix('')  # Remove .generating
            self.current_file.rename(final_file)
            
            logger.info(f"✅ Finalized MP3 stream: {final_file}")
            
            # Reset state
            self.encoder = None
            self.current_file = None
            self.sample_rate = None
            self.num_channels = None
            logger.info("🔄 MP3 encoder state reset after finalization")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to finalize stream: {e}")
            self.cleanup()
            return False
    
    def cleanup(self):
        """Clean up resources without finalizing."""
        try:
            if self.file_handle:
                self.file_handle.close()
                self.file_handle = None
            
            self.encoder = None
            self.current_file = None
            self.sample_rate = None
            self.num_channels = None
            
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")