import { expect } from "chai";
import { ethers } from "hardhat";
import { loadFixture } from "@nomicfoundation/hardhat-toolbox/network-helpers";

const ZERO = ethers.ZeroAddress;
const CREATOR_URI = "ipfs://foundev/asset-1.json";

async function tokenFixture() {
  const [admin, minter, auditor, creator, holder, payer] = await ethers.getSigners();

  const royaltyToken = await ethers.deployContract("MockERC20", ["Royalty Token", "RLT"]);
  const token = await ethers.deployContract("TokenizedAsset", [
    "Foundev Tokenized Asset",
    "FTA",
    await royaltyToken.getAddress(),
  ]);

  const MINTER_ROLE = await token.MINTER_ROLE();
  const AUDITOR_ROLE = await token.AUDITOR_ROLE();
  await token.grantRole(MINTER_ROLE, minter.address);
  await token.grantRole(AUDITOR_ROLE, auditor.address);

  return { token, royaltyToken, admin, minter, auditor, creator, holder, payer, MINTER_ROLE, AUDITOR_ROLE };
}

async function mintedTokenFixture() {
  const ctx = await tokenFixture();
  const escrowLink = ethers.Wallet.createRandom().address;
  const tx = await ctx.token
    .connect(ctx.minter)
    .mint(ctx.holder.address, ctx.creator.address, CREATOR_URI, escrowLink, 7n);
  const receipt = await tx.wait();
  const parsed = receipt!.logs
    .map((log) => {
      try {
        return ctx.token.interface.parseLog(log);
      } catch {
        return null;
      }
    })
    .find((log) => log?.name === "AssetMinted");
  const tokenId = parsed!.args.tokenId as bigint;
  return { ...ctx, escrowLink, tokenId };
}

describe("TokenizedAsset — minting and provenance", () => {
  it("mints with a full provenance record and metadata URI", async () => {
    const { token, minter, creator, holder, escrowLink, tokenId } = await mintedTokenFixture();

    expect(tokenId).to.equal(1n);
    expect(await token.ownerOf(tokenId)).to.equal(holder.address);
    expect(await token.tokenURI(tokenId)).to.equal(CREATOR_URI);

    const record = await token.provenance(tokenId);
    expect(record.originalCreator).to.equal(creator.address);
    expect(record.escrowContract).to.equal(escrowLink);
    expect(record.escrowRef).to.equal(7n);
    expect(record.audited).to.equal(false);
    expect(record.metadataURI).to.equal(CREATOR_URI);
    expect(record.mintedAt).to.be.greaterThan(0n);

    expect(await token.nextTokenId()).to.equal(2n);
  });

  it("emits AssetMinted with the escrow link", async () => {
    const { token, minter, creator, holder } = await loadFixture(tokenFixture);
    const escrowLink = ethers.Wallet.createRandom().address;

    await expect(
      token.connect(minter).mint(holder.address, creator.address, CREATOR_URI, escrowLink, 1n)
    )
      .to.emit(token, "AssetMinted")
      .withArgs(1n, holder.address, creator.address, escrowLink, 1n, CREATOR_URI);
  });

  it("rejects a zero original creator", async () => {
    const { token, minter, holder } = await loadFixture(tokenFixture);

    await expect(token.connect(minter).mint(holder.address, ZERO, CREATOR_URI, ZERO, 0n))
      .to.be.revertedWithCustomError(token, "InvalidCreator");
  });

  it("rejects tokenURI for an unknown token", async () => {
    const { token } = await loadFixture(tokenFixture);

    await expect(token.tokenURI(999n)).to.be.revertedWithCustomError(token, "ERC721NonexistentToken");
  });
});

describe("TokenizedAsset — role access control", () => {
  it("only the minter role can mint", async () => {
    const { token, minter, auditor, creator, holder, MINTER_ROLE } = await loadFixture(tokenFixture);

    await expect(
      token.connect(auditor).mint(holder.address, creator.address, CREATOR_URI, ZERO, 0n)
    )
      .to.be.revertedWithCustomError(token, "AccessControlUnauthorizedAccount")
      .withArgs(auditor.address, MINTER_ROLE);

    await expect(token.connect(minter).mint(holder.address, creator.address, CREATOR_URI, ZERO, 0n))
      .to.emit(token, "AssetMinted");
  });

  it("only the auditor role can record audits", async () => {
    const { token, minter, auditor, creator, holder, AUDITOR_ROLE } = await mintedTokenFixture();

    await expect(token.connect(minter).recordAudit(1n, true))
      .to.be.revertedWithCustomError(token, "AccessControlUnauthorizedAccount")
      .withArgs(minter.address, AUDITOR_ROLE);

    await expect(token.connect(auditor).recordAudit(1n, true))
      .to.emit(token, "AssetAudited")
      .withArgs(1n, auditor.address, true);

    expect((await token.provenance(1n)).audited).to.equal(true);

    // An auditor can also retract a verdict.
    await token.connect(auditor).recordAudit(1n, false);
    expect((await token.provenance(1n)).audited).to.equal(false);
  });

  it("only the auditor role can pause and unpause", async () => {
    const { token, minter, AUDITOR_ROLE } = await loadFixture(tokenFixture);

    await expect(token.connect(minter).pause())
      .to.be.revertedWithCustomError(token, "AccessControlUnauthorizedAccount")
      .withArgs(minter.address, AUDITOR_ROLE);
  });
});

