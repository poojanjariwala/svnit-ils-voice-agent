"""
voice_service.py - Text-to-Speech using Fish Audio
"""

import os
import requests
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Create audio directory if it doesn't exist
AUDIO_DIR = "audio_files"
Path(AUDIO_DIR).mkdir(exist_ok=True)

# Fish Audio API
FISH_AUDIO_API_KEY = os.getenv("FISH_AUDIO_API_KEY", "")
FISH_AUDIO_URL = "https://api.fish.audio/v1/tts"

# Language to Fish Audio voice mapping
VOICE_MAP = {
    "hi": "1d9c9d82-5b74-4e86-b5fa-0cb3d631ca88",   # Hindi voice
    "gu": "1d9c9d82-5b74-4e86-b5fa-0cb3d631ca88",   # Gujarati (uses Hindi voice)
    "en": "7a8fd6e8-8a0d-4f0a-b3f5-5f3c2c8e0d5a"    # English voice
}

# ============================================================================
# TWILIO HELPERS
# ============================================================================

def download_recording(recording_url: str, auth: tuple = None) -> str:
    """
    Download Twilio recording
    
    Args:
        recording_url: URL from Twilio
        auth: Tuple of (account_sid, auth_token)
    
    Returns:
        Local file path
    """
    logger.info(f"Downloading recording from: {recording_url}")
    
    try:
        response = requests.get(recording_url, auth=auth, timeout=30)
        response.raise_for_status()
        
        # Save to local file
        filename = f"{AUDIO_DIR}/recording_{os.urandom(4).hex()}.wav"
        with open(filename, 'wb') as f:
            f.write(response.content)
        
        logger.info(f"Recording saved to: {filename}")
        return filename
    except Exception as e:
        logger.error(f"Failed to download recording: {str(e)}")
        raise

def transcribe_audio(audio_path: str, language: str) -> str:
    """
    Transcribe audio using OpenAI Whisper
    
    Args:
        audio_path: Path to audio file
        language: Language code (hi, gu, en)
    
    Returns:
        Transcribed text
    """
    logger.info(f"Transcribing audio: {audio_path} (language: {language})")
    
    try:
        from openai import OpenAI
        
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        
        with open(audio_path, 'rb') as f:
            result = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language=language if language in ["hi", "gu", "en"] else "en"
            )
        
        text = result.text
        logger.info(f"Transcribed: {text}")
        return text
    except Exception as e:
        logger.error(f"Transcription failed: {str(e)}")
        return ""

# ============================================================================
# FISH AUDIO TEXT-TO-SPEECH
# ============================================================================

def synthesize_speech(text: str, language: str) -> str:
    """
    Convert text to speech using Fish Audio
    
    Args:
        text: Text to convert
        language: Language code (hi, gu, en)
    
    Returns:
        Audio file path
    """
    logger.info(f"Synthesizing speech: {text[:100]}... (language: {language})")
    
    if not FISH_AUDIO_API_KEY:
        logger.error("FISH_AUDIO_API_KEY not set!")
        raise ValueError("Fish Audio API key not configured")
    
    try:
        # Get voice ID for language
        voice_id = VOICE_MAP.get(language, VOICE_MAP["en"])
        
        # Call Fish Audio API
        headers = {
            "Authorization": f"Bearer {FISH_AUDIO_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "text": text,
            "voice_id": voice_id,
            "language": language
        }
        
        logger.info(f"Calling Fish Audio API with voice: {voice_id}")
        
        response = requests.post(
            FISH_AUDIO_URL,
            json=payload,
            headers=headers,
            timeout=30
        )
        
        response.raise_for_status()
        
        # Save audio to file
        audio_data = response.content
        filename = f"tts_{os.urandom(4).hex()}.mp3"
        filepath = os.path.join(AUDIO_DIR, filename)
        
        with open(filepath, 'wb') as f:
            f.write(audio_data)
        
        logger.info(f"Audio saved to: {filepath}")
        return filename
    
    except requests.exceptions.HTTPError as e:
        logger.error(f"Fish Audio API error: {e.response.text}")
        raise
    except Exception as e:
        logger.error(f"Speech synthesis failed: {str(e)}")
        raise

# ============================================================================
# UTILITY
# ============================================================================

def test_fish_audio_connection():
    """Test if Fish Audio API is working"""
    logger.info("Testing Fish Audio connection...")
    
    if not FISH_AUDIO_API_KEY:
        logger.error("FISH_AUDIO_API_KEY not set!")
        return False
    
    try:
        test_text = "Hello, this is a test."
        synthesize_speech(test_text, "en")
        logger.info("✅ Fish Audio connection successful!")
        return True
    except Exception as e:
        logger.error(f"❌ Fish Audio connection failed: {str(e)}")
        return False
