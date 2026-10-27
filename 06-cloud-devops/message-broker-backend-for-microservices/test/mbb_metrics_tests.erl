%%% Unit tests for the pure arithmetic in mbb_metrics: histogram bucket
%%% placement and rate calculation. The counters themselves are thin
%%% wrappers over the prometheus library, so what is tested here is the
%%% bookkeeping maths the metrics rely on.
-module(mbb_metrics_tests).

-include_lib("eunit/include/eunit.hrl").

%% The EUnit parse transform generates the test/0 entry point.
-spec test() -> ok.

%% ===================================================================
%% Histogram buckets
%% ===================================================================

-spec buckets_are_strictly_increasing_test() -> ok.
buckets_are_strictly_increasing_test() ->
    Buckets = mbb_metrics:latency_buckets(),
    ?assert(length(Buckets) >= 8),
    Pairs = lists:zip(lists:droplast(Buckets), tl(Buckets)),
    ?assert(lists:all(fun({A, B}) -> A < B end, Pairs)).

-spec buckets_are_positive_test() -> ok.
buckets_are_positive_test() ->
    ?assert(lists:all(fun(B) -> B > 0 end, mbb_metrics:latency_buckets())).

-spec observation_below_first_bound_is_zero_test() -> ok.
observation_below_first_bound_is_zero_test() ->
    Buckets = mbb_metrics:latency_buckets(),
    ?assertEqual(0, mbb_metrics:bucket_of(0.004, Buckets)).

-spec observation_on_bound_counts_test() -> ok.
observation_on_bound_counts_test() ->
    %% Prometheus-style cumulative bucket: bound values are inclusive.
    Buckets = mbb_metrics:latency_buckets(),
    ?assertEqual(1, mbb_metrics:bucket_of(0.005, Buckets)).

-spec observation_between_bounds_test() -> ok.
observation_between_bounds_test() ->
    Buckets = mbb_metrics:latency_buckets(),
    %% 0.06 is <= 0.005, 0.01, 0.025 and 0.05 but > 0.1
    ?assertEqual(4, mbb_metrics:bucket_of(0.06, Buckets)).

-spec observation_above_last_bound_is_all_test() -> ok.
observation_above_last_bound_is_all_test() ->
    Buckets = mbb_metrics:latency_buckets(),
    ?assertEqual(length(Buckets), mbb_metrics:bucket_of(99.0, Buckets)).

-spec bucket_of_empty_bounds_is_zero_test() -> ok.
bucket_of_empty_bounds_is_zero_test() ->
    ?assertEqual(0, mbb_metrics:bucket_of(3.14, [])).

-spec bucket_of_is_monotonic_test() -> ok.
bucket_of_is_monotonic_test() ->
    Buckets = mbb_metrics:latency_buckets(),
    Values = [0.0, 0.004, 0.005, 0.03, 0.2, 1.0, 7.0, 60.0],
    Indices = [mbb_metrics:bucket_of(V, Buckets) || V <- Values],
    ?assertEqual(Indices, lists:sort(Indices)).

%% ===================================================================
%% Rate arithmetic
%% ===================================================================

-spec rate_is_count_over_elapsed_test() -> ok.
rate_is_count_over_elapsed_test() ->
    ?assertEqual(50.0, mbb_metrics:rate_per_second(100, 2000)).

-spec rate_with_zero_elapsed_is_zero_test() -> ok.
rate_with_zero_elapsed_is_zero_test() ->
    ?assertEqual(0.0, mbb_metrics:rate_per_second(10, 0)),
    ?assertEqual(0.0, mbb_metrics:rate_per_second(10, 0)).

-spec rate_with_zero_count_is_zero_test() -> ok.
rate_with_zero_count_is_zero_test() ->
    ?assertEqual(0.0, mbb_metrics:rate_per_second(0, 1500)).

-spec rate_is_float_test() -> ok.
rate_is_float_test() ->
    ?assert(is_float(mbb_metrics:rate_per_second(1, 3000))).
