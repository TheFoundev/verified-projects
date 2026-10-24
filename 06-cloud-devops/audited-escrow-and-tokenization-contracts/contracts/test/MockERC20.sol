// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

/// @title MockERC20
/// @author Foundev contributors
/// @notice Test-only ERC20 with an open `mint`, used to exercise the ERC20
///         escrow path and the companion royalty token.
/// @dev Never deploy outside tests.
contract MockERC20 is ERC20 {
    constructor(string memory name_, string memory symbol_) ERC20(name_, symbol_) {}

    /// @notice Free mint for test setup.
    function mint(address to, uint256 value) external {
        _mint(to, value);
    }
}
