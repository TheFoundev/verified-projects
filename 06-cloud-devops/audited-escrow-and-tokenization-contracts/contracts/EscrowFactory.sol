// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Escrow} from "./Escrow.sol";

/// @title EscrowFactory
/// @author Foundev contributors
/// @notice Deploys `Escrow` instances and keeps an on-chain, queryable index
///         of every escrow it has created.
/// @dev This is a portfolio demo. Get an independent audit before real funds.
contract EscrowFactory {
    /// @notice Every escrow created by this factory, in creation order.
    address[] private _escrows;

    /// @notice O(1) membership check for addresses created by this factory.
    mapping(address => bool) public isEscrow;

    /// @notice Escrows where the given address is the buyer.
    mapping(address => address[]) private _escrowsByBuyer;

    /// @notice Escrows where the given address is the seller.
    mapping(address => address[]) private _escrowsBySeller;

    /// @notice Emitted whenever a new escrow instance is deployed.
    event EscrowCreated(
        address indexed escrow,
        uint256 index,
        address indexed buyer,
        address indexed seller,
        address arbiter,
        address asset,
        uint256 amount
    );

    /// @notice Emitted when an index query is out of range (also reverts).
    error IndexOutOfBounds(uint256 index, uint256 length);

    /// @notice Deploy a new escrow instance and record it.
    /// @param buyer Party that funds the escrow.
    /// @param seller Party paid on release.
    /// @param arbiter Third party that rules on disputes.
    /// @param asset ERC20 settlement token, or address(0) for native ETH.
    /// @param amount Total amount the buyer must fund.
    /// @param deliveryWindow Seconds after funding within which disputes may be raised.
    /// @param disputePeriod Seconds the arbiter has to rule once a dispute is open.
    /// @return escrow Address of the freshly deployed `Escrow` instance.
    function createEscrow(
        address buyer,
        address seller,
        address arbiter,
        address asset,
        uint256 amount,
        uint64 deliveryWindow,
        uint64 disputePeriod
    ) external returns (address escrow) {
        escrow = address(
            new Escrow(buyer, seller, arbiter, asset, amount, deliveryWindow, disputePeriod)
        );

        uint256 index = _escrows.length;
        _escrows.push(escrow);
        isEscrow[escrow] = true;
        _escrowsByBuyer[buyer].push(escrow);
        _escrowsBySeller[seller].push(escrow);

        emit EscrowCreated(escrow, index, buyer, seller, arbiter, asset, amount);
    }

    /// @notice Total number of escrows created so far.
    function escrowCount() external view returns (uint256) {
        return _escrows.length;
    }

    /// @notice Escrow at a creation-order index.
    /// @param index Zero-based index.
    function escrowAt(uint256 index) external view returns (address) {
        if (index >= _escrows.length) revert IndexOutOfBounds(index, _escrows.length);
        return _escrows[index];
    }

    /// @notice Paginated view over all created escrows.
    /// @param offset Zero-based start index.
    /// @param limit Maximum number of entries to return (clamped to the end).
    function getEscrows(uint256 offset, uint256 limit) external view returns (address[] memory page) {
        uint256 total = _escrows.length;
        if (offset >= total) {
            return new address[](0);
        }

        uint256 end = offset + limit;
        if (end > total) {
            end = total;
        }

        page = new address[](end - offset);
        for (uint256 i = offset; i < end; i++) {
            page[i - offset] = _escrows[i];
        }
    }

    /// @notice All escrows where `party` is the buyer.
    function escrowsOfBuyer(address party) external view returns (address[] memory) {
        return _escrowsByBuyer[party];
    }

    /// @notice All escrows where `party` is the seller.
    function escrowsOfSeller(address party) external view returns (address[] memory) {
        return _escrowsBySeller[party];
    }
}
