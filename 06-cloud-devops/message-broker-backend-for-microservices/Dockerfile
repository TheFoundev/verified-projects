# Multi-stage build: compile with rebar3 + full OTP, run on a slim
# runtime copy that carries only beams, deps and config.
#
# NOTE: not executed in this environment (docker not installed); see the
# Verification section of README.md for what was actually run.

# ---------------------------------------------------------------------
# Stage 1: build
# ---------------------------------------------------------------------
FROM erlang:27-alpine AS build
WORKDIR /build

# The official erlang image ships rebar3 (see adoptingerlang.org docs).
RUN command -v rebar3

COPY rebar.config ./
COPY src ./src
COPY config ./config

RUN rebar3 compile

# ---------------------------------------------------------------------
# Stage 2: runtime
# ---------------------------------------------------------------------
FROM erlang:27-alpine AS runtime

RUN addgroup -S mbb && adduser -S -G mbb mbb

WORKDIR /app

# Compiled application + fetched dependencies (amqp_client, rabbit_common,
# prometheus) as built by rebar3.
COPY --from=build /build/_build/default/lib /app/lib
COPY config ./config

ENV ERL_LIBS=/app/lib
# docker-compose network: the broker's container name is "rabbitmq".
ENV MBB_AMQP_HOST=rabbitmq

USER mbb
EXPOSE 8080

# -config takes the name without the .config extension (erl(1): the flag
# resolves "<name>.config"), vm.args supplies -sname/-setcookie/+flags.
# -eval starts the OTP application and blocks forever so -noshell never
# sees the eval phase end and terminate the node.
CMD ["erl", "-noshell", "-noinput", \
     "-config", "/app/config/sys", \
     "-args_file", "/app/config/vm.args", \
     "-eval", "{ok, _} = application:ensure_all_started(message_broker_backend), receive after infinity -> ok end."]

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8080/health || exit 1
