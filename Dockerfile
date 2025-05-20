FROM apache/airflow:2.5.0-python3.8

USER root

RUN apt-get update && apt-get install -y libcairo2 libpango-1.0-0 \
 libpangocairo-1.0-0 libgdk-pixbuf2.0-0

USER airflow


COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ENV AIRFLOW_HOME=/opt/airflow

COPY dags/ $AIRFLOW_HOME/dags/

CMD ["webserver"]