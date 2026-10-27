%%% @doc Manual-ack order consumer with idempotency guard, retry queues
%%% and dead-lettering.
%%%
%%% Flow for one delivery:
%%%
%%% <ol>
%%%   <li>duplicate check against the seen-cache ETS (TTL-based): a
%%%       redelivered message whose idempotency key was already processed
%%%       is acked without side effects;</li>
%%%   <li>`mbb_order:process/1' classifies the outcome;</li>
%%%   <li>`ok' -> ack; `{retry, R}' -> republish into the next
%%%       `orders.retry.N' queue with `x-retry-count' incremented and
%%%       ack (the queue's `x-message-ttl' + dead-letter-back provides
%%%       the delay); `{dead_letter, R}' -> republish to `orders.dlq'
%%%       with `x-dlq-reason' and ack;</li>
%%%   <li>a retry whose `x-retry-count' has reached `max_retries' is
%%%       dead-lettered with `retries_exhausted' instead of looping.</li>
%%% </ol>
%%%
%%% Backpressure: the channel prefetch (set by mbb_connection) bounds how
%%% many unacked deliveries can exist, so a blocked broker or a slow
%%% pipeline naturally stops the consumer from pulling more work. After
%%% a channel loss the consumer resubscribes with exponential backoff +
%%% jitter ({@link backoff_ms/2}).
-module(mbb_consumer).
-behaviour(gen_server).

-include_lib("amqp_client/include/amqp_client.hrl").

-export([start_link/0,
         retry_count/1, next_retry_queue/1, should_dead_letter/2,
         backoff_ms/2, dlq_queue/0]).
-export([init/1, handle_call/3, handle_cast/2, handle_info/2,
         terminate/2, code_change/3]).

-define(RETRY_HEADER, <<"x-retry-count">>).
-define(SETUP_BACKOFF_BASE_MS, 500).
-define(SETUP_BACKOFF_MAX_MS, 30000).
-define(POLL_TOPOLOGY_MS, 500).
-define(SEEN_PRUNE_MS, 60000).
-define(MAX_SETUP_ATTEMPTS, 100).

%% ===================================================================
%% Pure retry helpers (unit-tested in mbb_retry_tests)
%% ===================================================================

%% @doc Read `x-retry-count' from AMQP headers (undefined or malformed
%%      headers count as zero).
-spec retry_count(undefined | list()) -> non_neg_integer().
retry_count(undefined) ->
    0;
retry_count(Headers) when is_list(Headers) ->
    case lists:keyfind(?RETRY_HEADER, 1, Headers) of
        {?RETRY_HEADER, _Type, N} when is_integer(N), N >= 0 -> N;
        {?RETRY_HEADER, _Type, N} when is_float(N), N >= 0 -> erlang:trunc(N);
        false -> 0;
        _Malformed -> 0
    end;
retry_count(_Other) ->
    0.

%% @doc Retry queue for a message that has already been retried
%%      `RetryCount' times: count 0 goes to orders.retry.1, and so on.
-spec next_retry_queue(non_neg_integer()) -> binary().
next_retry_queue(RetryCount) when is_integer(RetryCount), RetryCount >= 0 ->
    iolist_to_binary(io_lib:format("orders.retry.~b", [RetryCount + 1])).

%% @doc Terminal? After `Max' retries the message goes to the DLQ.
-spec should_dead_letter(non_neg_integer(), pos_integer()) -> boolean().
should_dead_letter(RetryCount, Max) when is_integer(RetryCount),
                                         is_integer(Max), Max >= 1 ->
    RetryCount >= Max.

%% @doc Exponential backoff with up to 25% jitter, capped at 30 s.
%%      Attempt N waits BaseMs * 2^(N-1) + jitter milliseconds.
-spec backoff_ms(pos_integer(), pos_integer()) -> pos_integer().
backoff_ms(RetryCount, BaseMs) when RetryCount >= 1, BaseMs >= 1 ->
    Exp0 = BaseMs bsl (RetryCount - 1),
    Exp = erlang:min(Exp0, 30000),
    JitterMax = erlang:max(Exp div 4, 1),
    Exp + rand:uniform(JitterMax).

-spec dlq_queue() -> binary().
dlq_queue() ->
    mbb_topology:dlq_queue().

%% ===================================================================
%% API
%% ===================================================================

-spec start_link() -> {ok, pid()} | {error, term()}.
start_link() ->
    gen_server:start_link({local, ?MODULE}, ?MODULE, [], []).

