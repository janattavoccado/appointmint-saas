"""
Audio Transcriber - Handle WhatsApp audio messages
Downloads audio from Chatwoot attachment URL and transcribes using OpenAI Whisper API
"""

import os
import logging
import requests
import tempfile
from typing import Optional

logger = logging.getLogger(__name__)


class AudioTranscriber:
    """Transcribe audio files from Chatwoot attachments"""
    
    def __init__(self):
        """Initialize transcriber with OpenAI API key"""
        self.api_key = os.getenv('OPENAI_API_KEY')
        if not self.api_key:
            logger.warning("OPENAI_API_KEY not set - audio transcription will fail")
    
    def download_audio(self, audio_url: str) -> str:
        """
        Download audio file from Chatwoot URL
        
        Args:
            audio_url: URL to audio file from Chatwoot attachment
            
        Returns:
            Path to downloaded audio file (temporary)
        """
        try:
            logger.info(f"Downloading audio from: {audio_url[:100]}...")
            
            # Download audio file
            response = requests.get(audio_url, timeout=30)
            response.raise_for_status()
            
            # Save to temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix='.ogg') as tmp:
                tmp.write(response.content)
                tmp_path = tmp.name
            
            logger.info(f"Audio downloaded successfully: {tmp_path}")
            return tmp_path
        
        except Exception as e:
            logger.error(f"Error downloading audio: {str(e)}")
            raise
    
    def transcribe_audio(self, audio_path: str) -> str:
        """
        Transcribe audio file using OpenAI Whisper API
        
        Args:
            audio_path: Path to audio file
            
        Returns:
            Transcribed text
        """
        try:
            logger.info(f"Transcribing audio: {audio_path}")
            
            # Open audio file
            with open(audio_path, 'rb') as audio_file:
                # Call OpenAI Whisper API
                from openai import OpenAI
                client = OpenAI(api_key=self.api_key)
                
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file
                )
            
            transcribed_text = transcript.text
            logger.info(f"Transcription successful: {transcribed_text[:100]}...")
            
            return transcribed_text
        
        except Exception as e:
            logger.error(f"Error transcribing audio: {str(e)}")
            raise
        
        finally:
            # Clean up temporary file
            try:
                if os.path.exists(audio_path):
                    os.remove(audio_path)
                    logger.info(f"Cleaned up temporary audio file: {audio_path}")
            except Exception as e:
                logger.warning(f"Error cleaning up temp file: {str(e)}")
    
    def transcribe_from_url(self, audio_url: str) -> Optional[str]:
        """
        Download audio from URL and transcribe
        
        Args:
            audio_url: URL to audio file from Chatwoot attachment
            
        Returns:
            Transcribed text or None if failed
        """
        try:
            # Download audio
            audio_path = self.download_audio(audio_url)
            
            # Transcribe audio
            transcribed_text = self.transcribe_audio(audio_path)
            
            return transcribed_text
        
        except Exception as e:
            logger.error(f"Error in transcribe_from_url: {str(e)}")
            return None


def extract_audio_from_payload(payload: dict) -> Optional[str]:
    """
    Extract audio URL from Chatwoot webhook payload
    
    Args:
        payload: Chatwoot webhook payload
        
    Returns:
        Audio URL or None if no audio found
    """
    try:
        # Check for attachments at root level
        attachments = payload.get('attachments', [])
        
        if not attachments:
            logger.debug("No attachments in payload")
            return None
        
        # Find audio attachment
        for attachment in attachments:
            if attachment.get('file_type') == 'audio':
                audio_url = attachment.get('data_url')
                if audio_url:
                    logger.info(f"Found audio attachment: {audio_url[:100]}...")
                    return audio_url
        
        logger.debug("No audio attachment found in payload")
        return None
    
    except Exception as e:
        logger.error(f"Error extracting audio from payload: {str(e)}")
        return None


def is_audio_message(payload: dict) -> bool:
    """
    Check if payload contains an audio message
    
    Args:
        payload: Chatwoot webhook payload
        
    Returns:
        True if message contains audio
    """
    try:
        attachments = payload.get('attachments', [])
        
        for attachment in attachments:
            if attachment.get('file_type') == 'audio':
                return True
        
        return False
    
    except Exception as e:
        logger.error(f"Error checking if audio message: {str(e)}")
        return False
