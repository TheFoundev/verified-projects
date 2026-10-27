%%% @doc Order domain logic for the message-broker backend.
%%%
%%% `process/1' runs the four-step order pipeline
%%% (validate -> reserve inventory -> charge -> fulfil) and classifies the
%%% outcome as `ok', `{retry, Reason}' (transient failure, safe to try
%%% again) or `{dead_letter, Reason}' (terminal, must not be retried).
%%%
%%% Every function here is pure: the failure-injection rules are
%%% deterministic functions of the order itself (see {@link charge/1}), so
%%% each branch of the pipeline can be exercised in unit tests without a
%%% broker, a database or a clock.
%%%
%%% The module also ships a small JSON encoder/decoder covering the JSON
%%% subset used by the HTTP API (objects, arrays, strings with standard
%%% escapes, numbers, booleans, null) so the application needs no extra
%%% JSON dependency on top of the AMQP client.
-module(mbb_order).

-export([process/1, validate/1, reserve_inventory/1, charge/1, fulfil/1,
         to_json/1, from_json/1, idempotency_key/1, synthetic/1]).

-export_type([order/0, outcome/0]).

-type order() :: #{binary() => term()}.
-type outcome() :: ok | {retry, term()} | {dead_letter, term()}.

%% Deterministic simulation knobs. These stand in for the real inventory
%% and payment services an order backend would call over the network.
-define(STOCK, #{<<"SKU-1">> => 100,
                 <<"SKU-2">> => 50,
                 <<"SKU-3">> => 25,
                 <<"WAREHOUSE-DOWN">> => 100}).
-define(CURRENCIES, [<<"EUR">>, <<"USD">>]).
-define(CHARGE_RETRY_DIV, 7).
-define(CHARGE_LIMIT, 100000).
-define(INVENTORY_BUSY_QTY, 1000).

%% ===================================================================
%% Pipeline
%% ===================================================================

%% @doc Run the full order pipeline and classify the outcome.
-spec process(term()) -> outcome().
process(Order) ->
    case validate(Order) of
        {ok, Valid} ->
            case reserve_inventory(Valid) of
                {ok, Reserved} ->
                    case charge(Reserved) of
                        {ok, Charged} -> fulfil(Charged);
                        {error, Reason} -> classify(Reason)
                    end;
                {error, Reason} ->
                    classify(Reason)
            end;
        {error, Reason} ->
            {dead_letter, {validation, Reason}}
    end.

%% @doc Map a step error to retry (transient) or dead_letter (terminal).
-spec classify(term()) -> outcome().
classify({transient, Detail}) -> {retry, {transient, Detail}};
classify(Detail) -> {dead_letter, Detail}.

%% ===================================================================
%% Steps
%% ===================================================================

%% @doc Structural validation. Invalid orders are terminal: retrying
%%      them can never succeed, so they must be dead-lettered.
-spec validate(term()) -> {ok, order()} | {error, term()}.
validate(Order) when is_map(Order) ->
    validate_id(maps:get(<<"order_id">>, Order, undefined), Order);
validate(_NotAMap) ->
    {error, not_an_object}.

-spec validate_id(term(), order()) -> {ok, order()} | {error, term()}.
validate_id(Id, _Order) when Id =:= undefined; Id =:= <<>> ->
    {error, missing_order_id};
validate_id(Id, Order) when is_binary(Id) ->
    validate_currency(maps:get(<<"currency">>, Order, undefined), Order);
validate_id(_Id, _Order) ->
    {error, invalid_order_id}.

-spec validate_currency(term(), order()) -> {ok, order()} | {error, term()}.
validate_currency(Currency, Order) ->
    case lists:member(Currency, ?CURRENCIES) of
        true ->
            validate_items(maps:get(<<"items">>, Order, undefined), Order);
        false ->
            {error, {unsupported_currency, Currency}}
    end.

-spec validate_items(term(), order()) -> {ok, order()} | {error, term()}.
validate_items(Items, Order) when is_list(Items), Items =/= [] ->
    case validate_each_item(Items) of
        ok -> validate_amount(maps:get(<<"amount">>, Order, undefined), Order);
        {error, _} = Err -> Err
    end;
validate_items(_Items, _Order) ->
    {error, invalid_items}.

-spec validate_each_item([term()]) -> ok | {error, term()}.
validate_each_item([]) ->
    ok;
validate_each_item([Item | Rest]) when is_map(Item) ->
    Sku = maps:get(<<"sku">>, Item, undefined),
    Qty = maps:get(<<"qty">>, Item, undefined),
    case is_binary(Sku) andalso Sku =/= <<>>
        andalso is_integer(Qty) andalso Qty > 0 of
        true -> validate_each_item(Rest);
        false -> {error, {invalid_item, Item}}
    end;
validate_each_item([Item | _Rest]) ->
    {error, {invalid_item, Item}}.

-spec validate_amount(term(), order()) -> {ok, order()} | {error, term()}.
validate_amount(Amount, Order) when is_number(Amount), Amount > 0 ->
    {ok, Order};
validate_amount(Amount, _Order) ->
    {error, {invalid_amount, Amount}}.

%% @doc Deterministic inventory reservation against the simulated stock
%%      map. Very large orders see the inventory backend busy (transient,
%%      retry), orders exceeding per-SKU stock are terminal.
-spec reserve_inventory(order()) -> {ok, order()} | {error, term()}.
reserve_inventory(Order) ->
    Items = maps:get(<<"items">>, Order, []),
    case total_qty(Items, 0) of
        Qty when Qty >= ?INVENTORY_BUSY_QTY ->
            {error, {transient, inventory_backend_busy}};
        _Total ->
            check_stock(Items, Order)
    end.

-spec total_qty([term()], non_neg_integer()) -> non_neg_integer().
total_qty([], Acc) ->
    Acc;
total_qty([Item | Rest], Acc) when is_map(Item) ->
    total_qty(Rest, Acc + maps:get(<<"qty">>, Item, 0)).

-spec check_stock([term()], order()) -> {ok, order()} | {error, term()}.
check_stock([], Order) ->
    {ok, Order};
check_stock([Item | Rest], Order) ->
    Sku = maps:get(<<"sku">>, Item, <<>>),
    Qty = maps:get(<<"qty">>, Item, 0),
    case Qty =< maps:get(Sku, ?STOCK, 0) of
        true -> check_stock(Rest, Order);
        false -> {error, {out_of_stock, Sku}}
    end.

%% @doc Deterministic payment gateway. One in every {?CHARGE_RETRY_DIV}
%%      order ids times out (transient -> retry); amounts above the
%%      limit are declined (terminal).
-spec charge(order()) -> {ok, order()} | {error, term()}.
charge(Order) ->
    Amount = maps:get(<<"amount">>, Order, 0),
    OrderId = maps:get(<<"order_id">>, Order, <<>>),
    case Amount > ?CHARGE_LIMIT of
        true ->
            {error, {declined, amount_over_limit}};
        false ->
            maybe_gateway_timeout(OrderId, Order)
    end.

-spec maybe_gateway_timeout(binary(), order()) -> {ok, order()} | {error, term()}.
maybe_gateway_timeout(OrderId, Order) ->
    case erlang:phash2(OrderId) rem ?CHARGE_RETRY_DIV of
        0 -> {error, {transient, gateway_timeout}};
        _ -> {ok, Order#{<<"charged">> => true}}
    end.

%% @doc Fulfilment hand-off. The WAREHOUSE-DOWN node is stocked but
%%      offline, which produces a retryable fulfilment failure.
-spec fulfil(order()) -> outcome().
fulfil(Order) ->
    Items = maps:get(<<"items">>, Order, []),
    case has_sku(<<"WAREHOUSE-DOWN">>, Items) of
        true -> {retry, warehouse_unavailable};
        false -> ok
    end.

-spec has_sku(binary(), [term()]) -> boolean().
has_sku(_Sku, []) ->
    false;
has_sku(Sku, [Item | Rest]) when is_map(Item) ->
    case maps:get(<<"sku">>, Item, <<>>) of
        Sku -> true;
        _Other -> has_sku(Sku, Rest)
    end.

%% ===================================================================
%% Helpers used by the publisher / consumer
%% ===================================================================

%% @doc Stable idempotency key derived from the order id and its items.
%%      Republishing the same order yields the same key, so the
%%      consumer's seen-cache can drop redelivered duplicates.
-spec idempotency_key(order()) -> binary().
idempotency_key(Order) ->
    OrderId = maps:get(<<"order_id">>, Order, <<>>),
    Items = maps:get(<<"items">>, Order, []),
    Fingerprint = erlang:phash2({OrderId, Items}),
    iolist_to_binary(io_lib:format("~s-~8.16.0b", [OrderId, Fingerprint])).

%% @doc Deterministic synthetic order used by the burst generator and
%%      the demos. Valid for every N.
-spec synthetic(non_neg_integer()) -> order().
synthetic(N) ->
    Sku = case N rem 3 of
              0 -> <<"SKU-1">>;
              1 -> <<"SKU-2">>;
              _ -> <<"SKU-3">>
          end,
    #{<<"order_id">> => iolist_to_binary(io_lib:format("burst-~8..0b", [N])),
      <<"currency">> => <<"EUR">>,
      <<"items">> => [#{<<"sku">> => Sku, <<"qty">> => 1 + (N rem 5)}],
      <<"amount">> => 100 + (N rem 90)}.

%% ===================================================================
%% JSON encoding (small dependency-free subset)
%% ===================================================================

%% @doc Encode a map/list/scalar term as a JSON binary. Map keys are
%%      emitted sorted, so the output is deterministic.
-spec to_json(term()) -> binary().
to_json(Term) ->
    iolist_to_binary(encode(Term)).

-spec encode(term()) -> iolist().
encode(null) -> "null";
encode(true) -> "true";
encode(false) -> "false";
encode(N) when is_integer(N) -> integer_to_list(N);
encode(N) when is_float(N) -> io_lib:format("~w", [N]);
encode(B) when is_binary(B) -> encode_string(unicode:characters_to_list(B));
encode(A) when is_atom(A) -> encode_string(atom_to_list(A));
encode(L) when is_list(L) -> [$[, join_encoded([encode(E) || E <- L]), $]];
encode(M) when is_map(M) ->
    Pairs = lists:sort(maps:to_list(M)),
    Encoded = [[encode(K), $:, encode(V)] || {K, V} <- Pairs],
    [${, join_encoded(Encoded), $}];
encode(Other) -> erlang:error({cannot_encode_json, Other}).

-spec join_encoded([iolist()]) -> iolist().
join_encoded([]) -> "";
join_encoded([One]) -> One;
join_encoded([Head | Rest]) -> [Head, $, | join_encoded(Rest)].

-spec encode_string(string()) -> iolist().
encode_string(Str) ->
    [$", lists:flatten([escape_char(C) || C <- Str]), $"].

-spec escape_char(char()) -> string().
escape_char($") -> "\\\"";
escape_char($\\) -> "\\\\";
escape_char($\n) -> "\\n";
escape_char($\r) -> "\\r";
escape_char($\t) -> "\\t";
escape_char(C) when C < 32 -> io_lib:format("\\u~4.16.0b", [C]);
escape_char(C) -> [C].

%% @doc Decode a JSON binary (subset). Returns `{error, Reason}' instead
%%      of throwing, so the HTTP layer can answer 400 safely.
-spec from_json(binary() | string()) -> {ok, term()} | {error, term()}.
from_json(Input) when is_binary(Input) ->
    case unicode:characters_to_list(Input) of
        Str when is_list(Str) -> from_json(Str);
        _ -> {error, invalid_unicode}
    end;