%% ===================================================================
%% gen_server
%% ===================================================================

-spec init([]) -> {ok, map()}.
init([]) ->
    Seen = ets:new(mbb_seen, [set, public, {keypos, 1}]),
    _ = erlang:send_after(0, self(), setup),
    _ = erlang:send_after(?SEEN_PRUNE_MS, self(), prune),
    {ok, #{chan => undefined,
           chan_mon => undefined,
           consuming => false,
           seen => Seen,
           attempt => 0,
           in_flight => 0}}.

-spec handle_call(term(), {pid(), term()}, map()) -> {reply, term(), map()}.
handle_call(_Request, _From, State) ->
    {reply, {error, unsupported}, State}.

-spec handle_cast(term(), map()) -> {noreply, map()}.
handle_cast(_Message, State) ->
    {noreply, State}.

-spec handle_info(term(), map()) -> {noreply, map()}.
handle_info(setup, State) ->
    {noreply, try_setup(State)};
handle_info({'DOWN', Mon, process, _Pid, _Reason}, #{chan_mon := Mon} = State) ->
    logger:warning("mbb_consumer: channel lost; will resubscribe"),
    State1 = State#{chan => undefined, chan_mon => undefined,
                    consuming => false},
    {noreply, schedule_setup(State1, 1)};
