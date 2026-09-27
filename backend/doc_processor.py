"""
doc_processor.py - Advanced Document Processing
Enhanced by Henali for better text extraction
"""

import os
import logging
from pathlib import Path
import re

logger = logging.getLogger(__name__)

def extract_text(filename: str, file_bytes: bytes) -> str:
    """
    Extract text from various file formats
    Supports: PDF, TXT, XLSX, CSV
    """
    logger.info(f"Extracting text from: {filename}")
    
    try:
        if filename.endswith('.pdf'):
            return extract_from_pdf(file_bytes)
        elif filename.endswith('.txt'):
            return extract_from_text(file_bytes)
        elif filename.endswith(('.xlsx', '.xls')):
            return extract_from_excel(file_bytes, filename)
        elif filename.endswith('.csv'):
            return extract_from_csv(file_bytes)
        else:
            raise ValueError(f"Unsupported file type: {filename}")
    except Exception as e:
        logger.error(f"Error extracting text: {str(e)}")
        raise

def extract_from_text(file_bytes: bytes) -> str:
    """Extract from TXT file"""
    logger.info("Extracting from TXT file")
    try:
        text = file_bytes.decode('utf-8')
        return text.strip()
    except:
        # Try different encoding
        text = file_bytes.decode('latin-1')
        return text.strip()

def extract_from_pdf(file_bytes: bytes) -> str:
    """Extract from PDF file"""
    logger.info("Extracting from PDF file")
    from pypdf import PdfReader
    from io import BytesIO
    
    try:
        pdf = PdfReader(BytesIO(file_bytes))
        text = ""
        
        for page_num in range(len(pdf.pages)):
            page = pdf.pages[page_num]
            text += page.extract_text()
            text += "\n"
        
        logger.info(f"Extracted {len(text)} characters from PDF")
        return text.strip()
    except Exception as e:
        logger.error(f"PDF extraction failed: {str(e)}")
        raise ValueError("Failed to extract text from PDF")

def extract_from_excel(file_bytes: bytes, filename: str) -> str:
    """Extract from XLSX/XLS file"""
    logger.info("Extracting from Excel file")
    from openpyxl import load_workbook
    from io import BytesIO
    
    try:
        workbook = load_workbook(BytesIO(file_bytes))
        text = ""
        
        for sheet_name in workbook.sheetnames:
            sheet = workbook[sheet_name]
            text += f"\n=== {sheet_name} ===\n"
            
            for row in sheet.iter_rows(values_only=True):
                for cell in row:
                    if cell is not None:
                        text += f"{cell} | "
                text += "\n"
        
        logger.info(f"Extracted {len(text)} characters from Excel")
        return text.strip()
    except Exception as e:
        logger.error(f"Excel extraction failed: {str(e)}")
        raise ValueError("Failed to extract text from Excel file")

def extract_from_csv(file_bytes: bytes) -> str:
    """Extract from CSV file"""
    logger.info("Extracting from CSV file")
    try:
        text = file_bytes.decode('utf-8')
        return text.strip()
    except:
        text = file_bytes.decode('latin-1')
        return text.strip()

def clean_and_chunk(text: str, chunk_size: int = 2000) -> str:
    """
    Clean text and prepare for LLM
    Henali's enhancement: Better cleaning & chunking
    """
    logger.info(f"Cleaning and chunking text ({len(text)} chars)")
    
    # Remove extra whitespace
    text = re.sub(r'\s+', ' ', text)
    
    # Remove special characters but keep business info
    text = re.sub(r'[^\w\s\d\-.,;:?!()]\n', '', text)
    
    # Remove URLs
    text = re.sub(r'http[s]?://\S+', '', text)
    
    # Remove email addresses
    text = re.sub(r'\S+@\S+', '', text)
    
    # Clean up
    text = text.strip()
    
    logger.info(f"Cleaned text to {len(text)} characters")
    
    return text

def validate_knowledge_base(text: str) -> bool:
    """Validate extracted knowledge base"""
    logger.info("Validating knowledge base")
    
    # Must have minimum content
    if len(text) < 50:
        logger.warning("Knowledge base too small")
        return False
    
    # Must not be all special characters
    if not any(c.isalnum() for c in text):
        logger.warning("Knowledge base has no alphanumeric characters")
        return False
    
    logger.info("✅ Knowledge base valid")
    return True
