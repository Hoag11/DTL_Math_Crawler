import os
import time
import re
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from fpdf import FPDF
from PIL import Image
from io import BytesIO
import requests
import cairosvg

def latex_to_png_mathjax(latex, font_size=8, timeout=5):
    # Tạo HTML template có MathJax
    html = f"""
    <html>
    <head>
      <script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js"></script>
      <style>
        body {{ margin: 0; padding: 0; }}
        #math {{ font-size: {font_size}px; }}
      </style>
    </head>
    <body>
      <div id="math">\\[{latex}\\]</div>
      <script>
        MathJax.typesetPromise().then(() => {{
          var svg = document.querySelector('svg');
          svg.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
          window.svgSource = svg.outerHTML;
        }});
      </script>
    </body>
    </html>
    """

    # Lưu HTML ra file tạm
    temp_html = "mathjax_temp.html"
    with open(temp_html, "w", encoding="utf-8") as f:
        f.write(html)

    # Dùng Selenium headless để mở file
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--disable-gpu')
    options.add_argument('--no-sandbox')
    driver = webdriver.Chrome(options=options)
    driver.get("file://" + os.path.abspath(temp_html))
    # Đợi MathJax render
    for _ in range(timeout * 10):
        svg = driver.execute_script("return window.svgSource || null;")
        if svg:
            break
        time.sleep(0.1)
    else:
        driver.quit()
        os.remove(temp_html)
        raise RuntimeError("MathJax render timeout")
    driver.quit()
    os.remove(temp_html)

    # Convert SVG to PNG bytes
    png_bytes = cairosvg.svg2png(bytestring=svg.encode('utf-8'))
    return BytesIO(png_bytes)

def clean_latex_mathjax(latex):
    if latex is None:
        return ""
    return latex.strip()

def is_long_latex(latex):
    # Nếu có các ký hiệu phức tạp hoặc dài thì coi là dài
    return any(cmd in latex for cmd in [r'\frac', r'\sum', r'\int', r'\begin', r'\\', r'\sqrt', r'=', r'+', r'-', r'\cdot']) or len(latex) > 20

def setup_selenium():
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1280,1024')
    driver = webdriver.Chrome(options=options)
    return driver

def parse_table(table_tag):
    rows = []
    for tr in table_tag.find_all('tr'):
        row = []
        for th in tr.find_all('th'):
            row.append({'text': th.get_text(strip=True), 'bold': True})
        for td in tr.find_all('td'):
            row.append({'text': td.get_text(strip=True), 'bold': False})
        if row:
            rows.append(row)
    return rows

def parse_content_blocks(parent):
    blocks = parent.find_all(['p', 'table', 'img', 'ul', 'ol'], recursive=True)
    result = []
    for block in blocks:
        if block.name == 'table':
            table_data = parse_table(block)
            result.append({"type": "table", "data": table_data})
        elif block.name == 'img':
            img_url = block.get('src')
            if img_url and img_url.startswith("http"):
                result.append({"type": "img", "data": img_url})
        elif block.name in ['p', 'ul', 'ol']:
            paragraph = ""
            for elem in block.children:
                if getattr(elem, 'name', None) == "script" and elem.get("type") == "math/tex":
                    latex = clean_latex_mathjax(elem.string)
                    if latex:
                        paragraph += f"${latex}$"
                elif getattr(elem, 'name', None) is None:
                    text = str(elem)
                    if text.strip():
                        paragraph += text
            if paragraph.strip():
                result.append({"type": "text", "data": paragraph.strip()})
    return result

def extract_problem_and_solution(url):
    content = []
    driver = setup_selenium()
    driver.get(url)
    time.sleep(5)
    html_rendered = driver.page_source
    driver.quit()
    soup = BeautifulSoup(html_rendered, "html.parser")

    problem_div = soup.find("div", id="sub-question-1")
    solution_div = soup.find("div", id="sub-question-2")

    content.append({"type": "header", "data": "ĐỀ BÀI"})
    if problem_div:
        content += parse_content_blocks(problem_div)

    content.append({"type": "solution_header", "data": "LỜI GIẢI"})
    if solution_div:
        content += parse_content_blocks(solution_div)

    return content

def draw_table(pdf, table_data, page_width):
    table_width = page_width * 0.95
    col_count = max(len(row) for row in table_data)
    col_width = table_width / col_count

    for row in table_data:
        y_before = pdf.get_y()
        x = pdf.get_x()
        max_height = 8
        cell_heights = []
        for cell in row:
            pdf.set_font("DejaVu", "B", 10) if cell.get('bold') else pdf.set_font("DejaVu", "", 10)
            n_lines = max(1, int(pdf.get_string_width(cell['text']) / col_width))
            cell_heights.append(8 * n_lines)
        max_height = max(cell_heights) if cell_heights else 8

        for idx, cell in enumerate(row):
            pdf.set_xy(x + idx * col_width, y_before)
            pdf.set_font("DejaVu", "B", 10) if cell.get('bold') else pdf.set_font("DejaVu", "", 10)
            pdf.multi_cell(col_width, 8, cell['text'], border=1, align='L')
            pdf.set_xy(x + (idx + 1) * col_width, y_before)
        pdf.set_y(y_before + max_height)
    pdf.set_font("DejaVu", "", 10)

