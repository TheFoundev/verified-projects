%%% @doc Confirm-mode order publisher.
%%%
%%% `publish/1,3' encodes the order as JSON, attaches the idempotency
%%% key, correlation id and `x-retry-count: 0' headers, publishes to the
%%% `orders' topic exchange and waits for a publisher confirm. A nack or
%%% a confirm timeout is retried a bounded number of times before
%%% `{error, nack}' is returned, so callers always know whether the
%%% broker durably took the message.
%%%
%%% When the broker signals `connection.blocked' (resource alarm) the
%%% publisher sheds load immediately with `{error, blocked}' instead of
%%% buffering; mbb_burst uses this to demonstrate backpressure.
-module(mbb_publisher).
-behaviour(gen_server).

-include_lib("amqp_client/include/amqp_client.hrl").

-export([start_link/0, publish/1, publish/3]).
-export([init/1, handle_call/3, handle_cast/2, handle_info/2,
         terminate/2, code_change/3]).

-type order() :: #{binary() => term()}.

%% ===================================================================
%% API
%% ===================================================================

-spec start_link() -> {ok, pid()} | {error, term()}.
start_link() ->
    gen_server:start_link({local, ?MODULE}, ?MODULE, [], []).

%% @doc Publish a fresh order under the `order.created' routing key.
-spec publish(order()) -> ok | {error, term()}.
publish(Order) ->
    publish(<<"order.created">>, Order, []).

%% @doc Publish with an explicit routing key and extra AMQP headers.
-spec publish(binary(), order(), list()) -> ok | {error, term()}.
publish(RoutingKey, Order, ExtraHeaders) when is_binary(RoutingKey),
                                             is_map(Order),
                                             is_list(ExtraHeaders) ->
    gen_server:call(?MODULE, {publish, RoutingKey, Order, ExtraHeaders},
                    publish_call_timeout_ms()).

%% ===================================================================
%% gen_server
%% ===================================================================

-spec init([]) -> {ok, map()}.
init([]) ->
    {ok, #{}}.

-spec handle_call(term(), {pid(), term()}, map()) -> {reply, term(), map()}.
handle_call({publish, RoutingKey, Order, ExtraHeaders}, _From, State) ->
    Reply = do_publish(RoutingKey, Order, ExtraHeaders),
    {reply, Reply, State};
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

%% ===================================================================
%% Publishing
%% ===================================================================

-spec do_publish(binary(), order(), list()) -> ok | {error, term()}.
do_publish(RoutingKey, Order, ExtraHeaders) ->
    case mbb_connection:is_blocked() of
        true ->
            ok = mbb_metrics:inc_published(blocked),
            {error, blocked};
        false ->
            publish_on_channel(RoutingKey, Order, ExtraHeaders)
    end.

-spec publish_on_channel(binary(), order(), list()) -> ok | {error, term()}.
publish_on_channel(RoutingKey, Order, ExtraHeaders) ->
    case mbb_connection:get_channel() of
        {error, not_connected} ->
            ok = mbb_metrics:inc_published(nack),
            {error, not_connected};
        {ok, Chan} ->
            {Payload, Props} = build_message(Order, ExtraHeaders),
            confirmed(Chan, RoutingKey, Payload, Props,
                      max_attempts(), Order)
    end.

-spec build_message(order(), list()) -> {binary(), #'P_basic'{}}.
build_message(Order, ExtraHeaders) ->
    IdemKey = mbb_order:idempotency_key(Order),
    OrderId = case maps:get(<<"order_id">>, Order, undefined) of
                  Id when is_binary(Id) -> Id;
                  _ -> IdemKey
              end,
    Headers = set_header(ExtraHeaders, <<"x-idempotency-key">>,
                         longstr, IdemKey),
    Headers1 = set_header(Headers, <<"x-retry-count">>, long, 0),
    Props = #'P_basic'{content_type = <<"application/json">>,
                       delivery_mode = 2,
                       headers = Headers1,
                       message_id = IdemKey,
                       correlation_id = OrderId,
                       timestamp = erlang:system_time(second)},
    {mbb_order:to_json(Order), Props}.

%% Publish is a cast; the confirm decides whether the broker took the
%% message. On a nack we retry a bounded number of times, then surface
%% {error, nack} to the caller.
-spec confirmed(pid(), binary(), binary(), #'P_basic'{}, pos_integer(), order()) ->
          ok | {error, term()}.
confirmed(_Chan, _RoutingKey, _Payload, _Props, 0, _Order) ->
    ok = mbb_metrics:inc_published(nack),
    {error, nack};
confirmed(Chan, RoutingKey, Payload, Props, AttemptsLeft, Order) ->
    try
        Publish = #'basic.publish'{exchange = mbb_topology:exchange(),
                                   routing_key = RoutingKey,
                                   mandatory = false},
        amqp_channel:cast(Chan, Publish,
                          #amqp_msg{props = Props, payload = Payload}),
        case amqp_channel:wait_for_confirms(Chan, confirm_timeout_ms()) of
            true ->
                ok = mbb_metrics:inc_published(ok),
                ok;
            false ->
                confirmed(Chan, RoutingKey, Payload, Props,
                          AttemptsLeft - 1, Order)
        end
    catch
        Class:Reason ->
            logger:error("mbb_publisher: publish failed (~0p:~0p)", [Class, Reason]),
            ok = mbb_metrics:inc_published(nack),
            {error, not_connected}
    end.

-spec set_header(list(), binary(), atom(), term()) -> list().
set_header(Headers, Name, Type, Value) ->
    Header = {Name, Type, Value},
    case lists:keymember(Name, 1, Headers) of
        true -> lists:keystore(Name, 1, Headers, Header);
        false -> Headers ++ [Header]
    end.

%% ===================================================================
%% Configuration
%% ===================================================================

-spec max_attempts() -> pos_integer().
max_attempts() ->
    application:get_env(message_broker_backend, publish_max_attempts, 3).

-spec confirm_timeout_ms() -> pos_integer().
confirm_timeout_ms() ->
    application:get_env(message_broker_backend, confirm_timeout_ms, 5000).

-spec publish_call_timeout_ms() -> pos_integer().
publish_call_timeout_ms() ->
    %% Waits for the confirm chain internally; give it headroom.
    max_attempts() * confirm_timeout_ms() + 1000.
