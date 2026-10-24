// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ERC721} from "@openzeppelin/contracts/token/ERC721/ERC721.sol";
import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

/// @title TokenizedAsset
/// @author Foundev contributors
/// @notice ERC721 representing a real-world asset, composed from OpenZeppelin
///         primitives: `ERC721` for the token, `AccessControl` for minter and
///         auditor roles, `Pausable` for incident response, and `SafeERC20`
///         for a companion royalty token.
///
/// @dev Provenance is recorded per token: the original creator and an optional
/// link to the escrow instance that settled the asset's sale. Royalties are a
/// simple pull-payment pool: anyone can `payRoyalty` in the companion ERC20,
/// only the original creator can `claimRoyalties`.
///
/// @dev This is a portfolio demo. Get an independent audit before real funds.
contract TokenizedAsset is ERC721, AccessControl, Pausable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    // ---------------------------------------------------------------------
    // Roles
    // ---------------------------------------------------------------------

    /// @notice May mint new asset tokens.
    bytes32 public constant MINTER_ROLE = keccak256("MINTER_ROLE");

    /// @notice May audit provenance records and pause/unpause the contract.
    bytes32 public constant AUDITOR_ROLE = keccak256("AUDITOR_ROLE");

    // ---------------------------------------------------------------------
    // Types
    // ---------------------------------------------------------------------

    /// @notice Per-token provenance record.
    /// @param originalCreator Address credited as the creator (and paid royalties).
    /// @param escrowContract Escrow instance that settled the asset's sale, or zero.
    /// @param escrowRef Factory index / identifier of that escrow instance.
    /// @param mintedAt Unix timestamp of minting.
    /// @param audited True once an auditor has verified this record.
    /// @param metadataURI Token metadata URI (returned by `tokenURI`).
    struct Provenance {
        address originalCreator;
        address escrowContract;
        uint256 escrowRef;
        uint64 mintedAt;
        bool audited;
        string metadataURI;
    }

    // ---------------------------------------------------------------------
    // Storage
    // ---------------------------------------------------------------------

    /// @notice Companion ERC20 used to pay royalties, or zero when disabled.
    IERC20 public immutable royaltyToken;

    /// @notice Next token id to mint (ids start at 1).
    uint256 public nextTokenId = 1;

    /// @notice Royalty pool owed to each token's original creator.
    mapping(uint256 tokenId => uint256) public royaltyCredits;

    mapping(uint256 tokenId => Provenance) private _provenance;

    // ---------------------------------------------------------------------
    // Events
    // ---------------------------------------------------------------------

    /// @notice A new asset token was minted with its provenance.
    event AssetMinted(
        uint256 indexed tokenId,
        address indexed to,
        address indexed originalCreator,
        address escrowContract,
        uint256 escrowRef,
        string metadataURI
    );

    /// @notice An auditor verified (or retracted verification of) a record.
    event AssetAudited(uint256 indexed tokenId, address indexed auditor, bool passed);

    /// @notice Royalty tokens were paid into a token's royalty pool.
    event RoyaltyPaid(uint256 indexed tokenId, address indexed payer, uint256 amount);

    /// @notice The original creator pulled its royalty balance.
    event RoyaltyClaimed(uint256 indexed tokenId, address indexed creator, uint256 amount);

    // ---------------------------------------------------------------------
    // Custom errors
    // ---------------------------------------------------------------------

    /// @notice The original creator argument was the zero address.
    error InvalidCreator();

    /// @notice Amount argument must be greater than zero.
    error InvalidAmount();

    /// @notice No companion royalty token was configured for this deployment.
    error RoyaltyTokenNotConfigured();

    /// @notice Only the token's original creator may claim its royalties.
    error NotOriginalCreator(address caller);

    /// @notice Nothing to claim for the caller on this token.
    error NothingToWithdraw(uint256 tokenId);

    // ---------------------------------------------------------------------
    // Constructor
    // ---------------------------------------------------------------------

    /// @param name_ ERC721 collection name.
    /// @param symbol_ ERC721 collection symbol.
    /// @param royaltyToken_ Companion ERC20 for royalties, or address(0) to disable.
    constructor(string memory name_, string memory symbol_, address royaltyToken_)
        ERC721(name_, symbol_)
    {
        if (royaltyToken_ != address(0)) {
            royaltyToken = IERC20(royaltyToken_);
        }

        _grantRole(DEFAULT_ADMIN_ROLE, msg.sender);
        _grantRole(MINTER_ROLE, msg.sender);
        _grantRole(AUDITOR_ROLE, msg.sender);
    }

    // ---------------------------------------------------------------------
    // Minting and provenance
    // ---------------------------------------------------------------------

    /// @notice Mint a new asset token with a provenance record.
    /// @dev `whenNotPaused` plus the pause check inside `_update` mean mints
    ///      are blocked while the contract is paused.
    /// @param to Receiver of the token.
    /// @param originalCreator Creator credited in provenance and paid royalties.
    /// @param metadataURI Metadata URI returned by `tokenURI`.
    /// @param escrowContract Escrow instance that settled the sale (or zero).
    /// @param escrowRef Factory index / identifier of that escrow instance.
    /// @return tokenId The freshly minted token id.
    function mint(
        address to,
        address originalCreator,
        string calldata metadataURI,
        address escrowContract,
        uint256 escrowRef
    ) external onlyRole(MINTER_ROLE) whenNotPaused nonReentrant returns (uint256 tokenId) {
        if (originalCreator == address(0)) revert InvalidCreator();

        tokenId = nextTokenId++;
        _provenance[tokenId] = Provenance({
            originalCreator: originalCreator,
            escrowContract: escrowContract,
            escrowRef: escrowRef,
            mintedAt: uint64(block.timestamp),
            audited: false,
            metadataURI: metadataURI
        });

        _safeMint(to, tokenId);
        emit AssetMinted(tokenId, to, originalCreator, escrowContract, escrowRef, metadataURI);
    }

    /// @notice Record (or retract) an auditor verdict on a token's provenance.
    /// @param tokenId Token to audit.
    /// @param passed true to mark verified, false to retract a previous verdict.
    function recordAudit(uint256 tokenId, bool passed) external onlyRole(AUDITOR_ROLE) {
        _requireOwned(tokenId);
        _provenance[tokenId].audited = passed;
        emit AssetAudited(tokenId, msg.sender, passed);
    }

    /// @notice Full provenance record for a token (reverts for unknown ids).
    /// @param tokenId Token to read.
    /// @return The stored provenance struct.
    function provenance(uint256 tokenId) external view returns (Provenance memory) {
        _requireOwned(tokenId);
        return _provenance[tokenId];
    }

    // ---------------------------------------------------------------------
    // Incident response
    // ---------------------------------------------------------------------

    /// @notice Halt minting and transfers. Restricted to the auditor role.
    function pause() external onlyRole(AUDITOR_ROLE) {
        _pause();
    }

    /// @notice Resume minting and transfers. Restricted to the auditor role.
    function unpause() external onlyRole(AUDITOR_ROLE) {
        _unpause();
    }

    // ---------------------------------------------------------------------
    // Royalties (companion ERC20, SafeERC20 + pull payments)
    // ---------------------------------------------------------------------

    /// @notice Pay royalty tokens into a token's royalty pool.
    /// @dev Caller must have approved this contract; credit is written before
    ///      the external `safeTransferFrom` (checks-effects-interactions).
    /// @param tokenId Token whose creator should benefit.
    /// @param tokenAmount Royalty amount in companion-token units.
    function payRoyalty(uint256 tokenId, uint256 tokenAmount) external whenNotPaused nonReentrant {
        _requireOwned(tokenId);
        if (address(royaltyToken) == address(0)) revert RoyaltyTokenNotConfigured();
        if (tokenAmount == 0) revert InvalidAmount();

        royaltyCredits[tokenId] += tokenAmount;
        royaltyToken.safeTransferFrom(msg.sender, address(this), tokenAmount);
        emit RoyaltyPaid(tokenId, msg.sender, tokenAmount);
    }

    /// @notice The token's original creator pulls its royalty balance.
    /// @dev Deliberately allowed while paused: exits stay open.
    /// @param tokenId Token whose pool is being claimed.
    function claimRoyalties(uint256 tokenId) external nonReentrant {
        _requireOwned(tokenId);

        address creator = _provenance[tokenId].originalCreator;
        if (msg.sender != creator) revert NotOriginalCreator(msg.sender);

        uint256 value = royaltyCredits[tokenId];
        if (value == 0) revert NothingToWithdraw(tokenId);

        royaltyCredits[tokenId] = 0;
        emit RoyaltyClaimed(tokenId, creator, value);
        royaltyToken.safeTransfer(creator, value);
    }

    // ---------------------------------------------------------------------
    // ERC721 plumbing
    // ---------------------------------------------------------------------

    /// @inheritdoc ERC721
    function tokenURI(uint256 tokenId) public view override returns (string memory) {
        _requireOwned(tokenId);
        return _provenance[tokenId].metadataURI;
    }

    /// @dev Blocks all balance changes (mints and transfers) while paused.
    function _update(address to, uint256 tokenId, address auth) internal override returns (address) {
        _requireNotPaused();
        return super._update(to, tokenId, auth);
    }

    /// @dev Advertise ERC721, AccessControl and ERC165 interfaces.
    function supportsInterface(bytes4 interfaceId) public view override(ERC721, AccessControl) returns (bool) {
        return super.supportsInterface(interfaceId);
    }
}