def download_image(url):
    try:
        response = requests.get(url)
        response.raise_for_status()
        return BytesIO(response.content)
    except Exception as e:
        print(f"Lỗi tải ảnh: {e}")
        return None

def render_text_with_latex(pdf, text, page_width):
    parts = re.split(r'(\$.*?\$)', text)
    for part in parts:
        if part.startswith('$') and part.endswith('$'):
            latex = part[1:-1]
            if is_long_latex(latex):
                try:
                    img_buf = latex_to_png_mathjax(latex, font_size=6)
                    img = Image.open(img_buf)
                    w, h = img.size
                    max_w = page_width * 0.25
                    max_h = 10
                    scale = min(max_w / w, max_h / h, 1)
                    new_w, new_h = w * scale, h * scale
                    pdf.ln(2)  # Thêm khoảng cách trước công thức
                    if pdf.get_y() + new_h > pdf.h - pdf.b_margin:
                        pdf.add_page()
                    img_buf.seek(0)
                    x_pos = pdf.l_margin
                    pdf.image(img_buf, x=x_pos, w=new_w, h=new_h)
                    pdf.ln(new_h + 2)  # Thêm khoảng cách sau công thức
                except Exception as e:
                    pdf.set_text_color(255, 0, 0)
                    pdf.set_font("DejaVu", "I", 9)
                    pdf.multi_cell(page_width, 6, f"[Lỗi LaTeX: {str(e)}]\n{part}", align='L')
                    pdf.set_text_color(0, 0, 0)
                    pdf.set_font("DejaVu", "", 10)
            else:
                pdf.set_font("DejaVu", "I", 10)
                pdf.write(6, part)
                pdf.set_font("DejaVu", "", 10)
        else:
            if part.strip():
                pdf.set_font("DejaVu", "", 10)
                pdf.write(6, part)
    pdf.ln(8)
    
def generate_pdf(content, output_path, font_path):
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_left_margin(15)
    pdf.set_right_margin(15)
    pdf.add_page()
    font_dir = os.path.dirname(font_path)
    pdf.add_font("DejaVu", "", os.path.join(font_dir, "DejaVuSans.ttf"))
    pdf.add_font("DejaVu", "B", os.path.join(font_dir, "DejaVuSans-Bold.ttf"))
    pdf.add_font("DejaVu", "I", os.path.join(font_dir, "DejaVuSans-Oblique.ttf"))
    pdf.add_font("DejaVu", "BI", os.path.join(font_dir, "DejaVuSans-BoldOblique.ttf"))
    pdf.set_font("DejaVu", size=10)
    page_width = pdf.w - pdf.l_margin - pdf.r_margin
    for item in content:
        try:
            if item["type"] == "header":
                pdf.set_font("DejaVu", "B", 14)
                pdf.cell(page_width, 10, item["data"], new_x="LMARGIN", new_y="NEXT", align='L')
                pdf.ln(2)
                pdf.set_font("DejaVu", size=10)
            elif item["type"] == "solution_header":
                pdf.add_page()
                pdf.set_font("DejaVu", "B", 14)
                pdf.cell(page_width, 10, item["data"], new_x="LMARGIN", new_y="NEXT", align='L')
                pdf.ln(2)
                pdf.set_font("DejaVu", size=10)
            elif item["type"] == "text":
                render_text_with_latex(pdf, item["data"], page_width)
            elif item["type"] == "table":
                draw_table(pdf, item["data"], page_width)
                pdf.ln(2)
            elif item["type"] == "img":
                img_buf = download_image(item["data"])
                if img_buf:
                    img = Image.open(img_buf)
                    w, h = img.size
                    max_w = page_width * 0.6
                    max_h = 60
                    scale = min(max_w / w, max_h / h, 1)
                    new_w, new_h = w * scale, h * scale
                    if new_h > max_h or new_w > max_w:
                        pdf.add_page()
                        new_h = min(new_h, pdf.h - pdf.get_y() - pdf.b_margin - 10)
                    img_buf.seek(0)
                    x_pos = pdf.l_margin
                    pdf.image(img_buf, x=x_pos, w=new_w, h=new_h)
                    pdf.ln(new_h + 2)
        except Exception as e:
            pdf.set_text_color(255, 0, 0)
            pdf.multi_cell(page_width, 5, f"[Lỗi xử lý: {str(e)}]", align='L')
            pdf.set_text_color(0, 0, 0)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    pdf.output(output_path)
    print(f"Đã lưu PDF tại: {output_path}")

if __name__ == "__main__":
    url = "https://loigiaihay.com/de-tham-khao-thi-vao-10-toan-tp-ho-chi-minh-nam-2025-co-dap-an-va-loi-giai-chi-tiet-de-1-a179544.html"
    font_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
    font_path = os.path.join(font_dir, "DejaVuSans.ttf")
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
    output_path = os.path.join(output_dir, "bai_giai.pdf")
    os.makedirs(output_dir, exist_ok=True)
    content = extract_problem_and_solution(url)
    print(content)
    generate_pdf(content, output_path, font_path)
    print(f"Đã tạo file PDF với đề bài và lời giải tại: {output_path}")