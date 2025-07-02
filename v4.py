import fitz  # PyMuPDF
from openai import OpenAI
import time
import os
from collections import Counter
import traceback
from typing import Tuple, List, Dict, Any

# Load API key from environment variable for security
client = OpenAI(api_key="sss")

def translate_text(text: str, target_lang: str = "Spanish", retries: int = 3) -> str:
    """
    Translate text using OpenAI API with retry logic.
    
    Args:
        text: Text to translate
        target_lang: Target language for translation
        retries: Number of retry attempts
    
    Returns:
        Translated text or original text if translation fails
    """
    if not text.strip():
        return text
    
    prompt = f"Translate the following English text to {target_lang}, preserving format and tone. Only return the translation:\n\n{text}"
    
    for attempt in range(retries):
        try:
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=1000
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"Translation attempt {attempt + 1} failed: {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
            else:
                print(f"Failed to translate after {retries} attempts: {text[:50]}...")
    
    return text

def get_font_type(span: Dict[str, Any]) -> str:
    """
    Determine font type (bold, light, regular) from span data.
    
    Args:
        span: Text span dictionary from PyMuPDF
    
    Returns:
        Font type string
    """
    font_name = span.get('font', '').lower()
    flags = span.get('flags', 0)
    
    if 'bold' in font_name or flags & 16:  # Bold flag
        return 'bold'
    elif 'italic' in font_name or flags & 2:  # Italic flag
        return 'italic'
    elif 'light' in font_name or flags == 4:
        return 'light'
    else:
        return 'regular'

def get_pymupdf_font(font_type: str) -> str:
    """
    Map font type to PyMuPDF font name.
    
    Args:
        font_type: Font type string
    
    Returns:
        PyMuPDF font name
    """
    font_mapping = {
        "bold": "Times-Bold",
        "italic": "Times-Italic",
        "light": "Times-Roman",
        "regular": "Times-Roman"
    }
    return font_mapping.get(font_type, "Times-Roman")

def convert_color_to_rgb(color_value: Any) -> Tuple[float, float, float]:
    """
    Convert various color formats to RGB tuple.
    
    Args:
        color_value: Color value in various formats
    
    Returns:
        RGB tuple with values between 0 and 1
    """
    if isinstance(color_value, (int, float)):
        if color_value == 0:
            return (0, 0, 0)  # Black
        elif color_value == 1 or color_value >= 16777215:
            return (1, 1, 1)  # White
        else:
            # Convert integer color to RGB
            r = ((int(color_value) >> 16) & 255) / 255.0
            g = ((int(color_value) >> 8) & 255) / 255.0
            b = (int(color_value) & 255) / 255.0
            return (r, g, b)
    elif isinstance(color_value, (list, tuple)) and len(color_value) >= 3:
        return tuple(color_value[:3])
    else:
        return (0, 0, 0)  # Default to black

