"""
Constitutional Assistant - File Parser
Поддерживаемые форматы:
- PDF (текст + сканы с OCR)
- Word (.docx, .doc)
- Изображения (JPG, PNG, TIFF, BMP) с OCR
- Текст (.txt)
- OpenDocument (.odt)
"""

import os
import io
import tempfile
from pathlib import Path
from typing import Optional
from fastapi import UploadFile

# ============================================================================
# ОСНОВНАЯ ФУНКЦИЯ ПАРСИНГА
# ============================================================================

async def parse_file(file: UploadFile) -> dict:
    """
    Главная функция парсинга файлов.
    Определяет тип файла и вызывает нужный парсер.
    
    Returns:
        dict с ключами:
        - text: извлечённый текст
        - file_type: тип файла
        - pages: количество страниц (если применимо)
        - success: True/False
        - error: описание ошибки (если есть)
    """
    
    filename = file.filename.lower()
    content = await file.read()
    
    try:
        # PDF файлы
        if filename.endswith('.pdf'):
            return await parse_pdf(content, filename)
        
        # Word файлы
        elif filename.endswith('.docx'):
            return await parse_docx(content, filename)
        
        elif filename.endswith('.doc'):
            return await parse_doc(content, filename)
        
        # Текстовые файлы
        elif filename.endswith('.txt'):
            return await parse_txt(content, filename)
        
        # OpenDocument
        elif filename.endswith('.odt'):
            return await parse_odt(content, filename)
        
        # Изображения (OCR)
        elif filename.endswith(('.jpg', '.jpeg', '.png', '.tiff', '.tif', '.bmp', '.gif', '.webp')):
            return await parse_image_ocr(content, filename)
        
        else:
            return {
                "text": "",
                "file_type": "unknown",
                "pages": 0,
                "success": False,
                "error": f"Формат файла не поддерживается: {filename}"
            }
    
    except Exception as e:
        return {
            "text": "",
            "file_type": "error",
            "pages": 0,
            "success": False,
            "error": f"Ошибка при обработке файла: {str(e)}"
        }

# ============================================================================
# PDF ПАРСЕР
# ============================================================================

async def parse_pdf(content: bytes, filename: str) -> dict:
    """Парсит PDF файл. Если текст не извлекается - применяет OCR."""
    
    try:
        import pymupdf  # PyMuPDF (fitz)
        
        doc = pymupdf.open(stream=content, filetype="pdf")
        text_parts = []
        pages = len(doc)
        
        for page_num in range(pages):
            page = doc[page_num]
            text = page.get_text()
            
            if text.strip():
                text_parts.append(f"[Страница {page_num + 1}]\n{text}")
            else:
                # Страница скан — применяем OCR
                ocr_text = await ocr_page(page)
                if ocr_text:
                    text_parts.append(f"[Страница {page_num + 1} (OCR)]\n{ocr_text}")
        
        doc.close()
        
        full_text = "\n\n".join(text_parts)
        
        return {
            "text": full_text,
            "file_type": "pdf",
            "pages": pages,
            "success": True,
            "error": None
        }
    
    except ImportError:
        # Если PyMuPDF не установлен — пробуем PyPDF2
        return await parse_pdf_fallback(content, filename)
    
    except Exception as e:
        return {
            "text": "",
            "file_type": "pdf",
            "pages": 0,
            "success": False,
            "error": f"Ошибка PDF: {str(e)}"
        }

async def parse_pdf_fallback(content: bytes, filename: str) -> dict:
    """Запасной PDF парсер через pypdf."""
    try:
        import pypdf
        
        reader = pypdf.PdfReader(io.BytesIO(content))
        text_parts = []
        pages = len(reader.pages)
        
        for i, page in enumerate(reader.pages):
            text = page.extract_text()
            if text:
                text_parts.append(f"[Страница {i + 1}]\n{text}")
        
        return {
            "text": "\n\n".join(text_parts),
            "file_type": "pdf",
            "pages": pages,
            "success": True,
            "error": None
        }
    
    except Exception as e:
        return {
            "text": "",
            "file_type": "pdf",
            "pages": 0,
            "success": False,
            "error": f"Ошибка PDF (fallback): {str(e)}"
        }

# ============================================================================
# WORD ПАРСЕР
# ============================================================================

async def parse_docx(content: bytes, filename: str) -> dict:
    """Парсит .docx файл (Word 2007+)."""
    
    try:
        from docx import Document
        
        doc = Document(io.BytesIO(content))
        text_parts = []
        
        for para in doc.paragraphs:
            if para.text.strip():
                text_parts.append(para.text)
        
        # Также извлекаем текст из таблиц
        for table in doc.tables:
            for row in table.rows:
                row_text = " | ".join(cell.text for cell in row.cells if cell.text.strip())
                if row_text:
                    text_parts.append(row_text)
        
        full_text = "\n".join(text_parts)
        
        return {
            "text": full_text,
            "file_type": "docx",
            "pages": len(doc.paragraphs),
            "success": True,
            "error": None
        }
    
    except Exception as e:
        return {
            "text": "",
            "file_type": "docx",
            "pages": 0,
            "success": False,
            "error": f"Ошибка DOCX: {str(e)}"
        }

