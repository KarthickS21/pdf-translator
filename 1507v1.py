"""
OCR CREDIT

https://github.com/UB-Mannheim/tesseract/wiki
Download the Tesseract installer:
Run the installer:

Choose English and optionally other languages.

Install it to:
C:\Program Files\Tesseract-OCR (default)

Add Tesseract to your System PATH:

Open Start Menu > Environment Variables

Under “System Variables”, select Path → click Edit

Add the line:
C:\Program Files\Tesseract-OCR
Click OK and restart your terminal or IDE.

Verify installation:
In a new terminal, run:

tesseract --version
You should see version info like:

tesseract v5.3.1

Optional:
Tesseract uses .traineddata files for each language.

English is installed by default (eng).

To add Spanish (spa), do this:

Go to: C:\Program Files\Tesseract-OCR\tessdata

Download: spa.traineddata

Place the .traineddata file inside that tessdata folder.
"""
import io
import logging
import fitz  # PyMuPDF
from openai import OpenAI
import time
import os
import ssl
from collections import Counter
import traceback
from typing import Tuple, List, Dict, Any
import re
import httpx
from langchain_openai import AzureChatOpenAI

import easyocr
from PIL import Image
import numpy as np

# Create reader once
reader = easyocr.Reader(['en'])  # Add 'es' if you want multilingual



# Load custom SSL certificate and build HTTP client
cert_data = os.environ.get("HUMANA_CERT")  # path to PEM or cert content
ctx = ssl.create_default_context(cadata=cert_data)
custom_client = httpx.Client(verify=ctx)


def ocr_with_easyocr(image: Image.Image) -> str:
    # Convert PIL image to numpy array
    image_np = np.array(image)
    # Run OCR
    results = reader.readtext(image_np, detail=0)
    return "\n".join(results)

# Init LLM via AzureChatOpenAI
def get_llm_model(model_name="gpt-3.5-turbo", temperature=0.2):
    return AzureChatOpenAI(
        azure_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT"),  # e.g., https://example.openai.azure.com/
        api_key=os.environ.get("AZURE_OPENAI_KEY"),
        api_version="2024-02-15-preview",  # or your correct version
        model=model_name,
        temperature=temperature,
        http_client=custom_client
    )

# Initialize model once
# llm = get_llm_model()


client = OpenAI(api_key="")

 
def should_translate_text(text: str) -> bool:
    """
    Determine if text should be translated - be more conservative to preserve layout.
    """
    text = text.strip()
    
    if not text:
        return False
    
    # Don't translate pure numbers
    if re.match(r'^\d+$', text):
        return False
    
    # Don't translate currency amounts
    if re.match(r'^\$\d+([,\d]*\.?\d*)?$', text):
        return False
    
    # Don't translate codes/IDs
    if re.match(r'^[A-Z0-9]{3,}$', text):
        return False
    
    # Don't translate single characters or very short strings
    if len(text) <= 2:
        return False
    
    # Don't translate abbreviations
    if text.upper() in ['N/A', 'MRI', 'MRA', 'PET', 'CT', 'PCP', 'EOC']:
        return False
    
    # Don't translate if it's mostly numbers and special characters
    alpha_chars = sum(1 for c in text if c.isalpha())
    if alpha_chars < 3:
        return False
    
    return True