def detect_background_color(page: fitz.Page, bbox: Tuple[float, float, float, float], zoom: int = 2) -> Tuple[float, float, float]:
    """
    Detect the background color of a text region.
    
    Args:
        page: PyMuPDF page object
        bbox: Bounding box coordinates
        zoom: Zoom factor for sampling
    
    Returns:
        RGB tuple representing background color
    """
    try:
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        x0, y0, x1, y1 = [int(v * zoom) for v in bbox]
        
        # Sample multiple points around the text area
        sample_coords = [
            ((x0 + x1) // 2, (y0 + y1) // 2),  # Center
            (x0 + 5, y0 + 5), (x1 - 5, y0 + 5),  # Top corners
            (x0 + 5, y1 - 5), (x1 - 5, y1 - 5),  # Bottom corners
        ]
        
        colors = []
        for x, y in sample_coords:
            try:
                if 0 <= x < pix.width and 0 <= y < pix.height:
                    r, g, b = pix.pixel(x, y)[:3]
                    colors.append((round(r / 255, 2), round(g / 255, 2), round(b / 255, 2)))
            except:
                continue
        
        if colors:
            # Find most common color
            rounded_colors = [tuple(round(c, 1) for c in color) for color in colors]
            return Counter(rounded_colors).most_common(1)[0][0]
        
        return (1, 1, 1)  # Default to white
    except Exception as e:
        print(f"Error detecting background color: {e}")
        return (1, 1, 1)

def is_dark_color(rgb: Tuple[float, float, float], threshold: float = 0.4) -> bool:
    """
    Determine if a color is dark based on its RGB values.
    
    Args:
        rgb: RGB tuple
        threshold: Threshold for darkness (0-1)
    
    Returns:
        True if color is dark, False otherwise
    """
    return sum(rgb) / 3 < threshold

def infer_rotation(span: Dict[str, Any]) -> int:
    """
    Infer text rotation angle from span data.
    
    Args:
        span: Text span dictionary
    
    Returns:
        Rotation angle in degrees
    """
    angle = span.get("text_angle", 0)
    if angle != 0:
        return int(angle)
    
    # Check if text is likely rotated based on dimensions
    x0, y0, x1, y1 = span["bbox"]
    if abs(y1 - y0) > abs(x1 - x0) * 1.5:
        return 90
    
    return 0

def shrink_font_to_fit(page: fitz.Page, rect: fitz.Rect, text: str, fontname: str, 
                      original_size: float, color: Tuple[float, float, float], rotate: int) -> None:
    """
    Insert text with font size adjusted to fit within the rectangle.
    
    Args:
        page: PyMuPDF page object
        rect: Rectangle to fit text within
        text: Text to insert
        fontname: Font name
        original_size: Original font size
        color: Text color
        rotate: Rotation angle
    """
    # Try sizes from original down to minimum
    min_size = max(4, original_size * 0.3)  # Don't go below 30% of original or 4pt
    
    for size in range(int(original_size), int(min_size), -1):
        try:
            result = page.insert_textbox(
                rect,
                text,
                fontname=fontname,
                fontsize=size,
                color=color,
                rotate=rotate,
                align=0,
                render_mode=3  # Invisible mode for testing
            )
            if result >= 0:  # Success
                # Now insert visibly
                page.insert_textbox(
                    rect,
                    text,
                    fontname=fontname,
                    fontsize=size,
                    color=color,
                    rotate=rotate,
                    align=0
                )
                return
        except:
            continue
    
    # Fallback: insert with minimum size
    try:
        page.insert_textbox(
            rect,
            text,
            fontname=fontname,
            fontsize=int(min_size),
            color=color,
            rotate=rotate,
            align=0
        )
    except Exception as e:
        print(f"Failed to insert text: {text[:30]}... Error: {e}")

def translate_pdf(input_pdf_path: str, output_pdf_path: str, target_lang: str = "Spanish") -> None:
    """
    Translate a PDF document to the specified language.
    
    Args:
        input_pdf_path: Path to input PDF file
        output_pdf_path: Path for output translated PDF
        target_lang: Target language for translation
    """
    if not os.path.exists(input_pdf_path):
        raise FileNotFoundError(f"Input PDF not found: {input_pdf_path}")
    
    # if not os.getenv("OPENAI_API_KEY"):
    #     raise ValueError("OPENAI_API_KEY environment variable not set")
    
    doc = fitz.open(input_pdf_path)
    print(f"🔄 Starting translation of {len(doc)} pages to {target_lang}")

    for page_num, page in enumerate(doc):
        print(f"\n📄 Processing page {page_num + 1}/{len(doc)}")
        
        try:
            blocks = page.get_text("dict")["blocks"]
            text_elements = []

            # Extract text elements with their properties
            for block in blocks:
                if "lines" not in block:
                    continue
                
                for line in block["lines"]:
                    for span in line["spans"]:
                        text = span["text"].strip()
                        if not text or len(text) < 2:  # Skip very short text
                            continue
                        
                        bbox = span["bbox"]
                        
                        # Skip if bbox is invalid
                        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                            continue
                        
                        translated_text = translate_text(text, target_lang)
                        
                        text_elements.append({
                            'text': text,
                            'translated': translated_text,
                            'bbox': bbox,
                            'size': span.get("size", 12),
                            'font_type': get_font_type(span),
                            'rotation': infer_rotation(span),
                            'color': span.get("color", 0),
                        })

            print(f"   Found {len(text_elements)} text spans to translate")

            # Redact original text
            redaction_rects = []
            for el in text_elements:
                bbox = el["bbox"]
                # Add small padding to ensure complete coverage
                rect = fitz.Rect(
                    bbox[0] - 1,
                    bbox[1] - 1,
                    bbox[2] + 1,
                    bbox[3] + 1
                )
                
                bg_color = detect_background_color(page, bbox)
                redaction_rects.append((rect, bg_color))
                
                # Add redaction annotation
                page.add_redact_annot(rect, fill=bg_color)

            # Apply all redactions at once
            page.apply_redactions()

            # Insert translated text
            successful_insertions = 0
            for el in text_elements:
                translated = el["translated"]
                if not translated or translated == el["text"]:
                    continue
                
                try:
                    fontname = get_pymupdf_font(el["font_type"])
                    color = convert_color_to_rgb(el["color"])
                    bg_color = detect_background_color(page, el["bbox"])
                    
                    # Adjust text color for better contrast
                    if is_dark_color(bg_color) and sum(color) / 3 > 0.8:
                        color = (1, 1, 1)  # White text on dark background
                    elif not is_dark_color(bg_color) and sum(color) / 3 < 0.2:
                        color = (0, 0, 0)  # Black text on light background
                    
                    rect = fitz.Rect(el["bbox"])
                    shrink_font_to_fit(page, rect, translated, fontname, el["size"], color, el["rotation"])
                    successful_insertions += 1
                    
                except Exception as e:
                    print(f"   Failed to insert translation for: {el['text'][:30]}... Error: {e}")
                    continue

            print(f"   Successfully inserted {successful_insertions}/{len(text_elements)} translations")
            
        except Exception as e:
            print(f"   Error processing page {page_num + 1}: {e}")
            continue

    # Save the translated document
    doc.save(output_pdf_path)
    doc.close()
    print(f"\n✅ Translation completed! Saved to: {output_pdf_path}")

def main():
    """Main function to run the PDF translation."""
    try:
        # Configuration
        input_file = "test-p5.pdf"
        output_file = "final_output.pdf"
        target_language = "Spanish"
        
        # # Check if input file exists
        # if not os.path.exists(input_file):
        #     print(f"❌ Input file not found: {input_file}")
        #     print("Please ensure the PDF file exists in the current directory.")
        #     return
        
        # # Check API key
        # if not os.getenv("OPENAI_API_KEY"):
        #     print("❌ OpenAI API key not found!")
        #     print("Please set the OPENAI_API_KEY environment variable:")
        #     print("   export OPENAI_API_KEY='your-api-key-here'")
        #     return
        
        # Run translation
        translate_pdf(input_file, output_file, target_language)
        
    except Exception as e:
        print(f"❌ Error: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    main()
