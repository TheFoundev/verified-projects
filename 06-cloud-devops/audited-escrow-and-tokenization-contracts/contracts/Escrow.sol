// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

/// @title Escrow
/// @author Foundev contributors
/// @notice Multi-party escrow between a buyer, a seller and an arbiter, settled
///         in native ETH or a single ERC20 chosen at construction time.
///
/// @dev Design notes:
/// - Explicit state machine: Created -> Funded -> {Released | Disputed | Refunded}
///      and Disputed -> ArbiterDecided -> {Released | Refunded}.
/// - All value moves are pull-payments: `release`/`refund`/`timeout*` only write
///   internal `credits`; value leaves the contract exclusively through
///   `withdraw`, which follows checks-effects-interactions and is protected by
///   OpenZeppelin's `ReentrancyGuard`.
/// - The contract rejects stray ETH (`receive` reverts), so the only way in is
///   the audited `fund` path.
/// - Deadlines: after the delivery window expires the funds can be released to
///   the seller without a dispute; after a dispute window expires (arbiter
///   silent) the funds can be refunded to the buyer.
///
/// @dev This is a portfolio demo. Get an independent audit before real funds.
contract Escrow is ReentrancyGuard {
    using SafeERC20 for IERC20;

    // ---------------------------------------------------------------------
    // State machine
    // ---------------------------------------------------------------------

    /// @notice Lifecycle states of an escrow instance.
    enum State {
        Created, // 0: instantiated, waiting for the buyer to fund
        Funded, // 1: fully funded, delivery window running
        Disputed, // 2: a dispute is open, arbiter window running
        ArbiterDecided, // 3: arbiter recorded a ruling, awaiting execution
        Released, // 4: funds credited to the seller (terminal)
        Refunded // 5: funds credited to the buyer (terminal)
    }

    // ---------------------------------------------------------------------
    // Immutable configuration
    // ---------------------------------------------------------------------

    /// @notice The party that funds the escrow and receives refunds.
    address public immutable buyer;

    /// @notice The party that receives funds on release.
    address public immutable seller;

    /// @notice Neutral third party that settles disputes.
    address public immutable arbiter;

    /// @notice ERC20 used for settlement, or the zero address for native ETH.
    address public immutable asset;

    /// @notice Target funding amount (wei for ETH, token units for ERC20).
    uint256 public immutable amount;

    /// @notice Seconds after full funding during which a dispute may be raised.
    uint64 public immutable deliveryWindow;

    /// @notice Seconds a dispute stays open for the arbiter to rule.
    uint64 public immutable disputePeriod;

    // ---------------------------------------------------------------------
    // Mutable state
    // ---------------------------------------------------------------------

    /// @notice Current lifecycle state.
    State public state;

    /// @notice Amount funded so far. Equals `amount` once state is `Funded`.
    uint256 public funded;

    /// @notice Timestamp after which `timeoutRelease` is allowed (0 until funded).
    uint256 public deliveryDeadline;

    /// @notice Timestamp after which `timeoutRefund` is allowed (0 until disputed).
    uint256 public disputeDeadline;

    /// @notice Direction of the recorded arbiter ruling (true = pay the seller).
    bool public rulingForSeller;

    /// @notice Pull-payment balances. Value only leaves via `withdraw`.
    mapping(address => uint256) public credits;

    // ---------------------------------------------------------------------
    // Events
    // ---------------------------------------------------------------------

    /// @notice A partial (or final) contribution arrived from the buyer.
    event ContributionReceived(address indexed funder, uint256 value, uint256 totalFunded);

    /// @notice The escrow became fully funded and the delivery window opened.
    event EscrowFunded(uint256 amount, uint256 deliveryDeadline);

    /// @notice Funds were credited to the seller.
    event Released(address indexed beneficiary, uint256 amount, address indexed executedBy);

    /// @notice Funds were credited back to the buyer.
    event Refunded(address indexed beneficiary, uint256 amount, address indexed executedBy);

    /// @notice A not-yet-funded escrow was cancelled by one of the parties.
    event Cancelled(address indexed by, uint256 amountReturned);

    /// @notice A party opened a dispute and the arbiter window started.
    event DisputeRaised(address indexed raisedBy, string reason, uint256 disputeDeadline);

    /// @notice The arbiter recorded a ruling while the dispute was open.
    event RulingRecorded(address indexed arbiter, bool releaseToSeller);

    /// @notice The recorded ruling was executed and funds were credited.
    event RulingExecuted(bool releaseToSeller, address indexed executor, address indexed beneficiary, uint256 amount);

    /// @notice Internal credit was booked for an account (pull payment).
    event Credited(address indexed account, uint256 amount);

    /// @notice An account pulled its credited balance out of the escrow.
    event Withdrawn(address indexed account, uint256 amount);

    // ---------------------------------------------------------------------
    // Custom errors
    // ---------------------------------------------------------------------

    /// @notice Caller is not the buyer.
    error NotBuyer();

    /// @notice Caller is not the seller.
    error NotSeller();

    /// @notice Caller is not the arbiter.
    error NotArbiter();

    /// @notice Caller is neither the buyer nor the seller.
    error NotParty();

    /// @notice Operation not valid in the current state.
    error InvalidState(State expected, State actual);

    /// @notice A required address argument was the zero address.
    error InvalidAddress();

    /// @notice Amount argument must be greater than zero.
    error InvalidAmount();

    /// @notice Window argument (delivery or dispute) must be greater than zero.
    error InvalidWindow();

    /// @notice Same address used for more than one role, or buyer == seller.
    error DuplicateParty();

    /// @notice Contribution exceeds the remaining amount to fund.
    error ContributionTooHigh(uint256 offered, uint256 remaining);

    /// @notice Zero-value contribution.
    error ZeroContribution();

    /// @notice The delivery deadline already passed; the dispute window is closed.
    error DisputeWindowClosed();

    /// @notice The arbiter window already expired; use `timeoutRefund`.
    error RulingExpired();

    /// @notice The arbiter window has not expired yet; use `arbiterRule`.
    error DisputeStillOpen(uint256 disputeDeadline);

    /// @notice The delivery deadline has not been reached yet.
    error DeadlineNotReached(uint256 deadline);

    /// @notice Nothing to withdraw for the caller.
    error NothingToWithdraw(address account);

    /// @notice Native ETH transfer to the withdrawer failed.
    error EthTransferFailed(address to);

    /// @notice Direct ETH sends are rejected; use `fund`.
    error DirectEthRejected();

    /// @notice Funding function does not match the configured settlement asset.
    error AssetMismatch();

    // ---------------------------------------------------------------------
    // Constructor
    // ---------------------------------------------------------------------

    /// @param buyer_ Party that funds the escrow and is refunded on failure.
    /// @param seller_ Party paid on release.
    /// @param arbiter_ Third party that rules on disputes.
    /// @param asset_ ERC20 settlement token, or address(0) for native ETH.
    /// @param amount_ Total amount the buyer must fund.
    /// @param deliveryWindow_ Seconds after funding within which disputes may be raised.
    /// @param disputePeriod_ Seconds the arbiter has to rule once a dispute is open.
    constructor(
        address buyer_,
        address seller_,
        address arbiter_,
        address asset_,
        uint256 amount_,
        uint64 deliveryWindow_,
        uint64 disputePeriod_
    ) {
        if (buyer_ == address(0) || seller_ == address(0) || arbiter_ == address(0)) revert InvalidAddress();
        if (buyer_ == seller_ || buyer_ == arbiter_ || seller_ == arbiter_) revert DuplicateParty();
        if (amount_ == 0) revert InvalidAmount();
        if (deliveryWindow_ == 0 || disputePeriod_ == 0) revert InvalidWindow();

        buyer = buyer_;
        seller = seller_;
        arbiter = arbiter_;
        asset = asset_;
        amount = amount_;
        deliveryWindow = deliveryWindow_;
        disputePeriod = disputePeriod_;

        state = State.Created;
    }

    /// @dev Rejects stray ETH so funds can only enter through `fund`.
    receive() external payable {
        revert DirectEthRejected();
    }

    // ---------------------------------------------------------------------
    // Funding
    // ---------------------------------------------------------------------

    /// @notice Fund the escrow with native ETH (partial funding allowed).
    /// @dev Only the buyer may fund. Reaching `amount` transitions to `Funded`
    ///      and opens the delivery window.
    function fund() external payable nonReentrant {
        if (msg.sender != buyer) revert NotBuyer();
        if (state != State.Created) revert InvalidState(State.Created, state);
        if (asset != address(0)) revert AssetMismatch();
        if (msg.value == 0) revert ZeroContribution();

        uint256 remaining = amount - funded;
        if (msg.value > remaining) revert ContributionTooHigh(msg.value, remaining);

        funded += msg.value;
        emit ContributionReceived(msg.sender, msg.value, funded);

        if (funded == amount) {
            state = State.Funded;
            deliveryDeadline = block.timestamp + deliveryWindow;
            emit EscrowFunded(amount, deliveryDeadline);
        }
    }

    /// @notice Fund the escrow with the ERC20 chosen at construction.
    /// @dev Uses `SafeERC20`, so non-standard (no-return-value) tokens work.
    ///      The buyer must have approved this contract for `contribution`.
    /// @param contribution Token units to pull from the buyer.
    function fundERC20(uint256 contribution) external nonReentrant {
        if (msg.sender != buyer) revert NotBuyer();
        if (state != State.Created) revert InvalidState(State.Created, state);
        if (asset == address(0)) revert AssetMismatch();
        if (contribution == 0) revert ZeroContribution();

        uint256 remaining = amount - funded;
        if (contribution > remaining) revert ContributionTooHigh(contribution, remaining);

        funded += contribution;
        IERC20(asset).safeTransferFrom(buyer, address(this), contribution);
        emit ContributionReceived(msg.sender, contribution, funded);

        if (funded == amount) {
            state = State.Funded;
            deliveryDeadline = block.timestamp + deliveryWindow;
            emit EscrowFunded(amount, deliveryDeadline);
        }
    }

    // ---------------------------------------------------------------------
    // Uncontested paths
    // ---------------------------------------------------------------------

    /// @notice Buyer confirms receipt and releases the funds to the seller.
    /// @dev Allowed any time the escrow is fully funded and not disputed.
    function release() external {
        if (msg.sender != buyer) revert NotBuyer();
        if (state != State.Funded) revert InvalidState(State.Funded, state);
        _settle(State.Released, seller, msg.sender);
    }

    /// @notice Anyone may push the funds to the seller once the delivery
    ///         deadline has passed without a dispute (timeout release).
    function timeoutRelease() external {
        if (state != State.Funded) revert InvalidState(State.Funded, state);
        if (block.timestamp < deliveryDeadline) revert DeadlineNotReached(deliveryDeadline);
        _settle(State.Released, seller, msg.sender);
    }

    /// @notice Seller voluntarily returns the funds to the buyer before any dispute.
    function refund() external {
        if (msg.sender != seller) revert NotSeller();
        if (state != State.Funded) revert InvalidState(State.Funded, state);
        _settle(State.Refunded, buyer, msg.sender);
    }

    /// @notice Either party may cancel an escrow that is not fully funded yet.
    /// @dev Any partial contribution is credited back to the buyer.
    function cancel() external {
        if (msg.sender != buyer && msg.sender != seller) revert NotParty();
        if (state != State.Created) revert InvalidState(State.Created, state);

        state = State.Refunded;
        if (funded > 0) {
            _credit(buyer, funded);
        }
        emit Cancelled(msg.sender, funded);
    }

    // ---------------------------------------------------------------------
    // Dispute path
    // ---------------------------------------------------------------------

    /// @notice Buyer or seller opens a dispute while the delivery window runs.
    /// @param reason Free-text description of the dispute, stored in the event log.
    function raiseDispute(string calldata reason) external {
        if (msg.sender != buyer && msg.sender != seller) revert NotParty();
        if (state != State.Funded) revert InvalidState(State.Funded, state);
        if (block.timestamp >= deliveryDeadline) revert DisputeWindowClosed();

        state = State.Disputed;
        disputeDeadline = block.timestamp + disputePeriod;
        emit DisputeRaised(msg.sender, reason, disputeDeadline);
    }

    /// @notice Arbiter records a ruling while the dispute window is open.
    /// @dev Two-phase by design: recording the ruling only flips state to
    ///      `ArbiterDecided`; `executeRuling` performs the crediting, so a
    ///      mistaken click is visible on-chain before funds are booked.
    /// @param releaseToSeller true to pay the seller, false to refund the buyer.
    function arbiterRule(bool releaseToSeller) external {
        if (msg.sender != arbiter) revert NotArbiter();
        if (state != State.Disputed) revert InvalidState(State.Disputed, state);
        if (block.timestamp >= disputeDeadline) revert RulingExpired();

        rulingForSeller = releaseToSeller;
        state = State.ArbiterDecided;
        emit RulingRecorded(msg.sender, releaseToSeller);
    }

    /// @notice Anyone may execute a recorded ruling and book the credit.
    function executeRuling() external {
        if (state != State.ArbiterDecided) revert InvalidState(State.ArbiterDecided, state);

        if (rulingForSeller) {
            _settle(State.Released, seller, msg.sender);
        } else {
            _settle(State.Refunded, buyer, msg.sender);
        }
        emit RulingExecuted(rulingForSeller, msg.sender, rulingForSeller ? seller : buyer, funded);
    }

    /// @notice Anyone may refund the buyer once the arbiter has been silent
    ///         past the dispute deadline (timeout refund).
    function timeoutRefund() external {
        if (state != State.Disputed) revert InvalidState(State.Disputed, state);
        if (block.timestamp < disputeDeadline) revert DisputeStillOpen(disputeDeadline);
        _settle(State.Refunded, buyer, msg.sender);
    }

    // ---------------------------------------------------------------------
    // Pull payments
    // ---------------------------------------------------------------------

    /// @notice Withdraw the caller's full credited balance.
    /// @dev Checks-effects-interactions: the credit is zeroed before the
    ///      external call, and `nonReentrant` blocks re-entry outright.
    function withdraw() external nonReentrant {
        uint256 value = credits[msg.sender];
        if (value == 0) revert NothingToWithdraw(msg.sender);

        credits[msg.sender] = 0;
        emit Withdrawn(msg.sender, value);

        if (asset == address(0)) {
            (bool ok, ) = msg.sender.call{value: value}("");
            if (!ok) revert EthTransferFailed(msg.sender);
        } else {
            IERC20(asset).safeTransfer(msg.sender, value);
        }
    }

    // ---------------------------------------------------------------------
    // Views
    // ---------------------------------------------------------------------

    /// @return The amount still required to fully fund the escrow.
    function remainingToFund() external view returns (uint256) {
        return amount - funded;
    }

    // ---------------------------------------------------------------------
    // Internals
    // ---------------------------------------------------------------------

    /// @dev Terminal helper: moves the escrow to `next` and credits `beneficiary`.
    ///      No external calls here — crediting is bookkeeping only.
    function _settle(State next, address beneficiary, address executedBy) private {
        uint256 value = funded;
        state = next;

        if (value > 0) {
            _credit(beneficiary, value);
        }

        if (next == State.Released) {
            emit Released(beneficiary, value, executedBy);
        } else {
            emit Refunded(beneficiary, value, executedBy);
        }
    }

    /// @dev Books a pull-payment credit.
    function _credit(address account, uint256 value) private {
        credits[account] += value;
        emit Credited(account, value);
    }
}
