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
import logging
from google.cloud import bigquery
from urllib.parse import urlparse

FONT_PATH = "/opt/airflow/fonts/DejaVuSans.ttf"
OUTPUT_IMG_DIR = "/opt/airflow/output"

def get_links(main_url):
    resp = requests.get(main_url)
    soup = BeautifulSoup(resp.text, "html.parser")
    links = []
    for li in soup.find_all("li"):
        a = li.find("a", href=True)
        if not a:
            continue
        span = a.find("span")
        if span and span.get_text(strip=True).startswith("Đề số"):
            href = a['href']
            if href.startswith('http'):
                links.append(href)
            else:
                links.append('https://loigiaihay.com' + href)
    return links

def latex_to_png_mathjax(latex, font_size=8, timeout=5, driver=None):
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

    temp_html = "mathjax_temp.html"
    with open(temp_html, "w", encoding="utf-8") as f:
        f.write(html)

    if driver is None:
        driver = setup_selenium()
        close_driver = True
    else:
        close_driver = False

    driver.get("file://" + os.path.abspath(temp_html))
    for _ in range(timeout * 30):
        svg = driver.execute_script("return window.svgSource || null;")
        if svg:
            break
        time.sleep(0.1)
    else:
        if close_driver:
            driver.quit()
        os.remove(temp_html)
        raise RuntimeError("MathJax render timeout")
    if close_driver:
        driver.quit()
    os.remove(temp_html)
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
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) " \
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
    SELENIUM_URL = os.environ.get("SELENIUM__URL", "http://chrome:4444/wd/hub")
    driver = webdriver.Remote(command_executor=SELENIUM_URL, options=options)
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

    if problem_div or solution_div:
        content.append({"type": "header", "data": "ĐỀ BÀI"})
        if problem_div:
            content += parse_content_blocks(problem_div)
        content.append({"type": "solution_header", "data": "LỜI GIẢI"})
        if solution_div:
            content += parse_content_blocks(solution_div)
        return content

    
    # Tìm <p><strong class="content_question">Đề bài</strong></p>
    question_tag = soup.find("strong", class_="content_question")
    detail_tag = soup.find("strong", class_="content_detail")

    if question_tag:
        content.append({"type": "header", "data": "ĐỀ BÀI"})
        # Lấy các <p> sau <strong class="content_question"> cho đến <strong class="content_detail">
        p = question_tag.find_parent("p")
        for sib in p.find_next_siblings():
            # Dừng lại nếu gặp <strong class="content_detail">
            strong = sib.find("strong", class_="content_detail")
            if strong:
                break
            if sib.name in ["p", "ul", "ol", "table"]:
                content += parse_content_blocks(sib)
    if detail_tag:
        content.append({"type": "solution_header", "data": "LỜI GIẢI"})
        # Lấy các <p> sau <strong class="content_detail">
        p = detail_tag.find_parent("p")
        for sib in p.find_next_siblings():
            if sib.name in ["p", "ul", "ol", "table"]:
                content += parse_content_blocks(sib)

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
        logging.info(f"Lỗi tải ảnh: {e}")
        return None

def render_text_with_latex(pdf, text, page_width, driver=None):
    parts = re.split(r'(\$.*?\$)', text)
    for part in parts:
        if part.startswith('$') and part.endswith('$'):
            latex = part[1:-1]
            if is_long_latex(latex):
                try:
                    img_buf = latex_to_png_mathjax(latex, font_size=6, driver=driver)
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
    from selenium.common.exceptions import WebDriverException

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

    # Tạo 1 driver duy nhất cho cả file PDF
    driver = None
    try:
        driver = setup_selenium()
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
                    render_text_with_latex(pdf, item["data"], page_width, driver=driver)
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
    except WebDriverException as e:
        logging.error(f"Lỗi khởi tạo Selenium driver: {e}")
        pdf.set_text_color(255, 0, 0)
        pdf.multi_cell(page_width, 5, f"[Lỗi khởi tạo Selenium driver: {str(e)}]", align='L')
        pdf.set_text_color(0, 0, 0)
    finally:
        if driver:
            driver.quit()
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    pdf.output(output_path)
    logging.info(f"Đã lưu PDF tại: {output_path}")

def save_to_bigquery(de_bai, loi_giai):
    client = bigquery.Client()
    dataset_id = 'Math'
    table_id = 'MathEx'
    table_ref = client.dataset(dataset_id).table(table_id)
    rows_to_insert = [
        {"de_bai": de_bai, "loi_giai": loi_giai}
    ]
    errors = client.insert_rows_json(table_ref, rows_to_insert)
    if errors:
        logging.error(f"Lỗi: {errors}")
    else:
        logging.info("Thêm dòng thành công vào BigQuery.")