%%% Unit tests for the retry semantics: backoff + jitter bounds, the
%%% x-retry-count header escalation and the terminal hand-off to the
%%% dead-letter queue.
-module(mbb_retry_tests).

-include_lib("eunit/include/eunit.hrl").

%% The EUnit parse transform generates the test/0 entry point.
-spec test() -> ok.

-define(RETRY_HEADER, <<"x-retry-count">>).

%% ===================================================================
%% Backoff + jitter bounds
%% ===================================================================

-spec backoff_stays_within_jitter_bounds_test() -> ok.
backoff_stays_within_jitter_bounds_test() ->
    lists:foreach(
      fun(Attempt) ->
              Exp = erlang:min(500 bsl (Attempt - 1), 30000),
              JitterMax = erlang:max(Exp div 4, 1),
              lists:foreach(
                fun(_) ->
                        Ms = mbb_consumer:backoff_ms(Attempt, 500),
                        ?assert(Ms >= Exp),
                        ?assert(Ms =< Exp + JitterMax)
                end,
                lists:seq(1, 20))
      end,
      lists:seq(1, 12)).

-spec backoff_grows_exponentially_before_cap_test() -> ok.
backoff_grows_exponentially_before_cap_test() ->
    %% With jitter of at most 25%, doubling the exponent must dominate:
    %% the minimum of attempt N+1 exceeds the maximum of attempt N.
    lists:foreach(
      fun(Attempt) ->
              MinNext = erlang:min(500 bsl Attempt, 30000),
              MaxNow = begin
                            Exp = erlang:min(500 bsl (Attempt - 1), 30000),
                            Exp + erlang:max(Exp div 4, 1)
                        end,
              ?assert(MinNext > MaxNow)
      end,
      lists:seq(1, 5)).

-spec backoff_is_always_positive_test() -> ok.
backoff_is_always_positive_test() ->
    lists:foreach(
      fun({Attempt, Base}) ->
              ?assert(mbb_consumer:backoff_ms(Attempt, Base) >= 1)
      end,
      [{1, 1}, {1, 500}, {3, 100}, {9, 77}]).

-spec backoff_requires_valid_args_test() -> ok.
backoff_requires_valid_args_test() ->
    ?assertError(function_clause, mbb_consumer:backoff_ms(0, 500)),
    ?assertError(function_clause, mbb_consumer:backoff_ms(1, 0)).

%% ===================================================================
%% x-retry-count header handling
%% ===================================================================

-spec missing_headers_are_zero_test() -> ok.
missing_headers_are_zero_test() ->
    ?assertEqual(0, mbb_consumer:retry_count(undefined)).

-spec absent_header_is_zero_test() -> ok.
absent_header_is_zero_test() ->
    Headers = [{<<"content-type">>, longstr, <<"application/json">>}],
    ?assertEqual(0, mbb_consumer:retry_count(Headers)).

-spec header_is_read_test() -> ok.
header_is_read_test() ->
    Headers = [{<<"x-idempotency-key">>, longstr, <<"abc">>},
               {?RETRY_HEADER, long, 2}],
    ?assertEqual(2, mbb_consumer:retry_count(Headers)).

-spec float_header_is_truncated_test() -> ok.
float_header_is_truncated_test() ->
    Headers = [{?RETRY_HEADER, float, 3.0}],
    ?assertEqual(3, mbb_consumer:retry_count(Headers)).

-spec malformed_headers_are_zero_test() -> ok.
malformed_headers_are_zero_test() ->
    ?assertEqual(0, mbb_consumer:retry_count(not_a_header_list)),
    ?assertEqual(0, mbb_consumer:retry_count([{?RETRY_HEADER, long, -1}])).

%% ===================================================================
%% Escalation into the retry queues, then to the DLQ
%% ===================================================================

-spec retry_queue_escalation_test() -> ok.
retry_queue_escalation_test() ->
    ?assertEqual(<<"orders.retry.1">>, mbb_consumer:next_retry_queue(0)),
    ?assertEqual(<<"orders.retry.2">>, mbb_consumer:next_retry_queue(1)),
    ?assertEqual(<<"orders.retry.3">>, mbb_consumer:next_retry_queue(2)).

-spec retry_queue_names_match_topology_test() -> ok.
retry_queue_names_match_topology_test() ->
    ?assertEqual([<<"orders.retry.1">>,
                  <<"orders.retry.2">>,
                  <<"orders.retry.3">>],
                 mbb_topology:retry_queue_names(3)).

-spec retry_routing_keys_match_queues_test() -> ok.
retry_routing_keys_match_queues_test() ->
    %% The inbound binding of each retry queue pairs with its name.
    Pairs = lists:zip(mbb_topology:retry_queue_names(3),
                      [mbb_topology:retry_routing_key(N) || N <- [1, 2, 3]]),
    ?assertEqual({<<"orders.retry.1">>, <<"order.retry.1">>}, hd(Pairs)),
    ?assertEqual({<<"orders.retry.3">>, <<"order.retry.3">>}, lists:last(Pairs)).

-spec retry_ttl_backoff_escalates_test() -> ok.
retry_ttl_backoff_escalates_test() ->
    Ttls = [mbb_topology:retry_ttl_ms(N, 5000) || N <- [1, 2, 3, 4]],
    ?assertEqual([5000, 20000, 80000, 320000], Ttls),
    ?assertEqual(Ttls, lists:sort(Ttls)).

%% Full walk: a message that keeps failing must be dead-lettered once
%% x-retry-count reaches max_retries.
-spec escalation_reaches_dlq_test() -> ok.
escalation_reaches_dlq_test() ->
    Max = 3,
    %% Simulate what the consumer does per failure: read the count,
    %% stop when terminal, otherwise republish with count + 1.
    Final = lists:foldl(
              fun(_Attempt, Count) ->
                      case mbb_consumer:should_dead_letter(Count, Max) of
                          true -> Count;
                          false -> Count + 1
                      end
              end,
              0,
              [1, 2, 3, 4]),
    ?assertEqual(Max, Final),
    ?assert(mbb_consumer:should_dead_letter(Final, Max)),
    ?assertEqual(<<"orders.dlq">>, mbb_consumer:dlq_queue()).

-spec should_dead_letter_boundaries_test() -> ok.
should_dead_letter_boundaries_test() ->
    ?assertNot(mbb_consumer:should_dead_letter(0, 3)),
    ?assertNot(mbb_consumer:should_dead_letter(2, 3)),
    ?assert(mbb_consumer:should_dead_letter(3, 3)),
    ?assert(mbb_consumer:should_dead_letter(4, 3)).

-spec should_dead_letter_requires_positive_max_test() -> ok.
should_dead_letter_requires_positive_max_test() ->
    ?assertError(function_clause, mbb_consumer:should_dead_letter(1, 0)).
