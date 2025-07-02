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

def detect_background_color(page, bbox, zoom=2):
    try:
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)

        x0, y0, x1, y1 = [int(v * zoom) for v in bbox]
        margin = 4 * zoom

        sample_coords = [
            (max(0, x0 - margin), max(0, y0 - margin)),
            (min(pix.width-1, x1 + margin), max(0, y0 - margin)),
            (max(0, x0 - margin), min(pix.height-1, y1 + margin)),
            (min(pix.width-1, x1 + margin), min(pix.height-1, y1 + margin)),
            ((x0 + x1) // 2, (y0 + y1) // 2),
        ]

        colors = []
        for x, y in sample_coords:
            try:
                r, g, b = pix.pixel(x, y)[:3]
                colors.append((round(r / 255, 2), round(g / 255, 2), round(b / 255, 2)))
            except:
                continue

        if colors:
            rounded_colors = [tuple(round(c, 1) for c in color) for color in colors]
            return Counter(rounded_colors).most_common(1)[0][0]
        return (1, 1, 1)
    except Exception as e:
        print(f"BG detect error: {e}")
        return (1, 1, 1)

def is_dark_color(rgb, threshold=0.4):
    r, g, b = rgb
    return (r + g + b) / 3 < threshold

def translate_pdf(input_pdf_path, output_pdf_path, target_lang="Spanish"):
    doc = fitz.open(input_pdf_path)

    for page_num, page in enumerate(doc):
        print(f"\nTranslating page {page_num + 1}/{len(doc)}")
        blocks = page.get_text("dict")["blocks"]
        text_elements = []

        for block in blocks:
            if "lines" in block:
                for line in block["lines"]:
                    for span in line["spans"]:
                        original_text = span["text"].strip()
                        if original_text:
                            bbox = span["bbox"]
                            font_type = get_font_type(span)
                            text_elements.append({
                                'text': original_text,
                                'bbox': bbox,
                                'size': span["size"],
                                'flags': span.get("flags", 0),
                                'font_type': font_type,
                                'original_font': span.get('font', ''),
                                'origin': span.get("origin", (bbox[0], bbox[3] - 2)),
                                'rotation': span.get("text_angle", 0),
                                'color': span.get('color', 0),
                            })

        print(f"Found {len(text_elements)} text elements to translate")

        # Redact original text
        for el in text_elements:
            rect = fitz.Rect(el["bbox"])
            rect.x0 -= 0.5
            rect.y0 -= 0.5
            rect.x1 += 0.5
            rect.y1 += 0.5

            bg_color = detect_background_color(page, rect)
            page.add_redact_annot(rect, fill=bg_color)

        page.apply_redactions()

        # Add translated text
        for i, el in enumerate(text_elements):
            print(f"  Translating ({i+1}/{len(text_elements)}): {el['text'][:40]}...")

            #translated = translate_text(el['text'], target_lang)
            translated = el['text']
            font = get_pymupdf_font(el['font_type'])
            x, y = el['origin']
            rotation = el['rotation']
            text_color = convert_color_to_rgb(el['color'])

            # Force black if background is dark and text is light
            bg_color = detect_background_color(page, el['bbox'])
            if is_dark_color(bg_color) and sum(text_color) / 3 > 0.8:
                text_color = (0, 0, 0)

            try:
                page.insert_text(
                    point=(x, y),
                    text=translated,
                    fontsize=el['size'],
                    fontname=font,
                    color=text_color,
                    rotate=rotation
                )
            except Exception as e:
                print(f"    Error: {e} — using fallback font")
                page.insert_text(
                    point=(x, y),
                    text=translated,
                    fontsize=el['size'],
                    fontname="helv",
                    color=text_color,
                    rotate=rotation
                )

    doc.save(output_pdf_path)
    doc.close()
    print(f"\n✅ Translation complete. Saved to: {output_pdf_path}")

# Run the translation
if __name__ == "__main__":
    try:
        translate_pdf("test-p5.pdf", "o1.pdf", target_lang="Spanish")
    except Exception as e:
        print(f"❌ Error: {e}")
        traceback.print_exc()
