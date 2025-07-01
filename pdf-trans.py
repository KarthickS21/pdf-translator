import fitz  # PyMuPDF
from openai import OpenAI
import time
from collections import Counter
import traceback
import os

client = OpenAI(api_key="test")

def translate_text(text, target_lang="Spanish", retries=3):
    if not text.strip():
        return text
    prompt = f"Translate the following English text to {target_lang}, preserving format and tone. Only return the translation:\n\n{text}"
    for attempt in range(retries):
        try:
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"Retrying translation due to error: {e}")
            time.sleep(2 ** attempt)
    return text  # fallback

def get_font_type(span):
    font_name = span.get('font', '').lower()
    flags = span.get('flags', 0)
    if 'bold' in font_name or flags == 20:
        return 'bold'
    elif 'light' in font_name or flags == 4:
        return 'light'
    else:
        return 'regular'

def get_pymupdf_font(font_type):
    font_map = {
        'bold': 'helv-bold',
        'light': 'helv',
        'regular': 'helv'
    }
    return font_map.get(font_type, 'helv')

def convert_color_to_rgb(color_value):
    if isinstance(color_value, (int, float)):
        if color_value == 0:
            return (0, 0, 0)
        elif color_value == 1 or color_value >= 16777215:
            return (1, 1, 1)
        else:
            r = ((color_value >> 16) & 255) / 255.0
            g = ((color_value >> 8) & 255) / 255.0
            b = (color_value & 255) / 255.0
            return (r, g, b)
    elif isinstance(color_value, (list, tuple)) and len(color_value) >= 3:
        return tuple(color_value[:3])
    else:
        return (0, 0, 0)

def translate_pdf(input_pdf_path, output_pdf_path, target_lang="Spanish"):
    doc = fitz.open(input_pdf_path)
    total_pages = len(doc)

    for page_num in range(total_pages):
        page = doc[page_num]
        print(f"\nTranslating page {page_num + 1}/{total_pages}")
        blocks = page.get_text("dict")["blocks"]
        text_elements = []

        for block in blocks:
            if "lines" in block:
                for line in block["lines"]:
                    for span in line["spans"]:
                        original_text = span["text"].strip()
                        if original_text:
                            font_type = get_font_type(span)
                            bbox = span["bbox"]
                            text_elements.append({
                                'text': original_text,
                                'bbox': bbox,
                                'size': span["size"],
                                'flags': span.get("flags", 0),
                                'font_type': font_type,
                                'original_font': span.get('font', ''),
                                'origin': span.get("origin", (bbox[0], bbox[3] - 2)),
                                'rotation': span.get("text_angle", 0),
                                'color': span.get('color', 0)
                            })

        print(f"Found {len(text_elements)} text elements to translate")

        # Step 1: Redact (erase) all original text
        for element in text_elements:
            rect = fitz.Rect(element["bbox"])
            rect.x0 -= 0.5
            rect.y0 -= 0.5
            rect.x1 += 0.5
            rect.y1 += 0.5
            page.add_redact_annot(rect, fill=None)

        page.apply_redactions()


        # Step 2: Add translated text
        for i, element in enumerate(text_elements):
            original_text = element['text']
            print(f"  Translating ({i+1}/{len(text_elements)}): {original_text[:40]}...")
            print(f"    Font type: {element['font_type']} (original: {element['original_font']})")

            translated_text = translate_text(original_text, target_lang)
            pymupdf_font = get_pymupdf_font(element['font_type'])
            text_color = convert_color_to_rgb(element['color'])
            x, y = element['origin']

            try:
                if element['font_type'] == 'bold':
                    # Try bold font, fallback to simulated bold
                    try:
                        page.insert_text(
                            point=(x, y),
                            text=translated_text,
                            fontsize=element['size'],
                            fontname="helv-bold",
                            color=text_color,
                            rotate=element['rotation']
                        )
                    except:
                        page.insert_text(
                            point=(x, y),
                            text=translated_text,
                            fontsize=element['size'],
                            fontname="helv",
                            color=text_color,
                            rotate=element['rotation']
                        )
                        page.insert_text(
                            point=(x + 0.5, y),
                            text=translated_text,
                            fontsize=element['size'],
                            fontname="helv",
                            color=text_color,
                            rotate=element['rotation']
                        )
                else:
                    page.insert_text(
                        point=(x, y),
                        text=translated_text,
                        fontsize=element['size'],
                        fontname="helv",
                        color=text_color,
                        rotate=element['rotation']
                    )
            except Exception as e:
                print(f"    Error writing text: {e}")
                page.insert_text(
                    point=(x, y),
                    text=translated_text,
                    fontsize=element['size'],
                    fontname="helv",
                    color=(0, 0, 0),
                    rotate=element['rotation']
                )

    doc.save(output_pdf_path)
    doc.close()
    print(f"\n✅ Translation complete. Saved to: {output_pdf_path}")

# Run the translator
if __name__ == "__main__":
    try:
        translate_pdf("test-p5.pdf", "translated_output_clean_v1.pdf", target_lang="Spanish")
    except Exception as e:
        print(f"❌ Error: {e}")
        traceback.print_exc()