describe("TokenizedAsset — pausing", () => {
  it("blocks minting and transfers while paused, restores after unpause", async () => {
    const { token, minter, auditor, holder, payer } = await mintedTokenFixture();

    await token.connect(auditor).pause();
    expect(await token.paused()).to.equal(true);

    await expect(
      token.connect(holder).transferFrom(holder.address, payer.address, 1n)
    ).to.be.revertedWithCustomError(token, "EnforcedPause");

    await expect(
      token
        .connect(minter)
        .mint(payer.address, holder.address, CREATOR_URI, ZERO, 0n)
    ).to.be.revertedWithCustomError(token, "EnforcedPause");

    await expect(token.connect(auditor).unpause()).to.emit(token, "Unpaused");
    expect(await token.paused()).to.equal(false);

    await token.connect(holder).transferFrom(holder.address, payer.address, 1n);
    expect(await token.ownerOf(1n)).to.equal(payer.address);
  });
});

describe("TokenizedAsset — companion royalty token (SafeERC20)", () => {
  const ROYALTY = ethers.parseEther("100");

  it("accepts royalty payments into the pool and pays the creator on claim", async () => {
    const { token, royaltyToken, creator, payer, tokenId } = await mintedTokenFixture();
    const tokenAddress = await token.getAddress();

    await royaltyToken.mint(payer.address, ROYALTY);
    await royaltyToken.connect(payer).approve(tokenAddress, ROYALTY);

    await expect(token.connect(payer).payRoyalty(tokenId, ROYALTY))
      .to.emit(token, "RoyaltyPaid")
      .withArgs(tokenId, payer.address, ROYALTY);

    expect(await token.royaltyCredits(tokenId)).to.equal(ROYALTY);
    expect(await royaltyToken.balanceOf(tokenAddress)).to.equal(ROYALTY);

    await expect(token.connect(creator).claimRoyalties(tokenId)).to.changeTokenBalances(
      royaltyToken,
      [tokenAddress, creator],
      [-ROYALTY, ROYALTY]
    );

    expect(await token.royaltyCredits(tokenId)).to.equal(0n);
    await expect(token.connect(creator).claimRoyalties(tokenId))
      .to.be.revertedWithCustomError(token, "NothingToWithdraw")
      .withArgs(tokenId);
  });

  it("only the original creator can claim royalties", async () => {
    const { token, payer, tokenId } = await mintedTokenFixture();

    await expect(token.connect(payer).claimRoyalties(tokenId))
      .to.be.revertedWithCustomError(token, "NotOriginalCreator")
      .withArgs(payer.address);
  });

  it("royalty claims stay open while the contract is paused", async () => {
    const { token, royaltyToken, auditor, creator, payer, tokenId } = await mintedTokenFixture();
    const tokenAddress = await token.getAddress();

    await royaltyToken.mint(payer.address, ROYALTY);
    await royaltyToken.connect(payer).approve(tokenAddress, ROYALTY);
    await token.connect(payer).payRoyalty(tokenId, ROYALTY);

    await token.connect(auditor).pause();

    await expect(token.connect(payer).payRoyalty(tokenId, ROYALTY))
      .to.be.revertedWithCustomError(token, "EnforcedPause");

    await expect(token.connect(creator).claimRoyalties(tokenId)).to.changeTokenBalances(
      royaltyToken,
      [tokenAddress, creator],
      [-ROYALTY, ROYALTY]
    );
  });

  it("reverts when no companion royalty token is configured", async () => {
    const { admin, creator, holder } = await loadFixture(tokenFixture);
    const bare = await ethers.deployContract("TokenizedAsset", ["Bare Asset", "BAR", ZERO]);

    await bare
      .connect(admin)
      .mint(holder.address, creator.address, CREATOR_URI, ZERO, 0n);

    await expect(bare.connect(holder).payRoyalty(1n, ROYALTY))
      .to.be.revertedWithCustomError(bare, "RoyaltyTokenNotConfigured");
  });
});
