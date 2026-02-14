"""Meeting post-processing: chunked transcription + AI meeting minutes."""

from __future__ import annotations

import io
import time
import wave
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import numpy as np

if TYPE_CHECKING:
    from localwhispr.config import MeetingConfig, OllamaConfig, WhisperConfig
    from localwhispr.meeting import MeetingFiles
    from localwhispr.transcriber import Transcriber

# Chunk size for transcription (5 minutes in samples at 16kHz)
CHUNK_DURATION_S = 300  # 5 minutes
# Word threshold for incremental summary
SUMMARY_WORD_LIMIT = 3000


def process_meeting(
    files: "MeetingFiles",
    whisper_config: "WhisperConfig",
    ollama_config: "OllamaConfig",
    meeting_config: "MeetingConfig",
    transcriber: "Transcriber | None" = None,
) -> dict[str, Path]:
    """Full pipeline: transcription + meeting minutes. Returns paths of generated files."""
    output_dir = files.output_dir
    results: dict[str, Path] = {}

    # 1. Transcription
    print("[localwhispr] Starting meeting transcription...")
    t0 = time.time()
    transcription = transcribe_meeting(files.combined_wav, whisper_config, transcriber)
    elapsed = time.time() - t0

    if not transcription:
        print("[localwhispr] No audio transcribed from meeting.")
        return results

    # Save transcription
    transcription_path = output_dir / "transcription.md"
    header = (
        f"# Meeting Transcription\n\n"
        f"**Date**: {files.started_at.strftime('%Y-%m-%d %H:%M')}\n"
        f"**Duration**: {_format_duration(files.duration_seconds)}\n"
        f"**Transcription time**: {elapsed:.1f}s\n\n---\n\n"
    )
    transcription_path.write_text(header + transcription, encoding="utf-8")
    results["transcription"] = transcription_path
    print(f"[localwhispr] Transcription saved: {transcription_path}")

    # 2. Meeting minutes / Summary with AI
    print("[localwhispr] Generating meeting minutes with AI...")
    summary = generate_summary(transcription, ollama_config, meeting_config)

    if summary:
        summary_path = output_dir / "summary.md"
        summary_header = (
            f"# Meeting Minutes\n\n"
            f"**Date**: {files.started_at.strftime('%Y-%m-%d %H:%M')}\n"
            f"**Duration**: {_format_duration(files.duration_seconds)}\n\n---\n\n"
        )
        summary_path.write_text(summary_header + summary, encoding="utf-8")
        results["summary"] = summary_path
        print(f"[localwhispr] Minutes saved: {summary_path}")
    else:
        print("[localwhispr] WARNING: could not generate meeting minutes.")

    return results


