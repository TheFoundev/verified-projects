%%% @doc Minimal HTTP interface on `inets/httpd' (no cowboy).
%%%
%%% Routes:
%%% <ul>
%%%   <li>`GET /health'  - liveness: always 200 while the app runs;</li>
%%%   <li>`GET /ready'   - readiness: 200 only when the AMQP connection
%%%       is up and the topology has been declared;</li>
%%%   <li>`GET /metrics' - Prometheus text exposition (0.0.4);</li>
%%%   <li>`POST /orders' - JSON body -> `mbb_publisher:publish/1'
%%%       (202 accepted, 400 bad JSON/shape, 503 broker blocked/down or
%%%       nacked).</li>
%%% </ul>
%%%
%%% httpd invokes {@link do/1} (mod_esi) in its own request processes;
%%% the gen_server wrapper only owns the httpd instance.
-module(mbb_http).
-behaviour(gen_server).

-include_lib("inets/include/httpd.hrl").

-export([start_link/0, port/0]).
-export([do/1]).
-export([init/1, handle_call/3, handle_cast/2, handle_info/2,
         terminate/2, code_change/3]).

%% ===================================================================
%% API
%% ===================================================================

-spec start_link() -> {ok, pid()} | {error, term()}.
start_link() ->
    gen_server:start_link({local, ?MODULE}, ?MODULE, [], []).

%% @doc Configured listen port (handy in tests and logs).
-spec port() -> non_neg_integer().
port() ->
    gen_server:call(?MODULE, port).

%% ===================================================================
%% gen_server owning the httpd instance
%% ===================================================================

-spec init([]) -> {ok, map()} | {stop, term()}.
init([]) ->
    Port = application:get_env(message_broker_backend, http_port, 8080),
    Options = [{port, Port},
               {server_name, "message-broker-backend"},
               {server_root, "/tmp"},
               {document_root, "/tmp"},
               {modules, [mbb_http]}],
    case inets:start(httpd, Options) of
        {ok, Pid} ->
            logger:info("mbb_http: listening on :~p", [Port]),
            {ok, #{httpd => Pid, port => Port}};
        {error, {already_started, Pid}} ->
            {ok, #{httpd => Pid, port => Port}};
        {error, Reason} ->
            {stop, {httpd_start_failed, Reason}}
    end.

-spec handle_call(term(), {pid(), term()}, map()) -> {reply, term(), map()}.
handle_call(port, _From, #{port := Port} = State) ->
    {reply, Port, State};
handle_call(_Request, _From, State) ->
    {reply, {error, unsupported}, State}.

-spec handle_cast(term(), map()) -> {noreply, map()}.
handle_cast(_Message, State) ->
    {noreply, State}.

-spec handle_info(term(), map()) -> {noreply, map()}.
handle_info(_Message, State) ->
    {noreply, State}.

-spec terminate(term(), map()) -> ok.
terminate(_Reason, #{httpd := Pid}) when is_pid(Pid) ->
    _ = inets:stop(httpd, Pid),
    ok;
terminate(_Reason, _State) ->
    ok.

-spec code_change(term(), map(), term()) -> {ok, map()}.
code_change(_OldVsn, State, _Extra) ->
    {ok, State}.

%% ===================================================================
%% mod_esi entry point (called by httpd request processes)
%% ===================================================================

-spec do(#mod{}) -> {response, {pos_integer(), list(), binary() | iolist()}}.
do(#mod{method = Method, request_uri = Uri, entity_body = Body}) ->
    Path = path_only(Uri),
    dispatch(norm_method(Method), Path, Body).

-spec dispatch(string(), string(), term()) ->
          {response, {pos_integer(), list(), binary() | iolist()}}.
dispatch("get", "/health", _Body) ->
    json_response(200, mbb_order:to_json(#{<<"status">> => <<"ok">>}));
dispatch("get", "/ready", _Body) ->
    ready_response();
dispatch("get", "/metrics", _Body) ->
    {response, {200,
                [{"content-type", "text/plain; version=0.0.4; charset=utf-8"}],
                mbb_metrics:collect()}};
dispatch("post", "/orders", Body) ->
    orders_post(to_binary(Body));
dispatch(_Method, _Path, _Body) ->
    json_response(404, error_json(not_found)).

-spec ready_response() -> {response, {pos_integer(), list(), binary()}}.
ready_response() ->
    Ready = mbb_connection:is_connected() andalso mbb_topology:ready(),
    case Ready of
        true ->
            json_response(200, mbb_order:to_json(#{<<"status">> => <<"ready">>}));
        false ->
            json_response(503,
                          mbb_order:to_json(#{<<"status">> => <<"not_ready">>}))
    end.

-spec orders_post(binary()) -> {response, {pos_integer(), list(), binary()}}.
orders_post(RawBody) ->
    case mbb_order:from_json(RawBody) of
        {error, Reason} ->
            json_response(400, error_json({bad_json, Reason}));
        {ok, Order} ->
            publish_response(mbb_publisher:publish(Order))
    end.

-spec publish_response(ok | {error, term()}) ->
          {response, {pos_integer(), list(), binary()}}.
publish_response(ok) ->
    json_response(202, mbb_order:to_json(#{<<"status">> => <<"accepted">>}));
publish_response({error, invalid}) ->
    json_response(400, error_json(invalid_order));
publish_response({error, blocked}) ->
    json_response(503, error_json(broker_blocked));
publish_response({error, not_connected}) ->
    json_response(503, error_json(broker_unavailable));
publish_response({error, nack}) ->
    json_response(503, error_json(publish_nacked));
publish_response({error, Other}) ->
    json_response(503, error_json(Other)).

-spec error_json(term()) -> binary().
error_json(Reason) ->
    mbb_order:to_json(#{<<"status">> => <<"error">>, <<"error">> => Reason}).

-spec json_response(pos_integer(), binary()) ->
          {response, {pos_integer(), list(), binary()}}.
json_response(Code, Body) ->
    {response, {Code,
                [{"content-type", "application/json"},
                 {"cache-control", "no-store"}],
                Body}}.

%% ===================================================================
%% Request helpers
%% ===================================================================

-spec norm_method(term()) -> string().
norm_method(M) when is_atom(M) -> string:lowercase(atom_to_list(M));
norm_method(M) when is_list(M) -> string:lowercase(M);
norm_method(M) when is_binary(M) -> string:lowercase(binary_to_list(M)).

-spec path_only(term()) -> string().
path_only(Uri) when is_binary(Uri) ->
    path_only(binary_to_list(Uri));
path_only(Uri) when is_list(Uri) ->
    case string:split(Uri, "?", leading) of
        [Path | _] -> Path;
        Path -> Path
    end;
path_only(_Uri) ->
    "/".

-spec to_binary(term()) -> binary().
to_binary(Body) when is_binary(Body) -> Body;
to_binary(Body) when is_list(Body) -> iolist_to_binary(Body);
to_binary(_Body) -> <<>>.
