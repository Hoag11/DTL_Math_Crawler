from airflow import DAG
from airflow.operators.python import PythonOperator
from datetime import datetime, timedelta
import logging
import os

from MathCrawler import get_links, extract_problem_and_solution, save_to_bigquery, generate_pdf, FONT_PATH, OUTPUT_IMG_DIR

MAIN_URL = "https://loigiaihay.com/tong-hop-50-de-thi-vao-10-mon-toan-e10842.html"

default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'retries': 2,
    'retry_delay': timedelta(minutes=5),
}

with DAG(
    'math_crawler_pipeline',
    default_args=default_args,
    description='Lấy link, crawl dữ liệu, gửi lên BigQuery và xuất PDF',
    schedule_interval='0 8 * * *',
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=['math', 'crawler'],
) as dag:

    def task_get_links(**context):
        links = get_links(MAIN_URL)
        logging.info(f"Số lượng link lấy được: {len(links)}")
        for i, link in enumerate(links):
            logging.info(f"Link {i+1}: {link}")
        if not links:
            logging.warning("Không lấy được link nào!")
        context['ti'].xcom_push(key='links', value=links)

    def task_extract_problem_and_solution(**context):
        links = context['ti'].xcom_pull(key='links', task_ids='get_links')
        all_contents = []
        if not links:
            logging.warning("Không có link nào để crawl!")
            return
        for idx, url in enumerate(links):
            try:
                logging.info(f"Đang crawl link {idx+1}/{len(links)}: {url}")
                content = extract_problem_and_solution(url)
                logging.info(f"  -> Crawl thành công, số block: {len(content)}")
                all_contents.append({'url': url, 'content': content})
            except Exception as e:
                logging.error(f"Lỗi crawl link {url}: {e}")
        context['ti'].xcom_push(key='all_contents', value=all_contents)

    def task_save_to_bigquery(**context):
        all_contents = context['ti'].xcom_pull(key='all_contents', task_ids='extract_problem_and_solution')
        if not all_contents:
            logging.warning("Không có nội dung nào để lưu BigQuery!")
            return
        for idx, item in enumerate(all_contents):
            content = item['content']
            de_bai = ""
            loi_giai = ""
            is_loi_giai = False
            for entry in content:
                if entry["type"] == "solution_header":
                    is_loi_giai = True
                elif entry["type"] == "text":
                    if is_loi_giai:
                        loi_giai += entry["data"] + "\n"
                    else:
                        de_bai += entry["data"] + "\n"
            try:
                save_to_bigquery(de_bai, loi_giai)
                logging.info(f"Đã lưu BigQuery cho link {idx+1}")
            except Exception as e:
                logging.error(f"Lỗi lưu BigQuery cho link {idx+1}: {e}")

    def task_generate_pdf(**context):
        all_contents = context['ti'].xcom_pull(key='all_contents', task_ids='extract_problem_and_solution')
        if not all_contents:
            logging.warning("Không có nội dung nào để xuất PDF!")
            return
        for idx, item in enumerate(all_contents):
            content = item['content']
            pdf_name = f"bai_giai_{idx+1}.pdf"
            output_path = os.path.join(OUTPUT_IMG_DIR, pdf_name)
            try:
                generate_pdf(content, output_path, FONT_PATH)
                logging.info(f"Đã lưu PDF: {output_path}")
            except Exception as e:
                logging.error(f"Lỗi xuất PDF {output_path}: {e}")

    t1 = PythonOperator(
        task_id='get_links',
        python_callable=task_get_links,
        provide_context=True,
    )
    t2 = PythonOperator(
        task_id='extract_problem_and_solution',
        python_callable=task_extract_problem_and_solution,
        provide_context=True,
    )
    t3 = PythonOperator(
        task_id='save_to_bigquery',
        python_callable=task_save_to_bigquery,
        provide_context=True,
    )
    t4 = PythonOperator(
        task_id='generate_pdf',
        python_callable=task_generate_pdf,
        provide_context=True,
    )

    t1 >> t2 >> [t3, t4]