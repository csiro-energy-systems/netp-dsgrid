# This container only provides Spark. At some point when we stop churning so much we will make one
# that includes dsgrid software.

# USAGE:
# Do not run this while connected to the VPN. You may get a certificate error while downloading spark.
# docker build --tag spark_py310 --build-arg VERSION=x.y.z .

# This container can be converted to a Singularity container on Eagle with these commands:
# Save and upload the docker image to Eagle.
# $ docker save -o spark_py310_vx.y.z.tar spark_py310
# $ scp spark_py310_vx.y.z.tar <username>@eagle.hpc.nrel.gov:/projects/dsgrid/containers
# Acquire a compute node.
# $ export SINGULARITY_TMPDIR=/tmp/scratch
# $ module load singularity-container
# Create writable image for testing and development or read-only image for production.
# Writable
# $ singularity build --sandbox spark_py310 docker-archive://spark_py310_v0.1.0.tar
# Read-only
# $ singularity build spark_py310_v0.1.0.sif docker-archive://spark_py310_v0.1.0.tar

# Note: Apache provides a container with Spark and Python installed, but as of now it is Python 3.9
# and dsgrid requires 3.10. Whenever they have a newer Python, we can simplify this.
# The Apache container is
# FROM apache/spark-py

FROM python:3.10-slim
USER root

ARG VERSION
ARG SPARK_VERSION=3.3.1
ARG HADOOP_VERSION=3
ARG FULL_STR=spark-${SPARK_VERSION}-bin-hadoop${HADOOP_VERSION}

RUN if [ -z "$VERSION" ]; then echo "VERSION must be specified"; exit 1; fi
ENV CONTAINER_VERSION ${VERSION}

# Install OpenSSH to communicate between containers, and dropbear SSH server
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y ca-certificates jq git nano default-jdk procps sysstat \
    tini tmux tree vim wget openssh-client dropbear locales \
    && rm -rf /var/lib/apt/lists/*

# This prevents bash warnings on Eagle.
RUN sed -i '/en_US.UTF-8/s/^# //g' /etc/locale.gen && \
    locale-gen
ENV LANG en_US.UTF-8
ENV LANGUAGE en_US.UTF-8
ENV LC_ALL en_US.UTF-8

# Required for dropbear
ENV SPARK_SSH_OPTS="-p 2222 -o StrictHostKeyChecking=no"

# Set Dropbear port to 2222 (or whatever port was selected above)
RUN sed -i -e 's@\(DROPBEAR_PORT=\).*@\12222@' /etc/default/dropbear

RUN mkdir /data
RUN mkdir /datasets
RUN mkdir /nopt
RUN mkdir /projects
RUN mkdir /scratch

RUN echo "$VERSION" > /opt/version.txt

WORKDIR /opt
ENV SPARK_HOME=/opt/spark
RUN wget https://dlcdn.apache.org/spark/spark-${SPARK_VERSION}/${FULL_STR}.tgz \
	&& tar -xzf ${FULL_STR}.tgz \
	&& rm ${FULL_STR}.tgz \
	&& mv /opt/${FULL_STR} ${SPARK_HOME} \
	&& cp ${SPARK_HOME}/conf/spark-defaults.conf.template ${SPARK_HOME}/conf/spark-defaults.conf \
	&& cp ${SPARK_HOME}/conf/spark-env.sh.template ${SPARK_HOME}/conf/spark-env.sh \
	&& cp ${SPARK_HOME}/kubernetes/dockerfiles/spark/entrypoint.sh /opt/entrypoint.sh \
	&& chmod +x ${SPARK_HOME}/conf/spark-env.sh

RUN pip install ipython jupyter pandas pyarrow

ENV JAVA_HOME=/usr/lib/jvm/java-11-openjdk-amd64
ENV PATH $PATH:${SPARK_HOME}/bin:${SPARK_HOME}/sbin
ENV PYSPARK_DRIVER_PYTHON=ipython

RUN touch $HOME/.profile \
    && rm -rf $HOME/.cache

WORKDIR ${SPARK_HOME}/work-dir
RUN chmod g+w ${SPARK_HOME}/work-dir

ENTRYPOINT [ "/opt/entrypoint.sh" ]
