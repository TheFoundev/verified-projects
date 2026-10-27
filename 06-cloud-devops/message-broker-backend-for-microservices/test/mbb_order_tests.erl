%%% Unit tests for the pure order domain: validation, the retry vs
%%% dead-letter classification, deterministic failure injection and the
%%% JSON codec. No broker required.
-module(mbb_order_tests).

-include_lib("eunit/include/eunit.hrl").

%% The EUnit parse transform generates the test/0 entry point.
-spec test() -> ok.

%% ===================================================================
%% Fixtures
%% ===================================================================

%% A valid base order; ids are chosen at runtime so the deterministic
%% gateway-timeout rule (phash2 rem 7) does or does not fire, instead of
%% hardcoding an assumption about the hash.
base_order_with_id(Id) ->
    #{<<"order_id">> => Id,
      <<"currency">> => <<"EUR">>,
      <<"items">> => [#{<<"sku">> => <<"SKU-1">>, <<"qty">> => 2}],
      <<"amount">> => 100}.

%% First order id that the payment gateway does NOT time out on.
-spec stable_id() -> binary().
stable_id() ->
    stable_id(1).

stable_id(N) ->
    Id = <<"ord-stable-", (integer_to_binary(N))/binary>>,
    case erlang:phash2(Id) rem 7 of
        0 -> stable_id(N + 1);
        _ -> Id
    end.

%% First order id that DOES time out (retry branch).
-spec timeout_id() -> binary().
timeout_id() ->
    timeout_id(1).

timeout_id(N) ->
    Id = <<"ord-timeout-", (integer_to_binary(N))/binary>>,
    case erlang:phash2(Id) rem 7 of
        0 -> Id;
        _ -> timeout_id(N + 1)
    end.

-spec stable_order() -> mbb_order:order().
stable_order() ->
    base_order_with_id(stable_id()).

%% ===================================================================
%% Happy path
%% ===================================================================

-spec happy_path_is_ok_test() -> ok.
happy_path_is_ok_test() ->
    ?assertEqual(ok, mbb_order:process(stable_order())).

-spec synthetic_orders_validate_test() -> ok.
synthetic_orders_validate_test() ->
    lists:foreach(
      fun(N) ->
              ?assertMatch({ok, _}, mbb_order:validate(mbb_order:synthetic(N)))
      end,
      lists:seq(1, 40)).

-spec charged_flag_set_on_success_test() -> ok.
charged_flag_set_on_success_test() ->
    {ok, Charged} = mbb_order:charge(stable_order()),
    ?assertEqual(true, maps:get(<<"charged">>, Charged)).

%% ===================================================================
%% Validation failures are terminal (dead letter, never retried)
%% ===================================================================

-spec missing_order_id_is_dead_letter_test() -> ok.
missing_order_id_is_dead_letter_test() ->
    Order = maps:remove(<<"order_id">>, stable_order()),
    ?assertEqual({dead_letter, {validation, missing_order_id}},
                 mbb_order:process(Order)).

-spec empty_order_id_is_dead_letter_test() -> ok.
empty_order_id_is_dead_letter_test() ->
    Order = (stable_order())#{<<"order_id">> => <<>>},
    ?assertEqual({dead_letter, {validation, missing_order_id}},
                 mbb_order:process(Order)).

-spec non_map_is_dead_letter_test() -> ok.
non_map_is_dead_letter_test() ->
    ?assertEqual({dead_letter, {validation, not_an_object}},
                 mbb_order:process([an_atom, another_atom])).

-spec unsupported_currency_is_dead_letter_test() -> ok.
unsupported_currency_is_dead_letter_test() ->
    Order = (stable_order())#{<<"currency">> => <<"GBP">>},
    ?assertEqual({dead_letter, {validation, {unsupported_currency, <<"GBP">>}}},
                 mbb_order:process(Order)).

-spec empty_items_is_dead_letter_test() -> ok.
empty_items_is_dead_letter_test() ->
    Order = (stable_order())#{<<"items">> => []},
    ?assertEqual({dead_letter, {validation, invalid_items}},
                 mbb_order:process(Order)).

-spec invalid_item_qty_is_dead_letter_test() -> ok.
invalid_item_qty_is_dead_letter_test() ->
    Order = (stable_order())#{<<"items">> => [#{<<"sku">> => <<"SKU-1">>,
                                                <<"qty">> => 0}]},
    ?assertEqual({dead_letter, {validation, {invalid_item,
                                             #{<<"sku">> => <<"SKU-1">>,
                                               <<"qty">> => 0}}}},
                 mbb_order:process(Order)).

-spec negative_amount_is_dead_letter_test() -> ok.
negative_amount_is_dead_letter_test() ->
    Order = (stable_order())#{<<"amount">> => -5},
    ?assertEqual({dead_letter, {validation, {invalid_amount, -5}}},
                 mbb_order:process(Order)).

%% ===================================================================
%% Pipeline failure classification: retry vs dead letter
%% ===================================================================