handle_info({#'basic.cancel'{}, _SeqNo}, State) ->
    logger:warning("mbb_consumer: broker cancelled the subscription"),
    State1 = State#{consuming => false},
    {noreply, schedule_setup(State1, 1)};
handle_info(#'basic.cancel'{}, State) ->
    logger:warning("mbb_consumer: broker cancelled the subscription"),
    State1 = State#{consuming => false},
    {noreply, schedule_setup(State1, 1)};
handle_info(prune, #{seen := Seen} = State) ->
    _ = erlang:send_after(?SEEN_PRUNE_MS, self(), prune),
    ok = prune_seen(Seen, erlang:monotonic_time(millisecond)),
    {noreply, State};
handle_info({#'basic.deliver'{delivery_tag = Tag}, #amqp_msg{} = Msg}, State) ->
    {noreply, settle_delivery(Tag, Msg, State)};
handle_info(_Message, State) ->
    {noreply, State}.

-spec terminate(term(), map()) -> ok.
terminate(_Reason, #{chan := Chan, consuming := true}) when is_pid(Chan) ->
    _ = try amqp_channel:cancel(Chan, <<"mbb-consumer">>)
        catch _:_ -> ok
        end,
    ok;
terminate(_Reason, _State) ->
    ok.

-spec code_change(term(), map(), term()) -> {ok, map()}.
code_change(_OldVsn, State, _Extra) ->
    {ok, State}.

%% ===================================================================
%% Subscription setup (with backoff)
%% ===================================================================

-spec try_setup(map()) -> map().
try_setup(#{attempt := Attempt} = State) when Attempt >= ?MAX_SETUP_ATTEMPTS ->
    logger:error("mbb_consumer: giving up after ~p setup attempts", [Attempt]),
    State;
try_setup(State) ->
    case {mbb_connection:get_channel(), mbb_topology:get()} of
        {{ok, Chan}, {ok, Topo}} ->
            subscribe(Chan, maps:get(process_queue, Topo), State);
        {{error, not_connected}, _} ->
            schedule_setup_backoff(State);
        {_, {error, not_ready}} ->
            schedule_setup_backoff(State)
    end.

-spec subscribe(pid(), binary(), map()) -> map().
subscribe(Chan, Queue, State) ->
    try
        #'basic.consume_ok'{} =
            amqp_channel:subscribe(Chan,
                                   #'basic.consume'{queue = Queue,
                                                    no_ack = false,
                                                    consumer_tag = <<"mbb-consumer">>},
                                   self()),
        ok = mbb_metrics:reset_in_flight(),
        logger:info("mbb_consumer: consuming from ~s", [Queue]),
        State#{chan => Chan,
               chan_mon => erlang:monitor(process, Chan),
               consuming => true,
               attempt => 0,
               in_flight => 0}
    catch
        Class:Reason ->
            logger:error("mbb_consumer: subscribe failed (~0p:~0p)", [Class, Reason]),
            schedule_setup_backoff(State)
    end.

-spec schedule_setup(map(), non_neg_integer()) -> map().
schedule_setup(State, Attempt) ->
    State#{attempt => Attempt}.

-spec schedule_setup_backoff(map()) -> map().
schedule_setup_backoff(#{attempt := Attempt} = State) ->
    Next = Attempt + 1,
    Delay = backoff_ms(Next, ?SETUP_BACKOFF_BASE_MS),
    _ = erlang:send_after(Delay, self(), setup),
    State#{attempt => erlang:min(Next, ?MAX_SETUP_ATTEMPTS)}.

%% ===================================================================
%% Delivery settlement
%% ===================================================================

-spec settle_delivery(term(), #amqp_msg{}, map()) -> map().
settle_delivery(Tag, #amqp_msg{props = Props, payload = Payload}, State) ->
    #'P_basic'{headers = Headers, correlation_id = CorrId} = Props,
    Key = idempotency_key(CorrId, Headers, Payload),
    Seen = maps:get(seen, State),
    Now = erlang:monotonic_time(millisecond),
    State1 = bump_in_flight(State),
    case already_seen(Seen, Key, Now) of
        true ->
            ok = mbb_metrics:inc_consumed(duplicate),
            ok = ack(maps:get(chan, State1), Tag),
            drop_in_flight(State1);
        false ->
            Start = erlang:monotonic_time(millisecond),
            Outcome = decode_and_process(Payload),
            Latency = erlang:monotonic_time(millisecond) - Start,
            ok = mbb_metrics:observe_latency(Latency),
            settle(Outcome, Key, Tag, Headers, Payload, State1)
    end.

-spec decode_and_process(binary()) -> mbb_order:outcome().
decode_and_process(Payload) ->
    case mbb_order:from_json(Payload) of
        {ok, Order} -> mbb_order:process(Order);
        {error, Reason} -> {dead_letter, {malformed_json, Reason}}
    end.

-spec settle(mbb_order:outcome(), binary(), term(), list() | undefined,
             binary(), map()) -> map().
settle(ok, Key, Tag, _Headers, _Payload, State) ->
    mark_seen(State, Key),
    ok = mbb_metrics:inc_consumed(ok),
    ok = ack(maps:get(chan, State), Tag),
    drop_in_flight(State);
settle({retry, Reason}, Key, Tag, Headers, Payload, State) ->
    Count = retry_count(Headers),
    Max = max_retries(),
    case should_dead_letter(Count, Max) of
        true ->
            to_dlq(retries_exhausted, Reason, Tag, Headers, Payload, State);
        false ->
            to_retry(Count, Key, Tag, Headers, Payload, State)
    end;
settle({dead_letter, Reason}, _Key, Tag, Headers, Payload, State) ->
    to_dlq(terminal, Reason, Tag, Headers, Payload, State).

%% Re-queue into the next retry queue with x-retry-count incremented.
%% The queue's TTL holds the message, then dead-letters it back into
%% orders.process via routing key order.retry. Ack only after the broker
%% confirmed the copy, so a crash between publish and ack can only cause
%% a duplicate (handled by the seen-cache), never a loss.
-spec to_retry(non_neg_integer(), binary(), term(), list() | undefined,
               binary(), map()) -> map().
to_retry(Count, _Key, Tag, Headers, Payload, State) ->
    Queue = next_retry_queue(Count),
    Headers1 = set_header(Headers, ?RETRY_HEADER, long, Count + 1),
    case republish(mbb_topology:retry_routing_key(Count + 1), Headers1, Payload, State) of
        ok ->
            ok = mbb_metrics:inc_consumed(retry),
            ok = mbb_metrics:inc_retried(),
            ok = ack(maps:get(chan, State), Tag),
            drop_in_flight(State);
        {error, Reason} ->
            logger:warning("mbb_consumer: retry publish to ~s failed (~0p); "
                           "leaving message unacked for redelivery",
                           [Queue, Reason]),
            State
    end.

-spec to_dlq(atom(), term(), term(), list() | undefined, binary(), map()) -> map().
to_dlq(Class, Reason, Tag, Headers, Payload, State) ->
    ReasonBin = iolist_to_binary(io_lib:format("~0p", [Reason])),
    Headers1 = set_header(Headers, <<"x-dlq-reason">>, longstr, ReasonBin),
    Headers2 = set_header(Headers1, <<"x-dlq-class">>, longstr,
                          atom_to_binary(Class, utf8)),
    case republish(<<"order.dlq">>, Headers2, Payload, State) of
        ok ->
            ok = mbb_metrics:inc_consumed(dead_letter),
            ok = mbb_metrics:inc_dead_lettered(),
            ok = ack(maps:get(chan, State), Tag),
            drop_in_flight(State);
        {error, PublishErr} ->
            logger:warning("mbb_consumer: dlq publish failed (~0p); "
                           "leaving message unacked for redelivery",
                           [PublishErr]),
            State
    end.

%% Cast the copy and wait for the confirm on the same channel.
-spec republish(binary(), list(), binary(), map()) -> ok | {error, term()}.
republish(RoutingKey, Headers, Payload, State) ->
    case maps:get(chan, State) of
        Chan when is_pid(Chan) ->
            try
                Props = #'P_basic'{content_type = <<"application/json">>,
                                   delivery_mode = 2,
                                   headers = Headers,
                                   timestamp = erlang:system_time(second)},
                Publish = #'basic.publish'{exchange = mbb_topology:exchange(),
                                           routing_key = RoutingKey,
                                           mandatory = false},
                amqp_channel:cast(Chan, Publish,
                                  #amqp_msg{props = Props, payload = Payload}),
                case amqp_channel:wait_for_confirms(Chan, confirm_timeout_ms()) of
                    true -> ok;
                    false -> {error, confirm_timeout}
                end
            catch
                Class:Reason ->
                    {error, {Class, Reason}}
            end;
        _ ->
            {error, not_connected}
    end.

-spec ack(pid(), term()) -> ok.
ack(Chan, Tag) ->
    amqp_channel:cast(Chan, #'basic.ack'{delivery_tag = Tag}),
    ok.

%% ===================================================================
%% Idempotency (seen-cache)
%% ===================================================================

-spec idempotency_key(binary() | undefined, list() | undefined, binary()) -> binary().
idempotency_key(CorrId, Headers, Payload) ->
    case header_value(<<"x-idempotency-key">>, Headers) of
        undefined when is_binary(CorrId) -> CorrId;
        undefined -> iolist_to_binary(
                       io_lib:format("payload-~8.16.0b",
                                     [erlang:phash2(Payload)]));
        Key when is_binary(Key) -> Key
    end.

-spec header_value(binary(), list() | undefined) -> term().
header_value(_Name, undefined) ->
    undefined;
header_value(Name, Headers) when is_list(Headers) ->
    case lists:keyfind(Name, 1, Headers) of
        {Name, _Type, Value} -> Value;
        false -> undefined
    end;
header_value(_Name, _Other) ->
    undefined.

-spec already_seen(ets:tid(), binary(), integer()) -> boolean().
already_seen(Seen, Key, Now) ->
    case ets:lookup(Seen, Key) of
        [{Key, Expires}] when Expires > Now ->
            true;
        [{Key, _Expired}] ->
            ets:delete(Seen, Key),
            false;
        [] ->
            false
    end.

-spec mark_seen(map(), binary()) -> ok.
mark_seen(#{seen := Seen}, Key) ->
    Expires = erlang:monotonic_time(millisecond) + seen_ttl_ms(),
    true = ets:insert(Seen, {Key, Expires}),
    ok.

-spec prune_seen(ets:tid(), integer()) -> ok.
prune_seen(Seen, Now) ->
    _ = ets:select_delete(Seen, [{{'$1', '$2'},[{'=<', '$2', Now}], [true]}]),
    ok.

%% ===================================================================
%% Small helpers
%% ===================================================================

-spec set_header(list() | undefined, binary(), atom(), term()) -> list().
set_header(undefined, Name, Type, Value) ->
    set_header([], Name, Type, Value);
set_header(Headers, Name, Type, Value) ->
    Header = {Name, Type, Value},
    case lists:keymember(Name, 1, Headers) of
        true -> lists:keystore(Name, 1, Headers, Header);
        false -> [Header | Headers]
    end.

-spec bump_in_flight(map()) -> map().
bump_in_flight(State) ->
    ok = mbb_metrics:inc_in_flight(),
    State#{in_flight => maps:get(in_flight, State) + 1}.

-spec drop_in_flight(map()) -> map().
drop_in_flight(State) ->
    ok = mbb_metrics:dec_in_flight(),
    State#{in_flight => erlang:max(maps:get(in_flight, State) - 1, 0)}.

-spec confirm_timeout_ms() -> pos_integer().
confirm_timeout_ms() ->
    application:get_env(message_broker_backend, confirm_timeout_ms, 5000).

-spec max_retries() -> pos_integer().
max_retries() ->
    application:get_env(message_broker_backend, max_retries, 3).

-spec seen_ttl_ms() -> pos_integer().
seen_ttl_ms() ->
    application:get_env(message_broker_backend, seen_ttl_ms, 600000).
