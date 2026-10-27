%%% @doc Idempotent AMQP topology declaration.
%%%
%%% Topology (all durable; queues are quorum queues):
%%%
%%% <pre>
%%%   exchange "orders" (topic)
%%%     |-- bind order.created, order.retry --> queue orders.process (quorum)
%%%     |-- bind order.retry.N --------------> queue orders.retry.N (quorum,
%%%     |                                        x-message-ttl, DLX back to
%%%     |                                        "orders" / order.retry)
%%%     |-- bind order.dlq ------------------> queue orders.dlq (quorum)
%%%     |-- bind order.charged --------------> queue orders.billing  (service)
%%%     `-- bind order.fulfilled ------------> queue orders.shipping (service)
%%% </pre>
%%%
%%% `orders.process' also dead-letters to `orders.dlq' (routing key
%%% `order.dlq') when quorum-queue `x-delivery-limit' redeliveries are
%%% exhausted. The process polls the connection until a channel exists,
%%% then declares everything; because a channel id change signals a
%%% reconnect, the declarations are repeated (they are idempotent) so a
%%% freshly started broker gets the full topology.
-module(mbb_topology).
-behaviour(gen_server).

-include_lib("amqp_client/include/amqp_client.hrl").

-export([start_link/0, get/0, ready/0,
         exchange/0, process_queue/0, dlq_queue/0,
         retry_queue_names/1, retry_routing_key/1, retry_ttl_ms/2,
         service_queues/0]).
-export([init/1, handle_call/3, handle_cast/2, handle_info/2,
         terminate/2, code_change/3]).

-define(EXCHANGE, <<"orders">>).
-define(PROCESS_QUEUE, <<"orders.process">>).
-define(DLQ, <<"orders.dlq">>).
-define(POLL_MS, 2000).
-define(DELIVERY_LIMIT, 5).

%% Example per-service queues: other services consume their own slice of
%% the order stream through topic bindings instead of sharing
%% orders.process.
-define(SERVICE_QUEUES, [{<<"orders.billing">>, <<"order.charged">>},
                         {<<"orders.shipping">>, <<"order.fulfilled">>}]).

%% ===================================================================
%% Pure helpers (unit-tested in mbb_retry_tests)
%% ===================================================================

%% @doc The N retry queue names: orders.retry.1 .. orders.retry.N.
-spec retry_queue_names(pos_integer()) -> [binary()].
retry_queue_names(Max) when is_integer(Max), Max > 0 ->
    [iolist_to_binary(io_lib:format("orders.retry.~b", [N]))
     || N <- lists:seq(1, Max)].

%% @doc Inbound topic binding for the Nth retry queue.
-spec retry_routing_key(pos_integer()) -> binary().
retry_routing_key(Nth) when is_integer(Nth), Nth > 0 ->
    iolist_to_binary(io_lib:format("order.retry.~b", [Nth])).

%% @doc Exponential backoff implemented as queue TTL: attempt N waits
%%      BaseMs * 4^(N-1) milliseconds before the broker dead-letters the
%%      message back into orders.process.
-spec retry_ttl_ms(pos_integer(), pos_integer()) -> pos_integer().
retry_ttl_ms(N, BaseMs) when is_integer(N), N >= 1, BaseMs >= 1 ->
    BaseMs bsl (2 * (N - 1)).

-spec exchange() -> binary().
exchange() -> ?EXCHANGE.

-spec process_queue() -> binary().
process_queue() -> ?PROCESS_QUEUE.

-spec dlq_queue() -> binary().
dlq_queue() -> ?DLQ.

-spec service_queues() -> [{binary(), binary()}].
service_queues() -> ?SERVICE_QUEUES.

%% ===================================================================
%% API
%% ===================================================================

-spec start_link() -> {ok, pid()} | {error, term()}.
start_link() ->
    gen_server:start_link({local, ?MODULE}, ?MODULE, [], []).

%% @doc The declared topology map, once available.
-spec get() -> {ok, map()} | {error, not_ready}.
get() ->
    gen_server:call(?MODULE, get).

-spec ready() -> boolean().
ready() ->
    gen_server:call(?MODULE, ready).

%% ===================================================================
%% gen_server
%% ===================================================================

-spec init([]) -> {ok, map()}.
init([]) ->
    self() ! declare,
    {ok, #{topo => undefined, chan => undefined}}.

-spec handle_call(term(), {pid(), term()}, map()) -> {reply, term(), map()}.
handle_call(get, _From, #{topo := undefined} = State) ->
    {reply, {error, not_ready}, State};
handle_call(get, _From, #{topo := Topo} = State) ->
    {reply, {ok, Topo}, State};
handle_call(ready, _From, #{topo := Topo} = State) ->
    {reply, is_map(Topo), State};
handle_call(_Request, _From, State) ->
    {reply, {error, unsupported}, State}.

-spec handle_cast(term(), map()) -> {noreply, map()}.
handle_cast(_Message, State) ->
    {noreply, State}.

-spec handle_info(term(), map()) -> {noreply, map()}.
handle_info(declare, #{chan := Chan} = State) ->
    _ = erlang:send_after(?POLL_MS, self(), declare),
    case mbb_connection:get_channel() of
        {ok, Chan} ->
            %% Same channel as before and topology already declared:
            %% nothing to do.
            {noreply, State};
        {ok, NewChan} ->
            {noreply, try_declare(NewChan, State)};
        {error, not_connected} ->
            {noreply, State}
    end;
handle_info(_Message, State) ->
    {noreply, State}.

-spec terminate(term(), map()) -> ok.
terminate(_Reason, _State) ->
    ok.

-spec code_change(term(), map(), term()) -> {ok, map()}.
code_change(_OldVsn, State, _Extra) ->
    {ok, State}.

-spec try_declare(pid(), map()) -> map().
try_declare(Chan, State) ->
    try
        Topo = declare_all(Chan),
        logger:info("mbb_topology: declared ~0p", [Topo]),
        State#{topo => Topo, chan => Chan}
    catch
        Class:Reason:Stacktrace ->
            logger:error("mbb_topology: declaration failed: ~0p:~0p ~0p",
                         [Class, Reason, Stacktrace]),
            State
    end.

%% ===================================================================
%% Declaration (every call is idempotent)
%% ===================================================================

-spec declare_all(pid()) -> map().
declare_all(Chan) ->
    ok = declare_exchange(Chan),
    ok = declare_queue(Chan, ?PROCESS_QUEUE, process_args()),
    Max = max_retries(),
    RetryNames = retry_queue_names(Max),
    ok = declare_retry_queues(Chan, RetryNames, retry_ttl_base_ms(), 1),
    ok = declare_queue(Chan, ?DLQ, quorum_args()),
    ok = bind(Chan, ?PROCESS_QUEUE, [<<"order.created">>, <<"order.retry">>]),
    ok = bind(Chan, ?DLQ, [<<"order.dlq">>]),
    ok = declare_service_queues(Chan),
    #{exchange => ?EXCHANGE,
      process_queue => ?PROCESS_QUEUE,
      retry_queues => RetryNames,
      dlq => ?DLQ,
      service_queues => [Q || {Q, _Key} <- ?SERVICE_QUEUES],
      delivery_limit => ?DELIVERY_LIMIT,
      max_retries => Max}.

-spec declare_exchange(pid()) -> ok.
declare_exchange(Chan) ->
    _ = amqp_channel:call(Chan, #'exchange.declare'{exchange = ?EXCHANGE,
                                                    type = <<"topic">>,
                                                    durable = true}),
    ok.

-spec declare_queue(pid(), binary(), list()) -> ok.
declare_queue(Chan, Name, Args) ->
    _ = amqp_channel:call(Chan, #'queue.declare'{queue = Name,
                                                 durable = true,
                                                 arguments = Args}),
    ok.

-spec declare_retry_queues(pid(), [binary()], pos_integer(), pos_integer()) -> ok.
declare_retry_queues(Chan, [Name | Rest], BaseTtl, N) ->
    Args = [{<<"x-queue-type">>, longstr, <<"quorum">>},
            {<<"x-delivery-limit">>, long, ?DELIVERY_LIMIT},
            {<<"x-message-ttl">>, long, retry_ttl_ms(N, BaseTtl)},
            {<<"x-dead-letter-exchange">>, longstr, ?EXCHANGE},
            {<<"x-dead-letter-routing-key">>, longstr, <<"order.retry">>}],
    ok = declare_queue(Chan, Name, Args),
    ok = bind(Chan, Name, [retry_routing_key(N)]),
    declare_retry_queues(Chan, Rest, BaseTtl, N + 1);
declare_retry_queues(_Chan, [], _BaseTtl, _N) ->
    ok.

-spec declare_service_queues(pid()) -> ok.
declare_service_queues(Chan) ->
    lists:foreach(
      fun({Queue, Key}) ->
              ok = declare_queue(Chan, Queue, quorum_args()),
              ok = bind(Chan, Queue, [Key])
      end,
      ?SERVICE_QUEUES).

-spec bind(pid(), binary(), [binary()]) -> ok.
bind(Chan, Queue, Keys) ->
    lists:foreach(
      fun(Key) ->
              _ = amqp_channel:call(Chan, #'queue.bind'{queue = Queue,
                                                        exchange = ?EXCHANGE,
                                                        routing_key = Key}),
              ok
      end,
      Keys).

%% ===================================================================
%% Shared arguments / configuration
%% ===================================================================

%% All queues are quorum queues: replicated, crash-safe, with a
%% redelivery limit.
-spec quorum_args() -> list().
quorum_args() ->
    [{<<"x-queue-type">>, longstr, <<"quorum">>},
     {<<"x-delivery-limit">>, long, ?DELIVERY_LIMIT}].

%% orders.process dead-letters terminal or delivery-limit-exhausted
%% messages straight into the DLQ via the topic exchange.
-spec process_args() -> list().
process_args() ->
    [{<<"x-queue-type">>, longstr, <<"quorum">>},
     {<<"x-delivery-limit">>, long, ?DELIVERY_LIMIT},
     {<<"x-dead-letter-exchange">>, longstr, ?EXCHANGE},
     {<<"x-dead-letter-routing-key">>, longstr, <<"order.dlq">>}].

-spec max_retries() -> pos_integer().
max_retries() ->
    application:get_env(message_broker_backend, max_retries, 3).

-spec retry_ttl_base_ms() -> pos_integer().
retry_ttl_base_ms() ->
    application:get_env(message_broker_backend, retry_ttl_base_ms, 5000).
