FROM python:3.6-buster

ARG glider_gid_uid=1000

RUN apt-get update && \
    apt-get -y install rsync libxml2-dev libudunits2-dev && \
    mkdir glider-dac && \
    groupadd -g $glider_gid_uid glider && \
    useradd -u $glider_gid_uid -g $glider_gid_uid glider && \
    mkdir -p /data/data/priv_erddap /scratch/tomcat-erddap-private/flag

COPY . /glider-dac
WORKDIR /glider-dac

# not clear why reinstalling Mongo-related dependencies is necessary under
# Python 3, but this allows the service to run without import or runtime errors
RUN pip install --no-cache Cython thredds_crawler && \
    pip install --no-cache -r requirements.txt && \
    pip uninstall -y mongokit && \
    pip install --no-cache --force-reinstall mongokit-py3==0.9.1.1 && \
    pip install -U pymongo==2.8

RUN chown -R glider:glider /glider-dac/logs/
USER glider

# TODO: move logs elsewhere
VOLUME [ "/glider-dac/logs/", "/data/data/priv_erddap", "/scratch/tomcat-erddap-private/flag" ]
ENV PYTHONPATH="${PYTHONPATH}:/glider_dac"

EXPOSE 5000
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "app:app"]