-spec out_of_stock_is_dead_letter_test() -> ok.
out_of_stock_is_dead_letter_test() ->
    %% SKU-2 holds 50 units; ordering 51 is terminal.
    Order = (stable_order())#{<<"items">> => [#{<<"sku">> => <<"SKU-2">>,
                                                <<"qty">> => 51}]},
    ?assertEqual({dead_letter, {out_of_stock, <<"SKU-2">>}},
                 mbb_order:process(Order)).

-spec charge_limit_is_dead_letter_test() -> ok.
charge_limit_is_dead_letter_test() ->
    Order = (stable_order())#{<<"amount">> => 200000},
    ?assertEqual({dead_letter, {declined, amount_over_limit}},
                 mbb_order:process(Order)).

-spec inventory_busy_is_retry_test() -> ok.
inventory_busy_is_retry_test() ->
    Order = (stable_order())#{<<"items">> => [#{<<"sku">> => <<"SKU-1">>,
                                                <<"qty">> => 1200}]},
    ?assertEqual({retry, {transient, inventory_backend_busy}},
                 mbb_order:process(Order)).

-spec gateway_timeout_is_retry_test() -> ok.
gateway_timeout_is_retry_test() ->
    Order = base_order_with_id(timeout_id()),
    ?assertEqual({retry, {transient, gateway_timeout}},
                 mbb_order:process(Order)).

-spec warehouse_down_is_retry_test() -> ok.
warehouse_down_is_retry_test() ->
    Order = (stable_order())#{<<"items">> => [#{<<"sku">> => <<"WAREHOUSE-DOWN">>,
                                                <<"qty">> => 1}]},
    ?assertEqual({retry, warehouse_unavailable}, mbb_order:process(Order)).

-spec validation_runs_before_inventory_test() -> ok.
validation_runs_before_inventory_test() ->
    %% An invalid order must be classified as a validation dead letter,
    %% even though its quantity would also trip the inventory rules.
    Order = #{<<"order_id">> => <<"ord-x">>,
              <<"currency">> => <<"JPY">>,
              <<"items">> => [#{<<"sku">> => <<"SKU-1">>, <<"qty">> => 5000}],
              <<"amount">> => 100},
    ?assertEqual({dead_letter, {validation, {unsupported_currency, <<"JPY">>}}},
                 mbb_order:process(Order)).

%% ===================================================================
%% Idempotency key
%% ===================================================================

-spec idempotency_key_is_deterministic_test() -> ok.
idempotency_key_is_deterministic_test() ->
    Order = stable_order(),
    ?assertEqual(mbb_order:idempotency_key(Order),
                 mbb_order:idempotency_key(Order)).

-spec idempotency_key_differs_per_order_test() -> ok.
idempotency_key_differs_per_order_test() ->
    A = stable_order(),
    B = A#{<<"order_id">> => <<"other-order">>},
    ?assertNotEqual(mbb_order:idempotency_key(A), mbb_order:idempotency_key(B)).

%% ===================================================================
%% JSON codec
%% ===================================================================

-spec json_round_trip_test() -> ok.
json_round_trip_test() ->
    Order = stable_order(),
    {ok, Decoded} = mbb_order:from_json(mbb_order:to_json(Order)),
    ?assertEqual(Order, Decoded).

-spec json_escaping_round_trip_test() -> ok.
json_escaping_round_trip_test() ->
    Order = (stable_order())#{<<"order_id">> => <<"quo\"te\\new\nline">>},
    Json = mbb_order:to_json(Order),
    ?assertNotEqual(nomatch, binary:match(Json, <<"\\\"">>)),
    {ok, Decoded} = mbb_order:from_json(Json),
    ?assertEqual(Order, Decoded).

-spec json_deterministic_output_test() -> ok.
json_deterministic_output_test() ->
    Order = stable_order(),
    ?assertEqual(mbb_order:to_json(Order), mbb_order:to_json(Order)).

-spec json_decodes_scalars_test() -> ok.
json_decodes_scalars_test() ->
    ?assertEqual({ok, #{<<"a">> => 1, <<"b">> => [true, false, null],
                        <<"c">> => -2.5e2,
                        <<"d">> => <<"x">>}},
                 mbb_order:from_json(
                   <<"{\"a\":1,\"b\":[true,false,null],\"c\":-2.5e2,\"d\":\"x\"}">>)).

-spec json_rejects_trailing_garbage_test() -> ok.
json_rejects_trailing_garbage_test() ->
    ?assertEqual({error, trailing_garbage},
                 mbb_order:from_json(<<"{} extra">>)).

-spec json_rejects_malformed_test() -> ok.
json_rejects_malformed_test() ->
    ?assertMatch({error, _}, mbb_order:from_json(<<"{not json">>)).

-spec json_rejects_truncated_test() -> ok.
json_rejects_truncated_test() ->
    ?assertEqual({error, unterminated_string},
                 mbb_order:from_json(<<"{\"a\": \"oops">>)).