from_json(Input) when is_list(Input) ->
    try
        {Value, Rest} = parse_value(skip_ws(Input)),
        case skip_ws(Rest) of
            [] -> {ok, Value};
            _More -> {error, trailing_garbage}
        end
    catch
        throw:{json_error, Reason} -> {error, Reason}
    end.

-spec skip_ws(string()) -> string().
skip_ws([C | Rest]) when C =:= 32; C =:= $\t; C =:= $\n; C =:= $\r ->
    skip_ws(Rest);
skip_ws(Rest) ->
    Rest.

-spec parse_value(string()) -> {term(), string()}.
parse_value([${ | Rest]) -> parse_object(skip_ws(Rest), []);
parse_value([$[ | Rest]) -> parse_array(skip_ws(Rest), []);
parse_value([$" | Rest]) -> parse_string(Rest, []);
parse_value([C | _] = Input) when C =:= $-; C >= $0, C =< $9 ->
    parse_number(Input);
parse_value(Input) -> parse_literal(Input).

-spec parse_object(string(), map()) -> {map(), string()}.
parse_object([$} | Rest], Acc) ->
    {maps:from_list(lists:reverse(Acc)), Rest};
parse_object([$" | Rest], Acc) ->
    {Key, Rest1} = parse_string(Rest, []),
    case skip_ws(Rest1) of
        [$: | Rest2] ->
            {Value, Rest3} = parse_value(skip_ws(Rest2)),
            case skip_ws(Rest3) of
                [$, | Rest4] ->
                    parse_object(skip_ws(Rest4), [{Key, Value} | Acc]);
                [$} | Rest5] ->
                    {maps:from_list(lists:reverse([{Key, Value} | Acc])), Rest5};
                _ ->
                    throw({json_error, expected_object_separator})
            end;
        _ ->
            throw({json_error, expected_colon})
    end;
parse_object(_Input, _Acc) ->
    throw({json_error, expected_object_key}).

-spec parse_array(string(), list()) -> {list(), string()}.
parse_array([$] | Rest], Acc) ->
    {lists:reverse(Acc), Rest};
parse_array(Input, Acc) ->
    {Value, Rest0} = parse_value(Input),
    case skip_ws(Rest0) of
        [$, | Rest1] -> parse_array(skip_ws(Rest1), [Value | Acc]);
        [$] | Rest2] -> {lists:reverse([Value | Acc]), Rest2};
        _ -> throw({json_error, expected_array_separator})
    end.

-spec parse_string(string(), list()) -> {binary(), string()}.
parse_string([], _Acc) ->
    throw({json_error, unterminated_string});
parse_string([$" | Rest], Acc) ->
    {unicode:characters_to_binary(lists:reverse(Acc)), Rest};
parse_string([$\\, $u, H1, H2, H3, H4 | Rest], Acc) ->
    case parse_hex4([H1, H2, H3, H4]) of
        {ok, Code} -> parse_string(Rest, [Code | Acc]);
        error -> throw({json_error, bad_unicode_escape})
    end;
parse_string([$\\, C | Rest], Acc) ->
    parse_string(Rest, [unescape(C) | Acc]);
parse_string([C | Rest], Acc) ->
    parse_string(Rest, [C | Acc]).

-spec unescape(char()) -> char().
unescape($") -> $";
unescape($\\) -> $\\;
unescape($/) -> $/;
unescape($n) -> $\n;
unescape($r) -> $\r;
unescape($t) -> $\t;
unescape($b) -> $\b;
unescape($f) -> $\f;
unescape(Other) -> throw({json_error, {invalid_escape, Other}}).

-spec parse_hex4(string()) -> {ok, char()} | error.
parse_hex4(Str) ->
    try
        {ok, list_to_integer(Str, 16)}
    catch
        error:badarg -> error
    end.

-spec parse_literal(string()) -> {term(), string()}.
parse_literal("true" ++ Rest) -> {true, Rest};
parse_literal("false" ++ Rest) -> {false, Rest};
parse_literal("null" ++ Rest) -> {null, Rest};
parse_literal(_Other) -> throw({json_error, invalid_token}).

-spec parse_number(string()) -> {number(), string()}.
parse_number(Input) ->
    {Digits, Rest} = take_number(Input, []),
    Str = lists:reverse(Digits),
    try
        {decode_number(Str), Rest}
    catch
        error:badarg -> throw({json_error, {invalid_number, Str}})
    end.

-spec take_number(string(), list()) -> {list(), string()}.
take_number([C | Rest], Acc)
  when (C >= $0 andalso C =< $9) orelse C =:= $- orelse C =:= $+
       orelse C =:= $. orelse C =:= $e orelse C =:= $E ->
    take_number(Rest, [C | Acc]);
take_number(Rest, Acc) ->
    {Acc, Rest}.

%% list_to_float does not accept exponent notation, so "1e3" is split
%% into mantissa and exponent and recombined mathematically.
-spec decode_number(string()) -> number().
decode_number(Str) ->
    case string:chr(Str, $e) =:= 0 andalso string:chr(Str, $E) =:= 0 of
        true ->
            case string:chr(Str, $.) of
                0 -> list_to_integer(Str);
                _ -> list_to_float(Str)
            end;
        false ->
            {Mantissa, Exp} = split_exponent(Str),
            Base = case string:chr(Mantissa, $.) of
                       0 -> list_to_float(Mantissa ++ ".0");
                       _ -> list_to_float(Mantissa)
                   end,
            Base * math:pow(10, Exp)
    end.

-spec split_exponent(string()) -> {string(), integer()}.
split_exponent(Str) ->
    {Mantissa, Pos} = case string:chr(Str, $e) of
                          0 -> {Str, string:chr(Str, $E)};
                          P -> {string:substr(Str, 1, P - 1), P}
                      end,
    ExpStr = string:substr(Str, Pos + 1),
    Exp = case ExpStr of
              [$+ | Digits] -> list_to_integer(Digits);
              _ -> list_to_integer(ExpStr)
          end,
    {Mantissa, Exp}.
