"""Text polishing via Ollama LLM."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from localwhispr.config import OllamaConfig


class AICleanup:
    """Uses Ollama to clean and polish transcribed text."""

    def __init__(self, config: OllamaConfig | None = None) -> None:
        from localwhispr.config import OllamaConfig as OC

        cfg = config or OC()
        self._base_url = cfg.base_url.rstrip("/")
        self._model = cfg.cleanup_model
        self._prompt = cfg.cleanup_prompt

    def cleanup(self, raw_text: str) -> str:
        """Send raw text to Ollama and return polished text."""
        if not raw_text.strip():
            return ""

        try:
            response = httpx.post(
                f"{self._base_url}/api/generate",
                json={
                    "model": self._model,
                    "prompt": f"{self._prompt}\n\nTranscribed text:\n{raw_text}",
                    "stream": False,
                    "options": {
                        "temperature": 0.1,
                        "num_predict": 2048,
                    },
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            cleaned = data.get("response", "").strip()

            if cleaned:
                print(f"[localwhispr] AI cleanup: {cleaned[:100]}...")
                return cleaned

            # Fallback: return original text if AI returns empty
            return raw_text

        except httpx.ConnectError:
            print("[localwhispr] ERROR: Could not connect to Ollama. Is it running?")
            print(f"[localwhispr] URL: {self._base_url}")
            return raw_text
        except Exception as e:
            print(f"[localwhispr] ERROR in AI cleanup: {e}")
            return raw_text

    _CONVERSATION_PROMPT = (
        "You are a conversation transcription polishing assistant.\n"
        "The text contains labels [Me] and [Other] indicating who spoke.\n"
        "Rules:\n"
        "- KEEP the labels [Me] and [Other] exactly as they are\n"
        "- Remove hesitations (uh, uhm, hmm, eh, like, you know, so, well, tipo, né, então, assim)\n"
        "- Add correct punctuation\n"
        "- Fix obvious transcription errors\n"
        "- Keep the original meaning intact\n"
        "- ALWAYS respond in the SAME LANGUAGE as the input text\n"
        "- Respond ONLY with the cleaned text, no explanations or preambles."
    )

    def cleanup_conversation(self, labeled_text: str) -> str:
        """Polish conversation with [Me]/[Other] labels, keeping the labels."""
        if not labeled_text.strip():
            return ""

        try:
            response = httpx.post(
                f"{self._base_url}/api/generate",
                json={
                    "model": self._model,
                    "prompt": f"{self._CONVERSATION_PROMPT}\n\nTranscription:\n{labeled_text}",
                    "stream": False,
                    "options": {
                        "temperature": 0.1,
                        "num_predict": 4096,
                    },
                },
                timeout=45.0,
            )
            response.raise_for_status()
            data = response.json()
            cleaned = data.get("response", "").strip()

            if cleaned:
                print(f"[localwhispr] AI cleanup conversation: {cleaned[:100]}...")
                return cleaned

            return labeled_text

        except httpx.ConnectError:
            print("[localwhispr] ERROR: Could not connect to Ollama.")
            return labeled_text
        except Exception as e:
            print(f"[localwhispr] ERROR in conversation cleanup: {e}")
            return labeled_text