def transcribe_meeting(
    wav_path: Path,
    whisper_config: "WhisperConfig",
    transcriber: "Transcriber | None" = None,
) -> str:
    """Transcribe long audio in chunks with timestamps."""
    if not wav_path.exists() or wav_path.stat().st_size < 1000:
        return ""

    # Read the full audio
    with wave.open(str(wav_path), "rb") as wf:
        sample_rate = wf.getframerate()
        n_frames = wf.getnframes()
        raw_data = wf.readframes(n_frames)

    audio = np.frombuffer(raw_data, dtype=np.int16)
    total_duration = len(audio) / sample_rate
    chunk_samples = CHUNK_DURATION_S * sample_rate

    print(f"[localwhispr] Audio: {total_duration:.0f}s ({total_duration/60:.1f} min)")

    # Reuse model already loaded by the daemon, or load a new one
    if transcriber:
        model = transcriber._ensure_model()
        print("[localwhispr] Reusing Whisper model from daemon")
    else:
        from faster_whisper import WhisperModel
        print(f"[localwhispr] Loading Whisper '{whisper_config.model}'...")
        model = WhisperModel(
            whisper_config.model,
            device=whisper_config.device,
            compute_type=whisper_config.compute_type,
        )

    # Transcribe in chunks
    parts: list[str] = []
    n_chunks = max(1, int(np.ceil(len(audio) / chunk_samples)))

    for i in range(n_chunks):
        start_sample = i * chunk_samples
        end_sample = min((i + 1) * chunk_samples, len(audio))
        chunk = audio[start_sample:end_sample]

        chunk_start_time = start_sample / sample_rate
        timestamp = _format_duration(chunk_start_time)

        print(f"[localwhispr] Transcribing chunk {i+1}/{n_chunks} [{timestamp}]...")

        # Convert chunk to in-memory WAV
        wav_buf = io.BytesIO()
        with wave.open(wav_buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(chunk.tobytes())
        wav_buf.seek(0)

        segments, _info = model.transcribe(
            wav_buf,
            language=whisper_config.language if whisper_config.language else None,
            beam_size=5,
            vad_filter=True,
            vad_parameters=dict(
                min_silence_duration_ms=500,
                speech_pad_ms=300,
            ),
        )

        chunk_text_parts: list[str] = []
        for segment in segments:
            # Absolute timestamp = chunk offset + segment timestamp
            abs_start = chunk_start_time + segment.start
            ts_str = _format_duration(abs_start)
            chunk_text_parts.append(f"[{ts_str}] {segment.text.strip()}")

        if chunk_text_parts:
            parts.extend(chunk_text_parts)

    return "\n\n".join(parts)


def generate_summary(
    transcription: str,
    ollama_config: "OllamaConfig",
    meeting_config: "MeetingConfig",
) -> str:
    """Generate meeting minutes/summary via Ollama."""
    word_count = len(transcription.split())
    print(f"[localwhispr] Transcription: {word_count} words")

    if word_count <= SUMMARY_WORD_LIMIT:
        # Fits in a single call
        return _ollama_summarize(transcription, ollama_config, meeting_config)
    else:
        # Incremental summary: split into blocks, summarize each, then meta-summary
        return _incremental_summary(transcription, ollama_config, meeting_config)


def _ollama_summarize(
    text: str,
    ollama_config: "OllamaConfig",
    meeting_config: "MeetingConfig",
) -> str:
    """Send text to Ollama for summary generation."""
    base_url = ollama_config.base_url.rstrip("/")
    model = meeting_config.summary_model
    prompt = meeting_config.summary_prompt

    try:
        response = httpx.post(
            f"{base_url}/api/generate",
            json={
                "model": model,
                "prompt": f"{prompt}\n\nMeeting transcription:\n\n{text}",
                "stream": False,
                "options": {
                    "temperature": 0.3,
                    "num_predict": 4096,
                },
            },
            timeout=120.0,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("response", "").strip()

    except httpx.ConnectError:
        print("[localwhispr] ERROR: Could not connect to Ollama.")
        return ""
    except Exception as e:
        print(f"[localwhispr] ERROR generating meeting minutes: {e}")
        return ""


def _incremental_summary(
    transcription: str,
    ollama_config: "OllamaConfig",
    meeting_config: "MeetingConfig",
) -> str:
    """Summarize long transcriptions in blocks and then create a meta-summary."""
    # Split into ~2500 word blocks
    words = transcription.split()
    block_size = 2500
    blocks: list[str] = []

    for i in range(0, len(words), block_size):
        block = " ".join(words[i:i + block_size])
        blocks.append(block)

    print(f"[localwhispr] Incremental summary: {len(blocks)} blocks")

    # Summarize each block
    partial_summaries: list[str] = []
    for idx, block in enumerate(blocks):
        print(f"[localwhispr] Summarizing block {idx+1}/{len(blocks)}...")
        summary = _ollama_summarize(block, ollama_config, meeting_config)
        if summary:
            partial_summaries.append(f"## Part {idx+1}\n\n{summary}")

    if not partial_summaries:
        return ""

    # If only one block, return directly
    if len(partial_summaries) == 1:
        return partial_summaries[0]

    # Meta-summary: combine partial summaries
    combined = "\n\n---\n\n".join(partial_summaries)
    print("[localwhispr] Generating meta-summary...")

    meta_prompt = (
        "You received partial summaries of a long meeting. "
        "Combine them into a single coherent summary, keeping the format:\n"
        "1. SUMMARY\n2. DECISIONS\n3. ACTION ITEMS\n4. TOPICS\n"
        "Eliminate redundancies and organize chronologically. "
        "IMPORTANT: Respond in the SAME LANGUAGE as the transcription."
    )

    base_url = ollama_config.base_url.rstrip("/")
    try:
        response = httpx.post(
            f"{base_url}/api/generate",
            json={
                "model": meeting_config.summary_model,
                "prompt": f"{meta_prompt}\n\nPartial summaries:\n\n{combined}",
                "stream": False,
                "options": {
                    "temperature": 0.3,
                    "num_predict": 4096,
                },
            },
            timeout=120.0,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("response", "").strip()
    except Exception as e:
        print(f"[localwhispr] ERROR in meta-summary: {e}")
        # Return partial summaries as fallback
        return combined


def _format_duration(seconds: float) -> str:
    """Format seconds as HH:MM:SS."""
    td = timedelta(seconds=int(seconds))
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"
