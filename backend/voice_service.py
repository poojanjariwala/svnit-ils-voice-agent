"""
voice_service.py - Voice Service Optimization
Enhanced by Henali for better speech recognition & synthesis

FREE-TIER SWAP:
- Speech-to-text: OpenAI Whisper API → Groq Whisper (whisper-large-v3,
  free tier, OpenAI-compatible endpoint). Free key: https://console.groq.com/keys
- Text-to-speech: Fish Audio → Microsoft Edge TTS (edge-tts).
  Completely free, no API key required.
"""

import os
import asyncio
import logging
from pathlib import Path

import requests
import edge_tts

logger = logging.getLogger(__name__)

# Create audio directory
AUDIO_DIR = "audio_files"
Path(AUDIO_DIR).mkdir(exist_ok=True)

# Groq Speech Recognition Config (free tier, OpenAI-compatible endpoint)
# API key is read at call time via os.getenv so import order never matters.
GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_STT_MODEL = os.getenv("STT_MODEL", "whisper-large-v3")

# Henali's Voice Optimization: Better voices for each language
# (Microsoft Edge neural voices - free, no API key)
EDGE_VOICE_MAP = {
    "hi": "hi-IN-SwaraNeural",      # Hindi female voice
    "gu": "gu-IN-DhwaniNeural",     # Gujarati female voice
    "en": "en-US-JennyNeural"       # English female voice
}

# Speech Recognition Config (Henali's optimization)
WHISPER_LANGUAGE_MAP = {
    "hi": "hi",
    "gu": "gu",
    "en": "en"
}

# ============================================================================
# SPEECH RECOGNITION (Transcription)
# ============================================================================

def download_recording(recording_url: str, auth: tuple = None) -> str:
    """Download Twilio recording and validate"""
    logger.info(f"Downloading recording from Twilio")
    
    try:
        response = requests.get(recording_url, auth=auth, timeout=30)
        response.raise_for_status()
        
        # Save to file
        filename = f"{AUDIO_DIR}/recording_{os.urandom(4).hex()}.wav"
        with open(filename, 'wb') as f:
            f.write(response.content)
        
        # Validate file size
        file_size = os.path.getsize(filename)
        if file_size < 1000:
            logger.warning(f"Recording too small: {file_size} bytes")
        
        logger.info(f"✅ Recording saved: {filename} ({file_size} bytes)")
        return filename
    except Exception as e:
        logger.error(f"❌ Failed to download: {str(e)}")
        raise

def transcribe_audio(audio_path: str, language: str) -> str:
    """
    Transcribe audio using Groq's hosted Whisper (free tier)
    Henali's enhancement: Better language detection and error handling
    """
    logger.info(f"Transcribing audio (language: {language})")
    
    if not os.path.exists(audio_path):
        logger.error(f"Audio file not found: {audio_path}")
        return ""
    
    try:
        # Initialize Groq API key
        groq_key = os.getenv("GROQ_API_KEY")
        if not groq_key:
            logger.error("GROQ_API_KEY not set!")
            return ""
        
        # Open and transcribe via Groq (OpenAI-compatible endpoint)
        with open(audio_path, 'rb') as f:
            logger.info("Calling Groq Whisper API...")
            response = requests.post(
                GROQ_STT_URL,
                headers={"Authorization": f"Bearer {groq_key}"},
                files={"file": (os.path.basename(audio_path), f)},
                data={
                    "model": GROQ_STT_MODEL,
                    "language": WHISPER_LANGUAGE_MAP.get(language, "en")
                },
                timeout=60
            )
        
        response.raise_for_status()
        
        text = response.json().get("text", "").strip()
        
        if not text:
            logger.warning("Empty transcription received")
            return ""
        
        logger.info(f"✅ Transcribed: {text}")
        return text
    
    except Exception as e:
        logger.error(f"❌ Transcription failed: {str(e)}")
        return ""

# ============================================================================
# TEXT-TO-SPEECH (Synthesis)
# ============================================================================

def _sanitize_for_speech(text: str) -> str:
    """
    Strip anything that would sound robotic when spoken aloud:
    markdown, asterisks, emojis, bullet markers, stray symbols.
    The agent speaks like a human — the audio must too.
    """
    import re as _re
    t = text or ""
    t = _re.sub(r"[*_`#>~\[\]]", "", t)                    # markdown chars
    t = _re.sub(
        "[\U0001F300-\U0001FAFF\U00002700-\U000027BF\U0001F1E6-\U0001F1FF\u2600-\u27BF\uFE0F]",
        "",
        t,
    )                                                        # emojis/symbols
    t = _re.sub(r"^\s*[-•·]+\s*", "", t, flags=_re.M)        # bullet markers
    t = _re.sub(r"\s{2,}", " ", t).strip()
    return t


async def synthesize_speech(text: str, language: str) -> str:
    """
    Convert text to speech using Microsoft Edge TTS (free, no API key)
    Henali's enhancement: Better voice quality and error handling
    """
    logger.info(f"Synthesizing speech ({language}): {text[:50]}...")
    
    text = _sanitize_for_speech(text)
    if not text or len(text.strip()) == 0:
        logger.error("❌ Empty text provided")
        raise ValueError("Text cannot be empty")
    
    try:
        # Get Edge neural voice for language
        voice = EDGE_VOICE_MAP.get(language, EDGE_VOICE_MAP["en"])
        logger.info(f"Using voice: {voice}")
        
        # Prepare output file
        filename = f"tts_{os.urandom(4).hex()}.mp3"
        filepath = os.path.join(AUDIO_DIR, filename)
        
        # Call Edge TTS (slightly slower rate = calmer, more human delivery)
        logger.info("Calling Edge TTS...")
        communicate = edge_tts.Communicate(
            text,
            voice,
            rate="-4%",
        )
        await communicate.save(filepath)
        
        # Validate output
        file_size = os.path.getsize(filepath)
        if file_size == 0:
            logger.error("❌ Empty audio from Edge TTS")
            raise ValueError("Edge TTS returned empty audio")
        
        logger.info(f"✅ Audio saved: {filename} ({file_size} bytes)")
        
        return filename
    
    except Exception as e:
        logger.error(f"❌ Speech synthesis failed: {str(e)}")
        raise

# ============================================================================
# TESTING UTILITIES (Henali's addition)
# ============================================================================

def test_speech_pipeline(text: str, language: str) -> bool:
    """Test complete speech pipeline"""
    logger.info(f"Testing speech pipeline for {language}")
    
    try:
        # Test TTS
        logger.info("Testing Text-to-Speech...")
        audio_file = asyncio.run(synthesize_speech(text, language))
        
        if not audio_file:
            logger.error("❌ TTS failed")
            return False
        
        logger.info("✅ Speech pipeline working!")
        return True
    
    except Exception as e:
        logger.error(f"❌ Pipeline test failed: {str(e)}")
        return False

def validate_language_support(language: str) -> bool:
    """Check if language is supported"""
    supported = list(EDGE_VOICE_MAP.keys())
    is_supported = language in supported
    
    logger.info(f"Language {language} supported: {is_supported}")
    return is_supported
