%%% @doc Burst-load generator for the backpressure demo.
%%%
%%% Publishes synthetic orders at a fixed rate so the broker can be
%%% pushed into (and held at) its limits:
%%%
%%% <pre>
%%%   mbb_burst:run_sync(2000, 500).   %% 2000 orders at 500/s
%%% </pre>
%%%
%%% Every 10th order deliberately trips the transient inventory rule and
%%% every 23rd fails validation, so the run exercises the retry queues
%%% and the DLQ instead of only the happy path. When the broker signals
%%% `connection.blocked' the generator stops issuing new publishes and
%%% waits (polling `mbb_connection:is_blocked/0') - this is the visible
%%% backpressure behaviour under burst load.
-module(mbb_burst).

-export([run/0, run/2, run_sync/2, synthetic_order/1]).

-define(DEFAULT_COUNT, 500).
-define(DEFAULT_RATE, 200).
-define(BLOCKED_POLL_MS, 100).
-define(BLOCKED_MAX_POLLS, 300).

%% ===================================================================
%% API
%% ===================================================================

%% @doc Fire the configured default burst in the background.
-spec run() -> {ok, pid()}.
run() ->
    run(cfg(count), cfg(rate)).

%% @doc Fire `Count' orders at `RatePerSec' in a background process.
-spec run(pos_integer(), pos_integer()) -> {ok, pid()}.
run(Count, RatePerSec) when is_integer(Count), Count >= 1,
                            is_integer(RatePerSec), RatePerSec >= 1 ->
    Interval = 1000 div RatePerSec,
    Pid = spawn(fun() -> loop(1, Count, Interval, initial_summary()) end),
    {ok, Pid}.

%% @doc Run the burst synchronously and return the summary map.
-spec run_sync(pos_integer(), pos_integer()) -> map().
run_sync(Count, RatePerSec) when is_integer(Count), Count >= 1,
                                 is_integer(RatePerSec), RatePerSec >= 1 ->
    Interval = 1000 div RatePerSec,
    loop(1, Count, Interval, initial_summary()).

%% @doc Deterministic demo orders: mostly valid, with injected transient
%%      (every 10th) and terminal (every 23rd) failures.
-spec synthetic_order(pos_integer()) -> map().
synthetic_order(N) when N rem 23 =:= 0 ->
    (mbb_order:synthetic(N))#{<<"currency">> => <<"GBP">>};
synthetic_order(N) when N rem 10 =:= 0 ->
    (mbb_order:synthetic(N))#{<<"items">> => [#{<<"sku">> => <<"SKU-1">>,
                                                <<"qty">> => 1200}]};
synthetic_order(N) ->
    mbb_order:synthetic(N).

%% ===================================================================
%% Loop
%% ===================================================================

-spec initial_summary() -> map().
initial_summary() ->
    #{published => 0, blocked => 0, nacked => 0, blocked_waits => 0,
      started_ms => erlang:monotonic_time(millisecond)}.

-spec loop(pos_integer(), pos_integer(), pos_integer(), map()) -> map().
loop(N, Count, _Interval, Summary) when N > Count ->
    Finish = erlang:monotonic_time(millisecond),
    Elapsed = Finish - maps:get(started_ms, Summary),
    Rate = mbb_metrics:rate_per_second(maps:get(published, Summary), Elapsed),
    Final = Summary#{done => true, finished_ms => Finish,
                     elapsed_ms => Elapsed, achieved_rate_per_s => Rate},
    logger:info("mbb_burst: finished ~0p", [Final]),
    Final;
loop(N, Count, Interval, Summary) ->
    Started = erlang:monotonic_time(millisecond),
    Result = mbb_publisher:publish(synthetic_order(N)),
    Summary1 = count(Result, Summary),
    Summary2 = await_unblocked(Summary1),
    Elapsed = erlang:monotonic_time(millisecond) - Started,
    sleep(erlang:max(Interval - Elapsed, 0)),
    case N rem 100 of
        0 -> logger:info("mbb_burst: progress ~b/~b", [N, Count]);
        _ -> ok
    end,
    loop(N + 1, Count, Interval, Summary2).

-spec count(ok | {error, term()}, map()) -> map().
count(ok, Summary) ->
    Summary#{published := maps:get(published, Summary) + 1};
count({error, blocked}, Summary) ->
    Summary#{blocked := maps:get(blocked, Summary) + 1};
count({error, _Other}, Summary) ->
    Summary#{nacked := maps:get(nacked, Summary) + 1}.

%% If the broker is under a resource alarm, pause the burst until it
%% clears (bounded at ~30 s per order) - backpressure, observed live.
-spec await_unblocked(map()) -> map().
await_unblocked(Summary) ->
    await_unblocked(Summary, ?BLOCKED_MAX_POLLS).

-spec await_unblocked(map(), non_neg_integer()) -> map().
await_unblocked(Summary, 0) ->
    Summary;
await_unblocked(Summary, PollsLeft) ->
    case mbb_connection:is_blocked() of
        false ->
            Summary;
        true ->
            timer:sleep(?BLOCKED_POLL_MS),
            Summary1 = Summary#{blocked_waits :=
                                    maps:get(blocked_waits, Summary) + 1},
            await_unblocked(Summary1, PollsLeft - 1)
    end.

-spec sleep(non_neg_integer()) -> ok.
sleep(0) -> ok;
sleep(Ms) -> timer:sleep(Ms).

-spec cfg(atom()) -> pos_integer().
cfg(count) ->
    application:get_env(message_broker_backend, burst_count, ?DEFAULT_COUNT);
cfg(rate) ->
    application:get_env(message_broker_backend, burst_rate, ?DEFAULT_RATE).
