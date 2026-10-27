%%% @doc Top-level supervisor and application callback for
%%% message_broker_backend.
%%%
%%% Startup order matters: metrics register first (so every other
%%% component can count from the start), then the connection, then the
%%% topology (needs a channel), the publisher, the consumer (needs
%%% channel + topology) and finally the HTTP interface.
%%%
%%% Children run under a `one_for_one' strategy. After a restart the
%%% connection re-establishes its channel, the topology detects the
%%% channel change and re-declares (idempotently), and the consumer
%%% resubscribes with backoff, so no manual intervention is needed.
-module(mbb_sup).
-behaviour(supervisor).

-export([start/1, start_link/0, init/1]).

-spec start(list()) -> {ok, pid()} | {error, term()}.
start(_Args) ->
    start_link().

-spec start_link() -> {ok, pid()} | {error, term()}.
start_link() ->
    supervisor:start_link({local, ?MODULE}, ?MODULE, []).

-spec init([]) -> {ok, {map(), [map()]}}.
init([]) ->
    SupFlags = #{strategy => one_for_one,
                 intensity => 10,
                 period => 10},
    Children =
        [#{id => mbb_metrics,
           start => {mbb_metrics, start_link, []},
           restart => permanent,
           shutdown => 5000,
           type => worker,
           modules => [mbb_metrics]},
         #{id => mbb_connection,
           start => {mbb_connection, start_link, []},
           restart => permanent,
           shutdown => 5000,
           type => worker,
           modules => [mbb_connection]},
         #{id => mbb_topology,
           start => {mbb_topology, start_link, []},
           restart => permanent,
           shutdown => 5000,
           type => worker,
           modules => [mbb_topology]},
         #{id => mbb_publisher,
           start => {mbb_publisher, start_link, []},
           restart => permanent,
           shutdown => 5000,
           type => worker,
           modules => [mbb_publisher]},
         #{id => mbb_consumer,
           start => {mbb_consumer, start_link, []},
           restart => permanent,
           shutdown => 5000,
           type => worker,
           modules => [mbb_consumer]},
         #{id => mbb_http,
           start => {mbb_http, start_link, []},
           restart => permanent,
           shutdown => 5000,
           type => worker,
           modules => [mbb_http]}],
    {ok, {SupFlags, Children}}.
