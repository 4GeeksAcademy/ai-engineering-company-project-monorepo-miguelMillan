# Servidor de pruebas SIN endurecer: lo que suele entregar un VPS (root por SSH con contraseña, sin firewall).
FROM debian:12-slim
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends openssh-server openssh-client nftables iptables sudo \
      iproute2 procps python3 sshpass netcat-openbsd nmap >/dev/null && ssh-keygen -A >/dev/null \
    && printf 'PermitRootLogin yes\nPasswordAuthentication yes\n' > /etc/ssh/sshd_config.d/50-cloud-init.conf \
    && groupadd docker && mkdir -p /run/sshd
