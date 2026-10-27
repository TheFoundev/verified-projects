# Message Broker Backend for Microservices

An Erlang/OTP order-processing backend on RabbitMQ quorum queues: a publisher
with confirms, a consumer with a retry ladder, a Prometheus endpoint and a
small HTTP interface.

## Overview

The application accepts orders over HTTP, publishes them to a topic exchange,
consumes them from a quorum queue and runs them through a four-step pipeline.
Every failure is classified as **transient** (retry) or **terminal** (dead
letter), so a broken order never blocks the queue and a transient outage never
loses an order.

```text
POST /orders ──▶ exchange "orders" ──▶ orders.process ──▶ validate
                                                              │
                            ┌─────────────────────────────────┤
                            ▼                                 ▼
                   reserve inventory ──▶ charge ──▶ fulfil   retry ladder
                            │                                 │
                            └────────── dead letter ◀─────────┘
```

### The modules

| Module | What it does |
| --- | --- |
| `mbb_sup` | The supervision tree (one supervisor per process). |
| `mbb_connection` | Owns the AMQP connection and the channel; reconnects with backoff and re-declares the topology after a reconnect. |
| `mbb_topology` | Declares the exchange, the queues and the bindings (idempotently). |
| `mbb_publisher` | Publishes orders with publisher confirms and an idempotency key. |
| `mbb_consumer` | Consumes with a prefetch window, classifies the outcome and settles the delivery (ack, retry or dead letter). |
| `mbb_order` | The pure domain: the pipeline, the JSON codec and the idempotency key. No side effects. |
| `mbb_metrics` | Prometheus counters, gauges and the latency histogram. |
| `mbb_http` | The `inets/httpd` interface: `/health`, `/ready`, `/metrics`, `/orders`. |
| `mbb_burst` | A generator that fires synthetic orders at a configurable rate. |

## The order pipeline

`mbb_order:process/1` runs four steps and returns one of three outcomes:

| Outcome | Meaning |
| --- | --- |
| `ok` | The order was processed. |
| `{retry, Reason}` | A transient failure. The consumer republishes it into the next retry queue. |
| `{dead_letter, Reason}` | A terminal failure. The consumer publishes it to the dead-letter queue. |

The steps:

1. **validate** — the order id, the currency, the items and the amount.
   Structural problems are terminal: retrying them can never succeed.
2. **reserve inventory** — the requested quantity against the stock.
   Exceeding the stock is terminal, a very large order is transient
   (`inventory_backend_busy`).
3. **charge** — the amount against the payment gateway. An amount over the
   limit is terminal (`amount_over_limit`), a gateway timeout is transient.
4. **fulfil** — the hand-off to the warehouse. A warehouse that is down is
   transient (`warehouse_unavailable`).

The inventory and the payment gateway are **simulated inside `mbb_order`** with
deterministic rules (see the knobs `STOCK`, `CHARGE_LIMIT`,
`INVENTORY_BUSY_QTY` and `CHARGE_RETRY_DIV`), so every branch of the pipeline
is reachable in a unit test without a broker, a database or a clock. The seam
is the two functions `mbb_order:reserve_inventory/1` and `mbb_order:charge/1`:
replace their bodies with HTTP calls to the real services, keep their
`{ok, Order} | {error, Reason}` contract, and the rest of the application does
not change.

## The topology

```text
exchange "orders" (topic)
  |-- order.created, order.retry ──▶ orders.process   (quorum)
  |-- order.retry.N ───────────────▶ orders.retry.N   (quorum, x-message-ttl,
  │                                     dead-letters back to "orders")
  |-- order.dlq ────────────────────▶ orders.dlq      (quorum)
  |-- order.charged ────────────────▶ orders.billing  (per service)
  `-- order.fulfilled ──────────────▶ orders.shipping (per service)
