// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Escrow} from "../Escrow.sol";

/// @title ReentrancyAttacker
/// @author Foundev contributors
/// @notice Test-only attacker used to prove that `Escrow.withdraw` cannot be
///         re-entered. It plays the seller role: once the escrow credits it,
///         `attack(escrow)` calls `withdraw` and the incoming ETH transfer
///         tries to call `withdraw` again from the `receive` hook.
/// @dev The escrow is passed to `attack` (not the constructor) so the escrow
///      can be constructed with this contract's address as its seller first.
///      Never deploy outside tests.
contract ReentrancyAttacker {
    /// @notice The escrow currently under attack.
    Escrow public attackedEscrow;

    /// @notice True when the second (malicious) `withdraw` unexpectedly succeeded.
    bool public reentrancySucceeded;

    /// @notice True when the second `withdraw` was rejected by the escrow.
    bool public reentrancyBlocked;

    /// @notice Arms the hook for the duration of `attack`.
    bool private _armed;

    /// @notice Perform the legitimate withdrawal; `receive` fires the re-entry.
    /// @param target_ Escrow whose `withdraw` is being attacked.
    function attack(Escrow target_) external {
        attackedEscrow = target_;
        _armed = true;
        target_.withdraw();
        _armed = false;
    }

    /// @dev Re-enters `withdraw` while the ETH transfer is in flight.
    receive() external payable {
        if (!_armed || address(attackedEscrow) == address(0)) {
            return;
        }
        try attackedEscrow.withdraw() {
            reentrancySucceeded = true;
        } catch {
            reentrancyBlocked = true;
        }
    }
}
