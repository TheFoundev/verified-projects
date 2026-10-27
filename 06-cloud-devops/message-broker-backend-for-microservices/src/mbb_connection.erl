%%% @doc Supervised AMQP connection and channel with automatic
%%% reconnect, exponential backoff and broker-flow backpressure.
%%%
%%% The gen_server owns one `amqp_connection' and one channel put into
%%% confirm mode with a `basic.qos' prefetch window. It:
%%%
%%% <ul>
%%%   <li>reconnects with exponential backoff + jitter after any
%%%       connection or channel failure;</li>
%%%   <li>handles `connection.blocked'/`connection.unblocked' flow
%%%       control notifications from the broker (resource alarms) and
%%%       mirrors the state into the `mbb_connection_blocked' gauge and
%%%       the {@link is_blocked/0} backpressure signal;</li>
%%%   <li>serves channel pids to the publisher and consumer through
%%%       {@link get_channel/0}.</li>
%%% </ul>
%%%
%%% Environment keys can be overridden with `MBB_*' OS variables
%%% (e.g. `MBB_AMQP_HOST=rabbitmq' inside docker-compose).
-module(mbb_connection).
-behaviour(gen_server).

-include_lib("amqp_client/include/amqp_client.hrl").

-export([start_link/0, get_channel/0, is_connected/0, is_blocked/0]).
-export([init/1, handle_call/3, handle_cast/2, handle_info/2,
         terminate/2, code_change/3]).

%% ===================================================================
%% API
%% ===================================================================

-spec start_link() -> {ok, pid()} | {error, term()}.
start_link() ->
    gen_server:start_link({local, ?MODULE}, ?MODULE, [], []).

%% @doc Channel for publishing/consuming, or an error when not connected.
-spec get_channel() -> {ok, pid()} | {error, not_connected}.
get_channel() ->
    gen_server:call(?MODULE, get_channel).

-spec is_connected() -> boolean().
is_connected() ->
    gen_server:call(?MODULE, is_connected).

%% @doc True while the broker applies flow control (resource alarm).
%%      Publishers use this to shed load instead of buffering forever.
-spec is_blocked() -> boolean().
is_blocked() ->
    gen_server:call(?MODULE, is_blocked).

%% ===================================================================
%% Configuration
%% ===================================================================

-spec cfg(atom()) -> term().
cfg(Key) ->
    case os:getenv(env_name(Key)) of
        false -> application:get_env(message_broker_backend, Key, default(Key));
        Value -> parse_env(Key, Value)
    end.

-spec env_name(atom()) -> string().
env_name(Key) ->
    "MBB_" ++ string:uppercase(atom_to_list(Key)).

-spec parse_env(atom(), string()) -> term().
parse_env(Key, Value) ->
    case default(Key) of
        N when is_integer(N) -> list_to_integer(Value);
        B when is_binary(B) -> list_to_binary(Value);
        _ -> Value
    end.

-spec default(atom()) -> term().
default(amqp_host) -> "localhost";
default(amqp_port) -> 5672;
default(amqp_username) -> <<"guest">>;
default(amqp_password) -> <<"guest">>;
default(amqp_vhost) -> <<"/">>;
default(heartbeat_seconds) -> 30;
default(prefetch_count) -> 32;
default(backoff_base_ms) -> 500;
default(backoff_max_ms) -> 30000;
default(Key) -> erlang:error({missing_default, Key}).

%% ===================================================================
%% gen_server
%% ===================================================================

-spec init([]) -> {ok, map()}.
init([]) ->
    self() ! connect,
    {ok, #{conn => undefined,
           conn_mon => undefined,
           chan => undefined,
           chan_mon => undefined,
           backoff_ms => cfg(backoff_base_ms),
           blocked => false}}.

-spec handle_call(term(), {pid(), term()}, map()) -> {reply, term(), map()}.
handle_call(get_channel, _From, #{chan := Chan} = State) when is_pid(Chan) ->
    Reply = case is_process_alive(Chan) of
                true -> {ok, Chan};
                false -> {error, not_connected}
            end,
    {reply, Reply, State};
handle_call(get_channel, _From, State) ->
    {reply, {error, not_connected}, State};
handle_call(is_connected, _From, #{conn := Conn, chan := Chan} = State) ->
    Connected = is_pid(Conn) andalso is_pid(Chan)
        andalso is_process_alive(Conn) andalso is_process_alive(Chan),
    {reply, Connected, State};
handle_call(is_connected, _From, State) ->
    {reply, false, State};
handle_call(is_blocked, _From, State) ->
    {reply, maps:get(blocked, State, false), State};
handle_call(_Request, _From, State) ->
    {reply, {error, unsupported}, State}.

-spec handle_cast(term(), map()) -> {noreply, map()}.
handle_cast(_Message, State) ->
    {noreply, State}.

-spec handle_info(term(), map()) -> {noreply, map()}.
handle_info(connect, State) ->
    {noreply, attempt_connect(State)};
handle_info(#'connection.blocked'{reason = Reason}, State) ->
    logger:warning("mbb_connection: broker blocked (reason=~0p); "
                   "backpressure is now active", [Reason]),
    ok = mbb_metrics:set_connection_blocked(true),
    {noreply, State#{blocked => true}};
handle_info(#'connection.unblocked'{}, State) ->
    logger:info("mbb_connection: broker unblocked"),
    ok = mbb_metrics:set_connection_blocked(false),
    {noreply, State#{blocked => false}};
handle_info({'DOWN', Mon, process, _Pid, Reason},
            #{conn_mon := Mon, chan_mon := CMon} = State) ->
    erlang:demonitor(CMon, [flush]),
    logger:warning("mbb_connection: connection down (~0p); reconnecting", [Reason]),
    {noreply, schedule_reconnect(clear_conn(State))};
handle_info({'DOWN', Mon, process, _Pid, Reason}, #{chan_mon := Mon} = State) ->
    logger:warning("mbb_connection: channel down (~0p); reconnecting", [Reason]),
    {noreply, schedule_reconnect(clear_conn(State))};
handle_info(_Message, State) ->
    {noreply, State}.

-spec terminate(term(), map()) -> ok.
terminate(_Reason, #{conn := Conn}) when is_pid(Conn) ->
    _ = try amqp_connection:close(Conn)
        catch _:_ -> ok
        end,
    ok;
terminate(_Reason, _State) ->
    ok.

-spec code_change(term(), map(), term()) -> {ok, map()}.
code_change(_OldVsn, State, _Extra) ->
    {ok, State}.

%% ===================================================================
%% Connection lifecycle
%% ===================================================================

-spec attempt_connect(map()) -> map().
attempt_connect(State) ->
    Params = #amqp_params_network{host = cfg(amqp_host),
                                  port = cfg(amqp_port),
                                  username = cfg(amqp_username),
                                  password = cfg(amqp_password),
                                  virtual_host = cfg(amqp_vhost),
                                  heartbeat = cfg(heartbeat_seconds),
                                  client_properties =
                                      [{<<"connection_name">>,
                                        <<"message-broker-backend">>}]},
    case amqp_connection:start(Params) of
        {ok, Conn} ->
            ConnMon = erlang:monitor(process, Conn),
            case open_channel(Conn) of
                {ok, Chan} ->
                    ChanMon = erlang:monitor(process, Chan),
                    logger:info("mbb_connection: connected to ~s:~p "
                                "(heartbeat=~p, prefetch=~p)",
                                [cfg(amqp_host), cfg(amqp_port),
                                 cfg(heartbeat_seconds), cfg(prefetch_count)]),
                    State#{conn => Conn, conn_mon => ConnMon,
                           chan => Chan, chan_mon => ChanMon,
                           backoff_ms => cfg(backoff_base_ms),
                           blocked => false};
                {error, Reason} ->
                    logger:error("mbb_connection: channel open failed: ~0p", [Reason]),
                    erlang:demonitor(ConnMon, [flush]),
                    _ = try amqp_connection:close(Conn)
                        catch _:_ -> ok
                        end,
                    schedule_reconnect(State)
            end;
        {error, Reason} ->
            logger:error("mbb_connection: connect to ~s:~p failed: ~0p",
                         [cfg(amqp_host), cfg(amqp_port), Reason]),
            schedule_reconnect(State)
    end.

%% The channel is put into confirm mode (publisher confirms) and given a
%% prefetch window; the window is the consumer-side backpressure valve.
-spec open_channel(pid()) -> {ok, pid()} | {error, term()}.
open_channel(Conn) ->
    case amqp_connection:open_channel(Conn) of
        {ok, Chan} ->
            _ = amqp_channel:call(Chan, #'confirm.select'{}),
            _ = amqp_channel:call(Chan, #'basic.qos'{prefetch_count = cfg(prefetch_count)}),
            {ok, Chan};
        {error, Reason} = Error ->
            logger:error("mbb_connection: open_channel failed: ~0p", [Reason]),
            Error
    end.

-spec schedule_reconnect(map()) -> map().
schedule_reconnect(#{backoff_ms := Backoff} = State) ->
    Max = cfg(backoff_max_ms),
    Next = erlang:min(Backoff * 2, Max),
    Jitter = rand:uniform(erlang:max(Backoff div 4, 1)),
    _ = erlang:send_after(Backoff + Jitter, self(), connect),
    State#{conn => undefined, conn_mon => undefined,
           chan => undefined, chan_mon => undefined,
           blocked => false,
           backoff_ms => Next}.

-spec clear_conn(map()) -> map().
clear_conn(State) ->
    State#{conn => undefined, conn_mon => undefined,
           chan => undefined, chan_mon => undefined,
           blocked => false}.