```

The retry ladder is a set of queues whose TTL grows with the retry number
(`retry_ttl_base_ms * 4^(N-1)`). A message that lands in `orders.retry.N`
waits for its TTL and is dead-lettered back to `orders.process` with
`x-retry-count` incremented. When the count reaches `max_retries` the consumer
sends the order to `orders.dlq` with `x-dlq-reason: retries_exhausted`
instead of looping. `orders.process` also has an `x-delivery-limit`, so a
message that is redelivered too often without being settled lands in the
dead-letter queue on its own.

## The HTTP interface

| Route | Answer |
| --- | --- |
| `GET /health` | `200` while the application runs. |
| `GET /ready` | `200` when the AMQP connection is up and the topology is declared, `503` otherwise. |
| `GET /metrics` | The Prometheus text exposition of the counters, the gauges and the latency histogram. |
| `POST /orders` | A JSON order. `202` when it was published, `400` on a bad body, `503` when the broker is down or blocked. |

## Configuration

Every key of `config/sys.config` can be overridden with an OS variable that
starts with `MBB_` (see `mbb_connection:cfg/1`): `MBB_AMQP_HOST`,
`MBB_AMQP_PORT`, `MBB_AMQP_USERNAME`, `MBB_AMQP_PASSWORD`, `MBB_AMQP_VHOST`,
`MBB_HTTP_PORT`, `MBB_MAX_RETRIES`, `MBB_RETRY_TTL_BASE_MS`, ...

| Key | Default | Meaning |
| --- | --- | --- |
| `amqp_host`, `amqp_port` | `localhost`, `5672` | The broker. |
| `amqp_username`, `amqp_password`, `amqp_vhost` | `guest`, `guest`, `/` | The credentials. |
| `heartbeat_seconds` | `30` | The AMQP heartbeat. |
| `prefetch_count` | `32` | Unacked deliveries allowed in flight (the backpressure knob). |
| `backoff_base_ms`, `backoff_max_ms` | `500`, `30000` | The reconnect backoff. |
| `max_retries` | `3` | How many times an order is retried before it is dead-lettered. |
| `retry_ttl_base_ms` | `5000` | The TTL of the first retry queue. |
| `confirm_timeout_ms` | `5000` | How long a publisher confirm may take. |
| `publish_max_attempts` | `3` | How often a publish is retried. |
| `seen_ttl_ms` | `600000` | The lifetime of an entry in the idempotency seen-cache. |
| `http_port` | `8080` | The port of the HTTP interface. |
| `burst_count`, `burst_rate` | `500`, `200` | The defaults of the burst generator. |

## Run

With Docker (RabbitMQ, the backend and Prometheus):

```bash
docker compose up --build
curl localhost:8080/health
curl -X POST localhost:8080/orders \
  -H 'content-type: application/json' \
  -d '{"order_id":"ord-1","currency":"EUR","items":[{"sku":"SKU-1","qty":2}],"amount":100}'
open localhost:9090   # Prometheus
open localhost:15672  # the broker UI
```

Without Docker:

```bash
rebar3 compile
rebar3 shell          # starts the application against a local broker
```

## Testing

```bash
rebar3 eunit           # 53 tests, no broker needed
python3 tools/check_erl.py   # the structural checker (0 errors, 0 warnings)
```

The EUnit suite covers the parts that are pure: the order pipeline and its
validation, the retry/dead-letter classification, the retry ladder and the
backoff, the histogram buckets and the JSON codec. The modules that talk to
the broker (`mbb_connection`, `mbb_topology`, `mbb_publisher`,
`mbb_consumer`) are thin wrappers around the AMQP client and are exercised
by the integration stack of `docker-compose.yml`.

`tools/check_erl.py` is a hand-written structural checker (module name, ASCII,
balanced brackets, terminated forms, exports against specs). It is not a
compiler; `rebar3 eunit` is the real check.

## Requirements

- Erlang/OTP 26 or later. The suite was verified on OTP 29, which is what CI runs.
- `rebar3`.
- A RabbitMQ 3.13 broker with quorum queues for the full stack.

The dependencies are `amqp_client` and `rabbit_common` (the RabbitMQ Erlang
client) and `prometheus` (the exposition library), all fetched from hex.pm by
`rebar3` and pinned in `rebar.lock`: `amqp_client 4.3.4`, `rabbit_common 4.3.4`
and `prometheus 6.1.3`. The 3.13 series of `rabbit_common` was not used: it
does not build on a current OTP.

## Layout

```text
message-broker-backend-for-microservices/
├── src/                    the nine modules of the application
├── test/                   the three EUnit modules
├── config/                 sys.config and vm.args
├── rabbitmq/               the broker configuration
├── prometheus/             the scrape configuration
├── tools/check_erl.py      the structural checker
├── rebar.config            the build and the release
├── Dockerfile
└── docker-compose.yml
```

## License

See `LICENSE`.
