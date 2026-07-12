FROM ubuntu:20.04@sha256:c664f8f86ed5a386b0a340d981b8f81714e21a8b9c73f658c4bea56aa179d54a AS materializer

ARG DEBIAN_FRONTEND=noninteractive
ARG DEFECTS4J_REVISION=6d54320e0db5a357f9ab38a8e4d2e5aead7e1c09

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        cpanminus \
        curl \
        git \
        make \
        openjdk-11-jdk \
        perl \
        subversion \
        unzip \
    && rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/usr/lib/jvm/java-11-openjdk-amd64
ENV TZ=America/Los_Angeles
RUN ln -snf /usr/share/zoneinfo/${TZ} /etc/localtime \
    && printf '%s\n' "${TZ}" > /etc/timezone

RUN git clone https://github.com/rjust/defects4j.git /defects4j \
    && git -C /defects4j checkout --detach "${DEFECTS4J_REVISION}" \
    && test "$(git -C /defects4j rev-parse HEAD)" = "${DEFECTS4J_REVISION}"

WORKDIR /defects4j
RUN cpanm --installdeps . \
    && ./init.sh \
    && mkdir -p /opt/autobugfix \
    && printf '%s\n' "${DEFECTS4J_REVISION}" > /opt/autobugfix/defects4j-revision

ENV PATH=/defects4j/framework/bin:${PATH}
LABEL org.autobugfix.benchmark="defects4j" \
      org.autobugfix.defects4j.revision="6d54320e0db5a357f9ab38a8e4d2e5aead7e1c09" \
      org.autobugfix.defects4j.role="materializer"

CMD ["bash"]

FROM materializer AS verifier

RUN find /defects4j/framework/projects \
      \( -path '*/patches/*' \
      -o -path '*/modified_classes/*' \
      -o -path '*/loaded_classes/*' \
      -o -path '*/relevant_tests/*' \
      -o -path '*/trigger_tests/*' \
      -o -path '*/failing_tests/*' \
      -o -path '*/dependent_tests/*' \) \
      -type f -delete \
    && find /defects4j/project_repos -mindepth 1 -maxdepth 1 \
      ! -name README -exec rm -rf {} + \
    && rm -rf /defects4j/.git \
    && test ! -d /defects4j/.git \
    && test -f /defects4j/project_repos/README \
    && test -z "$(find /defects4j/project_repos -mindepth 1 -maxdepth 1 \
      ! -name README -print -quit)" \
    && test -z "$(find /defects4j/framework/projects -type f \
      \( -path '*/patches/*' \
      -o -path '*/modified_classes/*' \
      -o -path '*/loaded_classes/*' \
      -o -path '*/relevant_tests/*' \
      -o -path '*/trigger_tests/*' \) -print -quit)"

LABEL org.autobugfix.benchmark="defects4j" \
      org.autobugfix.defects4j.revision="6d54320e0db5a357f9ab38a8e4d2e5aead7e1c09" \
      org.autobugfix.defects4j.role="verifier"
