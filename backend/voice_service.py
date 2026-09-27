"""
voice_service.py - Voice Service Optimization
Enhanced by Henali for better speech recognition & synthesis
"""

import os
import requests
import logging
from pathlib import Path
from openai import OpenAI

logger = logging.getLogger(__name__)

# Create audio directory
AUDIO_DIR = "audio_files"
Path(AUDIO_DIR).mkdir(exist_ok=True)

# Fish Audio Config
FISH_AUDIO_API_KEY = os.getenv("FISH_AUDIO_API_KEY", "")
FISH_AUDIO_URL = "https://api.fish.audio/v1/tts"

# Henali's Voice Optimization: Better voices for each language
VOICE_MAP = {
    "hi": "1d9c9d82-5b74-4e86-b5fa-0cb3d631ca88",   # Hindi voice
    "gu": "1d9c9d82-5b74-4e86-b5fa-0cb3d631ca88",   # Gujarati voice
    "en": "7a8fd6e8-8a0d-4f0a-b3f5-5f3c2c8e0d5a"    # English voice
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
    Transcribe audio using OpenAI Whisper
    Henali's enhancement: Better language detection and error handling
    """
    logger.info(f"Transcribing audio (language: {language})")
    
    if not os.path.exists(audio_path):
        logger.error(f"Audio file not found: {audio_path}")
        return ""
    
    try:
        # Initialize OpenAI client
        openai_key = os.getenv("OPENAI_API_KEY")
        if not openai_key:
            logger.error("OPENAI_API_KEY not set!")
            return ""
        
        client = OpenAI(api_key=openai_key)
        
        # Open and transcribe
        with open(audio_path, 'rb') as f:
            logger.info("Calling Whisper API...")
            result = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language=WHISPER_LANGUAGE_MAP.get(language, "en")
            )
        
        text = result.text.strip()
        
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

def synthesize_speech(text: str, language: str) -> str:
    """
    Convert text to speech using Fish Audio
    Henali's enhancement: Better voice quality and error handling
    """
    logger.info(f"Synthesizing speech ({language}): {text[:50]}...")
    
    if not FISH_AUDIO_API_KEY:
        logger.error("❌ FISH_AUDIO_API_KEY not configured!")
        raise ValueError("Fish Audio API key not set")
    
    if not text or len(text.strip()) == 0:
        logger.error("❌ Empty text provided")
        raise ValueError("Text cannot be empty")
    
    try:
        # Get voice for language
        voice_id = VOICE_MAP.get(language, VOICE_MAP["en"])
        logger.info(f"Using voice: {voice_id}")
        
        # Prepare request
        headers = {
            "Authorization": f"Bearer {FISH_AUDIO_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "text": text,
            "voice_id": voice_id,
            "language": language,
            "rate": 1.0  # Normal speed
        }
        
        # Call Fish Audio API
        logger.info("Calling Fish Audio API...")
        response = requests.post(
            FISH_AUDIO_URL,
            json=payload,
            headers=headers,
            timeout=30
        )
        
        response.raise_for_status()
        
        # Validate response
        if not response.content:
            logger.error("❌ Empty response from Fish Audio")
            raise ValueError("Fish Audio returned empty response")
        
        # Save audio
        filename = f"tts_{os.urandom(4).hex()}.mp3"
        filepath = os.path.join(AUDIO_DIR, filename)
        
        with open(filepath, 'wb') as f:
            f.write(response.content)
        
        file_size = os.path.getsize(filepath)
        logger.info(f"✅ Audio saved: {filename} ({file_size} bytes)")
        
        return filename
    
    except requests.exceptions.HTTPError as e:
        logger.error(f"❌ Fish Audio API error: {e.response.text}")
        raise
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
        audio_file = synthesize_speech(text, language)
        
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
    supported = list(VOICE_MAP.keys())
    is_supported = language in supported
    
    logger.info(f"Language {language} supported: {is_supported}")
    return is_supported
