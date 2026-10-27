%%% @doc Prometheus metrics for the order-processing backend.
%%%
%%% The gen_server's only job is to register the metric definitions once
%%% at startup; increments and observations go straight through the
%%% prometheus library (which is backed by ETS and safe to call from any
%%% process). The arithmetic that decides histogram buckets and rates is
%%% kept in pure, exported helpers so it can be unit-tested without the
%%% prometheus application running.
-module(mbb_metrics).
-behaviour(gen_server).

-export([start_link/0,
         inc_published/1, inc_consumed/1, inc_retried/0, inc_dead_lettered/0,
         inc_in_flight/0, dec_in_flight/0, reset_in_flight/0,
         set_connection_blocked/1, observe_latency/1,
         collect/0,
         latency_buckets/0, bucket_of/2, rate_per_second/2]).
-export([init/1, handle_call/3, handle_cast/2, handle_info/2,
         terminate/2, code_change/3]).

-define(COUNTER_PUBLISHED, mbb_orders_published_total).
-define(COUNTER_CONSUMED, mbb_orders_consumed_total).
-define(COUNTER_RETRIED, mbb_orders_retried_total).
-define(COUNTER_DLQ, mbb_orders_dead_lettered_total).
-define(GAUGE_IN_FLIGHT, mbb_orders_in_flight).
-define(GAUGE_BLOCKED, mbb_connection_blocked).
-define(HISTOGRAM_LATENCY, mbb_processing_latency_seconds).

%% ===================================================================
%% API
%% ===================================================================

-spec start_link() -> {ok, pid()} | {error, term()}.
start_link() ->
    gen_server:start_link({local, ?MODULE}, ?MODULE, [], []).

%% @doc Count a publish attempt by result: ok | nack | blocked.
-spec inc_published(ok | nack | blocked) -> ok.
inc_published(Result) ->
    prometheus_counter:inc(?COUNTER_PUBLISHED, [atom_to_list(Result)], 1),
    ok.

%% @doc Count a consumed delivery by outcome:
%%      ok | retry | dead_letter | duplicate.
-spec inc_consumed(ok | retry | dead_letter | duplicate) -> ok.
inc_consumed(Outcome) ->
    prometheus_counter:inc(?COUNTER_CONSUMED, [atom_to_list(Outcome)], 1),
    ok.

-spec inc_retried() -> ok.
inc_retried() ->
    prometheus_counter:inc(?COUNTER_RETRIED, [], 1),
    ok.

-spec inc_dead_lettered() -> ok.
inc_dead_lettered() ->
    prometheus_counter:inc(?COUNTER_DLQ, [], 1),
    ok.

-spec inc_in_flight() -> ok.
inc_in_flight() ->
    prometheus_gauge:inc(?GAUGE_IN_FLIGHT, 1),
    ok.

-spec dec_in_flight() -> ok.
dec_in_flight() ->
    prometheus_gauge:dec(?GAUGE_IN_FLIGHT, 1),
    ok.

%% Reset after a consumer restart: redeliveries are replayed from the
%% broker, so any pre-crash gauge value is meaningless.
-spec reset_in_flight() -> ok.
reset_in_flight() ->
    prometheus_gauge:set(?GAUGE_IN_FLIGHT, 0),
    ok.

-spec set_connection_blocked(boolean()) -> ok.
set_connection_blocked(true) ->
    prometheus_gauge:set(?GAUGE_BLOCKED, 1),
    ok;
set_connection_blocked(false) ->
    prometheus_gauge:set(?GAUGE_BLOCKED, 0),
    ok.

%% @doc Record one processing latency observation, given in milliseconds.
-spec observe_latency(number()) -> ok.
observe_latency(Ms) when is_number(Ms) ->
    prometheus_histogram:observe(?HISTOGRAM_LATENCY, Ms / 1000),
    ok.

%% @doc Render every registered metric in Prometheus text format 0.0.4.
-spec collect() -> binary().
collect() ->
    iolist_to_binary(prometheus_text_format:render()).

%% ===================================================================
%% Pure arithmetic (unit-tested in mbb_metrics_tests)
%% ===================================================================

%% Upper bounds of the latency histogram, in seconds.
-spec latency_buckets() -> [float()].
latency_buckets() ->
    [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0].

%% @doc Zero-based cumulative bucket index: how many upper bounds the
%%      observation is less than or equal to.
-spec bucket_of(number(), [number()]) -> non_neg_integer().
bucket_of(Value, Bounds) when is_number(Value), is_list(Bounds) ->
    length([B || B <- Bounds, Value >= B]).

%% @doc Simple throughput calculation used by the burst summary.
-spec rate_per_second(non_neg_integer(), non_neg_integer()) -> float().
rate_per_second(_Count, ElapsedMs) when ElapsedMs =< 0 ->
    0.0;
rate_per_second(Count, ElapsedMs) ->
    Count * 1000 / ElapsedMs.

%% ===================================================================
%% gen_server
%% ===================================================================

-spec init([]) -> {ok, map()}.
init([]) ->
    ok = ensure_metrics(),
    {ok, #{}}.

-spec ensure_metrics() -> ok.
ensure_metrics() ->
    Definitions =
        [fun() ->
             prometheus_counter:new([{name, ?COUNTER_PUBLISHED},
                                     {help, <<"Publish attempts by result.">>},
                                     {labels, [result]}])
         end,
         fun() ->
             prometheus_counter:new([{name, ?COUNTER_CONSUMED},
                                     {help, <<"Consumed deliveries by outcome.">>},
                                     {labels, [outcome]}])
         end,
         fun() ->
             prometheus_counter:new([{name, ?COUNTER_RETRIED},
                                     {help, <<"Messages re-queued into a retry queue.">>}])
         end,
         fun() ->
             prometheus_counter:new([{name, ?COUNTER_DLQ},
                                     {help, <<"Messages routed to the dead-letter queue.">>}])
         end,
         fun() ->
             prometheus_gauge:new([{name, ?GAUGE_IN_FLIGHT},
                                   {help, <<"Deliveries currently being processed.">>}])
         end,
         fun() ->
             prometheus_gauge:new([{name, ?GAUGE_BLOCKED},
                                   {help, <<"1 while the broker connection is blocked.">>}])
         end,
         fun() ->
             prometheus_histogram:new([{name, ?HISTOGRAM_LATENCY},
                                       {help, <<"Order processing latency in seconds.">>},
                                       {buckets, latency_buckets()}])
         end],
    lists:foreach(fun(Def) -> safe_register(Def) end, Definitions),
    ok.

%% Metric registration is idempotent across hot code changes; a definition
%% that already exists in the default registry is fine.
-spec safe_register(fun(() -> term())) -> ok.
safe_register(Fun) ->
    try
        _ = Fun(),
        ok
    catch
        _:_ -> ok
    end.

-spec handle_call(term(), {pid(), term()}, map()) -> {reply, term(), map()}.
handle_call(_Request, _From, State) ->
    {reply, {error, unsupported}, State}.

-spec handle_cast(term(), map()) -> {noreply, map()}.
handle_cast(_Message, State) ->
    {noreply, State}.

-spec handle_info(term(), map()) -> {noreply, map()}.
handle_info(_Message, State) ->
    {noreply, State}.

-spec terminate(term(), map()) -> ok.
terminate(_Reason, _State) ->
    ok.

-spec code_change(term(), map(), term()) -> {ok, map()}.
code_change(_OldVsn, State, _Extra) ->
    {ok, State}.