async def parse_doc(content: bytes, filename: str) -> dict:
    """Парсит .doc файл (старый Word формат)."""
    
    try:
        # Пробуем через textract
        import textract
        
        with tempfile.NamedTemporaryFile(suffix='.doc', delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        
        text = textract.process(tmp_path).decode('utf-8')
        os.unlink(tmp_path)
        
        return {
            "text": text,
            "file_type": "doc",
            "pages": 1,
            "success": True,
            "error": None
        }
    
    except Exception as e:
        return {
            "text": "",
            "file_type": "doc",
            "pages": 0,
            "success": False,
            "error": f"Ошибка DOC: {str(e)}. Попробуйте конвертировать в .docx"
        }

# ============================================================================
# ТЕКСТОВЫЙ ПАРСЕР
# ============================================================================

async def parse_txt(content: bytes, filename: str) -> dict:
    """Парсит .txt файл."""
    
    try:
        # Пробуем разные кодировки
        for encoding in ['utf-8', 'cp1251', 'latin-1']:
            try:
                text = content.decode(encoding)
                return {
                    "text": text,
                    "file_type": "txt",
                    "pages": 1,
                    "success": True,
                    "error": None
                }
            except UnicodeDecodeError:
                continue
        
        # Если ничего не сработало
        text = content.decode('utf-8', errors='replace')
        return {
            "text": text,
            "file_type": "txt",
            "pages": 1,
            "success": True,
            "error": None
        }
    
    except Exception as e:
        return {
            "text": "",
            "file_type": "txt",
            "pages": 0,
            "success": False,
            "error": f"Ошибка TXT: {str(e)}"
        }

# ============================================================================
# ODT ПАРСЕР
# ============================================================================

async def parse_odt(content: bytes, filename: str) -> dict:
    """Парсит .odt файл (OpenDocument)."""
    
    try:
        from odf.opendocument import load
        from odf.text import P
        from odf import teletype
        
        with tempfile.NamedTemporaryFile(suffix='.odt', delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        
        doc = load(tmp_path)
        os.unlink(tmp_path)
        
        text_parts = []
        for para in doc.getElementsByType(P):
            text = teletype.extractText(para)
            if text.strip():
                text_parts.append(text)
        
        return {
            "text": "\n".join(text_parts),
            "file_type": "odt",
            "pages": 1,
            "success": True,
            "error": None
        }
    
    except Exception as e:
        return {
            "text": "",
            "file_type": "odt",
            "pages": 0,
            "success": False,
            "error": f"Ошибка ODT: {str(e)}"
        }

# ============================================================================
# OCR ПАРСЕР (для изображений и сканов)
# ============================================================================

async def parse_image_ocr(content: bytes, filename: str) -> dict:
    """Применяет OCR к изображению (JPG, PNG, TIFF и т.д.)."""
    
    try:
        import pytesseract
        from PIL import Image
        
        image = Image.open(io.BytesIO(content))
        
        # OCR на русском + казахском + английском языках
        text = pytesseract.image_to_string(
            image,
            lang='rus+kaz+eng',
            config='--psm 6'
        )
        
        return {
            "text": text,
            "file_type": "image_ocr",
            "pages": 1,
            "success": True,
            "error": None
        }
    
    except ImportError:
        return {
            "text": "",
            "file_type": "image",
            "pages": 0,
            "success": False,
            "error": "OCR не установлен. Выполните: pip install pytesseract pillow && установите Tesseract OCR"
        }
    
    except Exception as e:
        return {
            "text": "",
            "file_type": "image",
            "pages": 0,
            "success": False,
            "error": f"Ошибка OCR: {str(e)}"
        }

async def ocr_page(page) -> str:
    """Применяет OCR к странице PDF (для сканированных PDF)."""
    
    try:
        import pytesseract
        from PIL import Image
        
        # Конвертируем страницу PDF в изображение
        pix = page.get_pixmap(dpi=300)
        img_data = pix.tobytes("png")
        image = Image.open(io.BytesIO(img_data))
        
        text = pytesseract.image_to_string(
            image,
            lang='rus+kaz+eng',
            config='--psm 6'
        )
        
        return text
    
    except Exception:
        return ""

# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================================

def get_supported_formats() -> list:
    """Возвращает список поддерживаемых форматов."""
    return [
        ".pdf",
        ".docx",
        ".doc", 
        ".txt",
        ".odt",
        ".jpg", ".jpeg",
        ".png",
        ".tiff", ".tif",
        ".bmp",
        ".gif",
        ".webp"
    ]

def clean_text(text: str) -> str:
    """Очищает извлечённый текст от лишних символов."""
    
    if not text:
        return ""
    
    # Убираем лишние пробелы и переносы
    lines = [line.strip() for line in text.split('\n')]
    lines = [line for line in lines if line]
    
    # Убираем повторяющиеся пустые строки
    cleaned = '\n'.join(lines)
    
    return cleaned

def truncate_text(text: str, max_chars: int = 10000) -> str:
    """Обрезает текст до максимальной длины для LLM."""
    
    if len(text) <= max_chars:
        return text
    
    return text[:max_chars] + f"\n\n[... текст обрезан, показаны первые {max_chars} символов из {len(text)}]"