def translate_text_conservative(text: str, target_lang: str = "Spanish", retries: int = 3) -> str:
    """
    Conservative translation that preserves structure.
    """
    if not should_translate_text(text):
        return text
    
    # For very short text, be extra careful
    if len(text.split()) <= 2:
        # Check if it's a common term
        common_terms = {
            'copay': 'copago',
            'deductible': 'deducible',
            'premium': 'prima',
            'plan': 'plan',
            'year': 'año',
            'services': 'servicios',
            'coverage': 'cobertura',
            'benefits': 'beneficios',
            'maximum': 'máximo',
            'monthly': 'mensual',
            'medical': 'médico',
            'hospital': 'hospital',
            'inpatient': 'hospitalización',
            'outpatient': 'ambulatorio'
        }
        
        lower_text = text.lower().strip()
        if lower_text in common_terms:
            return common_terms[lower_text]
    
    prompt = f"""Translate this English text to {target_lang}. This is from a medical insurance document.

RULES:
1. Keep ALL numbers and currency exactly as they are
2. Keep ALL codes unchanged: H5619136002, N/A, etc.
3. Keep proper nouns: Apple Health, Medicaid, Medicare Part A, Part B, Part D
4. Keep abbreviations: MRI, CT, PET, MRA, PCP, EOC
5. Use standard medical/insurance Spanish terminology
6. Keep formatting and punctuation exactly the same
7. Return ONLY the translation, no explanations

Text: "{text}"

Translation:"""
    
    for attempt in range(retries):
        try:
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            translated =  response.choices[0].message.content.strip()
            # response = llm.invoke(prompt) 
            #translated = response.choices[0].message.content.strip()
            # translated = response.content.strip()
            translated = translated.replace('"', '').replace("'", "")
            
            # Clean up common response prefixes
            prefixes = ['Translation:', 'Traducción:', f'{target_lang}:', 'Spanish:']
            for prefix in prefixes:
                if translated.startswith(prefix):
                    translated = translated[len(prefix):].strip()
            
            return translated
            
        except Exception as e:
            print(f"Translation attempt {attempt + 1} failed: {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    
    return text

def get_font_info(span: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract font information with better defaults and available fonts.
    """
    font_name = span.get('font', '').lower()
    flags = span.get('flags', 0)
    size = span.get('size', 12)
    
    # Determine font type
    is_bold = 'bold' in font_name or (flags & 16)
    is_italic = 'italic' in font_name or (flags & 2)
    
    # Use standard fonts that are always available
    if is_bold and is_italic:
        font = "helv-boldoblique"
    elif is_bold:
        font = "helv-bold"
    elif is_italic:
        font = "helv-oblique"
    else:
        font = "helv"
    
    return {
        'font': font,
        'size': max(6, min(size, 24)),  # Reasonable size limits
        'color': span.get('color', 0),
        'flags': flags
    }

def calculate_text_dimensions(text: str, font_size: float) -> Tuple[float, float]:
    """
    Calculate approximate text dimensions.
    """
    # Average character width is approximately 0.6 of font size for Helvetica
    avg_char_width = font_size * 0.6
    text_width = len(text) * avg_char_width
    text_height = font_size * 1.2  # Include some line spacing
    
    return text_width, text_height

def insert_text_with_fallbacks(page: fitz.Page, bbox: Tuple[float, float, float, float], 
                              text: str, font_info: Dict[str, Any]) -> bool:
    """
    Insert text with multiple fallback strategies.
    """
    if not text.strip():
        return False
    
    x0, y0, x1, y1 = bbox
    original_rect = fitz.Rect(x0, y0, x1, y1)
    
    # Ensure minimum rect size
    if original_rect.width < 10:
        original_rect.x1 = original_rect.x0 + max(50, len(text) * 8)
    if original_rect.height < 8:
        original_rect.y1 = original_rect.y0 + 12
    
    font_name = font_info['font']
    original_size = font_info['size']
    
    # Convert color
    color = font_info['color']
    if isinstance(color, (int, float)):
        if color == 0:
            color_rgb = (0, 0, 0)  # Black
        else:
            color_rgb = (0, 0, 0)  # Default black
    else:
        color_rgb = (0, 0, 0)
    
    # Strategy 1: Try original size and font
    strategies = [
        {'font': font_name, 'size': original_size, 'rect': original_rect},
        {'font': font_name, 'size': original_size * 0.9, 'rect': original_rect},
        {'font': font_name, 'size': original_size * 0.8, 'rect': original_rect},
        {'font': 'helv', 'size': original_size, 'rect': original_rect},
        {'font': 'helv', 'size': original_size * 0.9, 'rect': original_rect},
        {'font': 'helv', 'size': 8, 'rect': original_rect},
    ]
    
    # Strategy 2: Try with expanded rectangles
    expanded_rect = fitz.Rect(x0, y0, x1 + 50, y1 + 5)
    strategies.extend([
        {'font': font_name, 'size': original_size, 'rect': expanded_rect},
        {'font': 'helv', 'size': original_size, 'rect': expanded_rect},
        {'font': 'helv', 'size': 8, 'rect': expanded_rect},
    ])
    
    for strategy in strategies:
        try:
            rect = strategy['rect']
            font = strategy['font']
            size = strategy['size']
            
            # Ensure size is reasonable
            if size < 6:
                size = 6
            elif size > 20:
                size = 20
            
            result = page.insert_textbox(
                rect,
                text,
                fontname=font,
                fontsize=size,
                color=color_rgb,
                align=0,  # Left align
                rotate=0
            )
            
            if result >= 0:
                return True
                
        except Exception as e:
            continue
    
    # Last resort: Insert at point with minimal formatting
    try:
        point = fitz.Point(x0, y0 + 10)  # Slightly offset for better positioning
        result = page.insert_text(
            point,
            text,
            fontname="helv",
            fontsize=8,
            color=(0, 0, 0)
        )
        return result >= 0
    except:
        return False

def create_better_redaction_rect(bbox: Tuple[float, float, float, float], 
                                text: str, font_size: float) -> fitz.Rect:
    """
    Create a more accurate redaction rectangle.
    """
    x0, y0, x1, y1 = bbox
    
    # Calculate text dimensions
    text_width, text_height = calculate_text_dimensions(text, font_size)
    
    # Use the larger of the original bbox or calculated dimensions
    width = max(x1 - x0, text_width)
    height = max(y1 - y0, text_height)
    
    # Add small padding
    padding = 1
    
    return fitz.Rect(
        x0 - padding,
        y0 - padding,
        x0 + width + padding,
        y0 + height + padding
    )

def translate_pdf_layout_preserving(input_pdf_path: str, output_pdf_path: str, target_lang: str = "Spanish") -> None:
    """
    Translate PDF while strictly preserving layout and positioning.
    """
    doc = fitz.open(input_pdf_path)
    print(f"🔄 Starting layout-preserving translation of {len(doc)} pages to {target_lang}")

    for page_num, page in enumerate(doc):
        print(f"\n📄 Processing page {page_num + 1}/{len(doc)}")
        try:
            process_images_on_page(page, target_lang)
        except Exception as err:
            logging.error(err)
        
        try:
            # Extract text spans individually to preserve exact positioning
            blocks = page.get_text("dict")["blocks"]
            individual_spans = []
            
            for block in blocks:
                if "lines" not in block:
                    continue
                
                for line in block["lines"]:
                    for span in line["spans"]:
                        text = span["text"]
                        if text and text.strip():
                            bbox = span["bbox"]
                            if bbox[2] > bbox[0] and bbox[3] > bbox[1]:
                                individual_spans.append({
                                    'text': text,
                                    'bbox': bbox,
                                    'font_info': get_font_info(span)
                                })
            
            print(f"   Found {len(individual_spans)} individual text spans")
            
            # Process each span individually to maintain exact positioning
            translation_tasks = []
            for span in individual_spans:
                text = span['text']
                
                print(f"   Processing: '{text}' -> ", end="")
                
                translated = translate_text_conservative(text, target_lang)
                print(f"'{translated}'")
                
                # Only add to tasks if translation is different
                if translated != text:
                    translation_tasks.append({
                        'original': text,
                        'translated': translated,
                        'bbox': span['bbox'],
                        'font_info': span['font_info']
                    })
            
            print(f"   Will translate {len(translation_tasks)} spans")
            
            # First, collect all redaction rectangles
            redaction_rects = []
            for task in translation_tasks:
                bbox = task['bbox']
                font_size = task['font_info']['size']
                redact_rect = create_better_redaction_rect(bbox, task['original'], font_size)
                redaction_rects.append(redact_rect)
                page.add_redact_annot(redact_rect, fill=(1, 1, 1))  # White fill
            
            # Apply all redactions at once
            page.apply_redactions()
            
            # Insert translated text in exact same positions
            successful_insertions = 0
            for task in translation_tasks:
                translated = task['translated']
                bbox = task['bbox']
                font_info = task['font_info']
                
                if insert_text_with_fallbacks(page, bbox, translated, font_info):
                    successful_insertions += 1
                else:
                    print(f"   Failed to insert: '{translated}' at {bbox}")
            
            print(f"   Successfully inserted {successful_insertions}/{len(translation_tasks)} translations")
            
        except Exception as e:
            print(f"   Error processing page {page_num + 1}: {e}")
            traceback.print_exc()
            continue

    # Save the document
    doc.save(output_pdf_path)
    doc.close()
    print(f"\n✅ Translation completed! Saved to: {output_pdf_path}")


def get_image_rect_from_text_dict(page, xref):
    """Get image rectangle from text dictionary"""
    text_dict = page.get_text("dict")
    for block in text_dict["blocks"]:
        if block["type"] == 1:  # Image block
            image_data = block.get("image")
            if isinstance(image_data, dict):
                if image_data.get("xref") == xref:
                    return fitz.Rect(block["bbox"])
    return None


def get_image_rect_from_drawings(page, xref):
    """Alternative method to get image rectangle from drawings"""
    try:
        drawings = page.get_drawings()
        for drawing in drawings:
            if hasattr(drawing, 'rect') and drawing.get('image_xref') == xref:
                return drawing['rect']
    except Exception as e:
        print(f"   ⚠️ get_drawings failed: {e}")
    return None


def get_image_rect_comprehensive(page, xref):
    """Try multiple methods to get image rectangle"""
    
    # Method 1: Try get_image_bbox (most reliable when it works)
    try:
        rects = page.get_image_bbox(xref)
        if rects:
            print(f"   ✅ Found bbox via get_image_bbox: {rects[0]}")
            return rects[0]
    except Exception as e:
        print(f"   ⚠️ get_image_bbox failed for xref {xref}: {e}")
    
    # Method 2: Try text dictionary method
    img_rect = get_image_rect_from_text_dict(page, xref)
    if img_rect:
        print(f"   ✅ Found bbox via text_dict: {img_rect}")
        return img_rect
    
    # Method 3: Try drawings method
    img_rect = get_image_rect_from_drawings(page, xref)
    if img_rect:
        print(f"   ✅ Found bbox via drawings: {img_rect}")
        return img_rect
    
    # Method 4: Try to find by matching image data
    try:
        target_image = page.parent.extract_image(xref)
        text_dict = page.get_text("dict")
        
        for block in text_dict["blocks"]:
            if block["type"] == 1:  # Image block
                try:
                    block_xref = block.get("image", {}).get("xref")
                    if block_xref and block_xref == xref:
                        rect = fitz.Rect(block["bbox"])
                        print(f"   ✅ Found bbox via block matching: {rect}")
                        return rect
                except Exception:
                    continue
                    
    except Exception as e:
        print(f"   ⚠️ Image data matching failed: {e}")
    
    return None


def process_images_on_page(page: fitz.Page, target_lang: str):
    image_list = page.get_images(full=True)
    print(f"   Found {len(image_list)} images on page")

    for img_index, img in enumerate(image_list):
        xref = img[0]
        
        try:
            base_image = page.parent.extract_image(xref)
            image_bytes = base_image["image"]
            img_ext = base_image["ext"]
        except Exception as e:
            print(f"   ❌ Failed to extract image {img_index + 1} (xref {xref}): {e}")
            continue
 
        try:
            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            english_text = ocr_with_easyocr(image).strip()
        except Exception as e:
            print(f"   ❌ Failed to process image {img_index + 1} for OCR: {e}")
            continue

        if not english_text:
            print(f"   🖼️ Image {img_index + 1}: No text detected via OCR.")
            continue

        print(f"   🖼️ OCR Text: {english_text}")
 
        try:
            translated_text = translate_text_conservative(english_text, target_lang)
            print(f"   🔤 Translated OCR Text: {translated_text}")
        except Exception as e:
            print(f"   ❌ Translation failed for image {img_index + 1}: {e}")
            continue
 
        # Get image rectangle using comprehensive method
        img_rect = get_image_rect_comprehensive(page, xref)
        
        if not img_rect:
            print(f"   ❌ No bounding box found for xref {xref} after trying all methods")
            continue

        try:
            font_info = {"font": "helv", "size": 10, "color": 0, "flags": 0}

            # Optional: Draw a white background over the image region
            # page.draw_rect(img_rect, color=(1, 1, 1), fill=(1, 1, 1))
 
            insert_text_with_fallbacks(page, img_rect, translated_text, font_info)
            print(f"   ✅ Successfully processed image {img_index + 1}")
            
        except Exception as e:
            print(f"   ❌ Failed to insert text for image {img_index + 1}: {e}")
            continue


def main():
    """Main function to run the PDF translation."""
    try:
        # Configuration
        input_file = "img-pdf.pdf"
        output_file = "t5.pdf"
        target_language = "Spanish"
         
        # Check if input file exists
        if not os.path.exists(input_file):
            print(f"❌ Input file '{input_file}' not found!")
            return
        
        # Run translation
        translate_pdf_layout_preserving(input_file, output_file, target_language)
        
    except Exception as e:
        print(f"❌ Error: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    main()
