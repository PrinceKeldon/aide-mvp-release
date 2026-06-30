import asyncio
import logging
import queue
import numpy as np
import sounddevice as sd
from typing import Optional, Callable
from faster_whisper import WhisperModel
from core.settings import settings

logger = logging.getLogger("aide.voice")


class NativeVoiceListener:
    """
    System-native implementation of the OnIt voice pipeline.
    Implements a real-time streaming transcription model.
    """

    def __init__(self, on_transcript_callback: Callable[[str], asyncio.Future]):
        self.on_transcript = on_transcript_callback
        self.is_listening = False
        self.is_recording = False
        self.session_transcript = ""

        # Load Whisper model locally
        logger.info("Loading Faster-Whisper model for real-time stream...")
        self.model = WhisperModel("small", device="cpu", compute_type="int8")

        # Audio settings
        self.sample_rate = 16000
        self.audio_queue = queue.Queue()
        self.audio_buffer = []

        # VAD / Segment settings
        self.min_segment_len = 1.5  # seconds
        self.max_segment_len = 10.0  # seconds
        self.silence_threshold = 0.01  # energy threshold for silence

    def _audio_callback(self, indata, frames, time, status):
        if status:
            logger.warning(f"Audio callback status: {status}")
        if self.is_recording:
            self.audio_queue.put(indata.copy())

    async def start_listening(self):
        self.is_listening = True
        logger.info("Native Voice Listener active (System-wide)")
        try:
            with sd.InputStream(
                samplerate=self.sample_rate, channels=1, callback=self._audio_callback
            ):
                while self.is_listening:
                    await asyncio.sleep(1)
        except Exception as exc:
            self.is_listening = False
            logger.warning(f"Native voice listener unavailable: {exc}")

    async def trigger_session(self, action: str, task_name: Optional[str] = None):
        if action == "start":
            logger.info(f"Starting native voice session: {task_name or 'unnamed task'}")
            self.is_recording = True
            self.session_transcript = ""
            self.audio_buffer = []
        elif action == "stop":
            logger.info("Stopping native voice session. Finalizing...")
            self.is_recording = False
            # Process any remaining audio in the buffer
            await self._process_segment()
            if self.session_transcript:
                await self.on_transcript(self.session_transcript)

    def _is_silent(self, audio_data: np.ndarray) -> bool:
        """Check if the end of the audio buffer is silent."""
        if len(audio_data) == 0:
            return True
        # Check the last 0.3 seconds
        check_len = int(self.sample_rate * 0.3)
        chunk = audio_data[-check_len:] if len(audio_data) > check_len else audio_data
        rms = np.sqrt(np.mean(chunk**2))
        return rms < self.silence_threshold

    async def _process_segment(self):
        """Transcribe the current buffer and append to session transcript."""
        if not self.audio_buffer:
            return

        audio_data = np.concatenate(self.audio_buffer).flatten()
        self.audio_buffer = []  # Clear buffer immediately to avoid double processing

        segments, _ = self.model.transcribe(audio_data, beam_size=5)
        text = " ".join([segment.text for segment in segments]).strip()

        if text:
            self.session_transcript += " " + text if self.session_transcript else text
            logger.info(f"Real-time segment captured: {text}")
            # Optional: we could call self.on_transcript(self.session_transcript) here for real-time UI updates

    async def run_loop(self):
        """Background loop to manage audio and trigger real-time transcription segments."""
        while True:
            if self.is_recording:
                try:
                    # Drain queue into buffer
                    while not self.audio_queue.empty():
                        self.audio_buffer.append(self.audio_queue.get_nowait())

                    # Check if we should process a segment
                    if self.audio_buffer:
                        duration = (
                            len(np.concatenate(self.audio_buffer)) / self.sample_rate
                        )

                        if duration >= self.max_segment_len:
                            await self._process_segment()
                        elif duration >= self.min_segment_len and self._is_silent(
                            np.concatenate(self.audio_buffer).flatten()
                        ):
                            await self._process_segment()

                except Exception as e:
                    logger.error(f"Error in voice run_loop: {e}")

            await asyncio.sleep(0.1)

    def stop_listening(self):
        self.is_listening = False
