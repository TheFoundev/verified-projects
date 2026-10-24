import { expect } from "chai";
import { ethers } from "hardhat";
import { loadFixture, time } from "@nomicfoundation/hardhat-toolbox/network-helpers";

const ONE_ETH = ethers.parseEther("1");
const HALF_ETH = ethers.parseEther("0.5");
const DELIVERY_WINDOW = 7n * 24n * 3600n;
const DISPUTE_PERIOD = 3n * 24n * 3600n;
const ZERO = ethers.ZeroAddress;

/** Enum values of `Escrow.State`, mirrored for readable assertions. */
const State = {
  Created: 0n,
  Funded: 1n,
  Disputed: 2n,
  ArbiterDecided: 3n,
  Released: 4n,
  Refunded: 5n,
} as const;

async function nativeEscrowFixture() {
  const [deployer, buyer, seller, arbiter, outsider] = await ethers.getSigners();
  const escrow = await ethers.deployContract("Escrow", [
    buyer.address,
    seller.address,
    arbiter.address,
    ZERO,
    ONE_ETH,
    DELIVERY_WINDOW,
    DISPUTE_PERIOD,
  ]);
  return { escrow, buyer, seller, arbiter, outsider, amount: ONE_ETH };
}

async function erc20EscrowFixture() {
  const [deployer, buyer, seller, arbiter, outsider] = await ethers.getSigners();
  const token = await ethers.deployContract("MockERC20", ["Settlement Dollar", "sUSD"]);
  const amount = ethers.parseEther("500");
  const escrow = await ethers.deployContract("Escrow", [
    buyer.address,
    seller.address,
    arbiter.address,
    await token.getAddress(),
    amount,
    DELIVERY_WINDOW,
    DISPUTE_PERIOD,
  ]);
  await token.mint(buyer.address, amount * 2n);
  await token.connect(buyer).approve(await escrow.getAddress(), amount);
  return { escrow, token, buyer, seller, arbiter, outsider, amount };
}

async function reentrancyFixture() {
  const [deployer, buyer, seller, arbiter] = await ethers.getSigners();
  const attacker = await ethers.deployContract("ReentrancyAttacker");
  const escrow = await ethers.deployContract("Escrow", [
    buyer.address,
    await attacker.getAddress(),
    arbiter.address,
    ZERO,
    ONE_ETH,
    DELIVERY_WINDOW,
    DISPUTE_PERIOD,
  ]);
  return { escrow, attacker, buyer, seller, arbiter, amount: ONE_ETH };
}

describe("Escrow (native ETH)", () => {
  it("starts in Created state and only accepts funding from the buyer", async () => {
    const { escrow, buyer, outsider, amount } = await loadFixture(nativeEscrowFixture);

    expect(await escrow.state()).to.equal(State.Created);

    await expect(escrow.connect(outsider).fund({ value: amount }))
      .to.be.revertedWithCustomError(escrow, "NotBuyer");

    await expect(escrow.connect(buyer).fund({ value: 0n }))
      .to.be.revertedWithCustomError(escrow, "ZeroContribution");
  });

  it("funds partially, then fully, and records the delivery deadline", async () => {
    const { escrow, buyer, amount } = await loadFixture(nativeEscrowFixture);

    await expect(escrow.connect(buyer).fund({ value: HALF_ETH }))
      .to.emit(escrow, "ContributionReceived")
      .withArgs(buyer.address, HALF_ETH, HALF_ETH);

    expect(await escrow.state()).to.equal(State.Created);
    expect(await escrow.remainingToFund()).to.equal(HALF_ETH);

    const tx = await escrow.connect(buyer).fund({ value: HALF_ETH });
    const receipt = await tx.wait();
    const block = await ethers.provider.getBlock(receipt!.blockNumber);

    expect(await escrow.state()).to.equal(State.Funded);
    expect(await escrow.funded()).to.equal(amount);
    expect(await escrow.deliveryDeadline()).to.equal(BigInt(block!.timestamp) + DELIVERY_WINDOW);
  });

  it("rejects stray ETH sent directly to the contract", async () => {
    const { escrow, buyer } = await loadFixture(nativeEscrowFixture);

    await expect(
      buyer.sendTransaction({ to: await escrow.getAddress(), value: ONE_ETH })
    ).to.be.revertedWithCustomError(escrow, "DirectEthRejected");
  });

  it("happy path: buyer releases early and the seller pulls the funds", async () => {
    const { escrow, buyer, seller, amount } = await loadFixture(nativeEscrowFixture);
    const escrowAddress = await escrow.getAddress();

    await escrow.connect(buyer).fund({ value: amount });

    await expect(escrow.connect(buyer).release())
      .to.emit(escrow, "Released")
      .withArgs(seller.address, amount, buyer.address);

    expect(await escrow.state()).to.equal(State.Released);
    expect(await escrow.credits(seller.address)).to.equal(amount);

    // Pull payment: funds only move when the seller calls withdraw.
    await expect(escrow.connect(seller).withdraw()).to.changeEtherBalances(
      [escrow, seller],
      [-amount, amount]
    );

    expect(await escrow.credits(seller.address)).to.equal(0n);
    await expect(escrow.connect(seller).withdraw())
      .to.be.revertedWithCustomError(escrow, "NothingToWithdraw")
      .withArgs(seller.address);
  });

  it("timeout release pays the seller after the delivery window expires", async () => {
    const { escrow, buyer, seller, outsider, amount } = await loadFixture(nativeEscrowFixture);
    const escrowAddress = await escrow.getAddress();

    await escrow.connect(buyer).fund({ value: amount });

    await expect(escrow.connect(outsider).timeoutRelease())
      .to.be.revertedWithCustomError(escrow, "DeadlineNotReached")
      .withArgs(await escrow.deliveryDeadline());

    await time.increaseTo(await escrow.deliveryDeadline());

    await expect(escrow.connect(outsider).timeoutRelease()).to.emit(escrow, "Released");
    expect(await escrow.state()).to.equal(State.Released);

    await expect(escrow.connect(seller).withdraw()).to.changeEtherBalances(
      [escrow, seller],
      [-amount, amount]
    );
  });

  it("seller refunds voluntarily and the buyer pulls the funds back", async () => {
    const { escrow, buyer, seller, amount } = await loadFixture(nativeEscrowFixture);
    const escrowAddress = await escrow.getAddress();

    await escrow.connect(buyer).fund({ value: amount });

    await expect(escrow.connect(seller).refund())
      .to.emit(escrow, "Refunded")
      .withArgs(buyer.address, amount, seller.address);

    expect(await escrow.state()).to.equal(State.Refunded);

    await expect(escrow.connect(buyer).withdraw()).to.changeEtherBalances(
      [escrow, buyer],
      [-amount, amount]
    );
  });

  it("either party can cancel before full funding; the partial amount returns to the buyer", async () => {
    const { escrow, buyer, seller, outsider } = await loadFixture(nativeEscrowFixture);
    const escrowAddress = await escrow.getAddress();

    await escrow.connect(buyer).fund({ value: HALF_ETH });

    await expect(escrow.connect(outsider).cancel())
      .to.be.revertedWithCustomError(escrow, "NotParty");

    await expect(escrow.connect(seller).cancel())
      .to.emit(escrow, "Cancelled")
      .withArgs(seller.address, HALF_ETH);

    expect(await escrow.state()).to.equal(State.Refunded);
    expect(await escrow.credits(buyer.address)).to.equal(HALF_ETH);

    await expect(escrow.connect(buyer).withdraw()).to.changeEtherBalances(
      [escrow, buyer],
      [-HALF_ETH, HALF_ETH]
    );
  });
});

describe("Escrow (dispute resolution)", () => {
  async function fundedAndDisputedFixture() {
    const ctx = await loadFixture(nativeEscrowFixture);
    await ctx.escrow.connect(ctx.buyer).fund({ value: ctx.amount });
    await ctx.escrow.connect(ctx.buyer).raiseDispute("item arrived damaged");
    return ctx;
  }

  it("only buyer or seller can open a dispute, and only while funded", async () => {
    const { escrow, buyer, outsider, amount } = await loadFixture(nativeEscrowFixture);

    await expect(escrow.connect(outsider).raiseDispute("nope"))
      .to.be.revertedWithCustomError(escrow, "NotParty");

    await expect(escrow.connect(buyer).raiseDispute("too early"))
      .to.be.revertedWithCustomError(escrow, "InvalidState")
      .withArgs(State.Funded, State.Created);

    await escrow.connect(buyer).fund({ value: amount });

    await expect(escrow.connect(buyer).raiseDispute("ok")).to.emit(escrow, "DisputeRaised");

    expect(await escrow.disputeDeadline()).to.equal(
      BigInt(await time.latest()) + DISPUTE_PERIOD
    );
  });

  it("dispute → arbiter rules for the seller → funds released", async () => {
    const { escrow, buyer, seller, arbiter, outsider, amount } =
      await fundedAndDisputedFixture();
    const escrowAddress = await escrow.getAddress();

    expect(await escrow.state()).to.equal(State.Disputed);

    // Buyer cannot release or refund while a dispute is open.
    await expect(escrow.connect(buyer).release())
      .to.be.revertedWithCustomError(escrow, "InvalidState")
      .withArgs(State.Funded, State.Disputed);

    // Only the arbiter may rule.
    await expect(escrow.connect(outsider).arbiterRule(true))
      .to.be.revertedWithCustomError(escrow, "NotArbiter");

    await expect(escrow.connect(arbiter).arbiterRule(true))
      .to.emit(escrow, "RulingRecorded")
      .withArgs(arbiter.address, true);

    expect(await escrow.state()).to.equal(State.ArbiterDecided);
    expect(await escrow.rulingForSeller()).to.equal(true);

    await expect(escrow.connect(outsider).executeRuling())
      .to.emit(escrow, "RulingExecuted")
      .withArgs(true, outsider.address, seller.address, amount);

    expect(await escrow.state()).to.equal(State.Released);
    expect(await escrow.credits(seller.address)).to.equal(amount);

    await expect(escrow.connect(seller).withdraw()).to.changeEtherBalances(
      [escrow, seller],
      [-amount, amount]
    );
  });

  it("dispute → arbiter rules for the buyer → funds refunded", async () => {
    const { escrow, buyer, arbiter, outsider, amount } = await fundedAndDisputedFixture();
    const escrowAddress = await escrow.getAddress();

    await escrow.connect(arbiter).arbiterRule(false);
    expect(await escrow.rulingForSeller()).to.equal(false);

    await escrow.connect(outsider).executeRuling();
    expect(await escrow.state()).to.equal(State.Refunded);
    expect(await escrow.credits(buyer.address)).to.equal(amount);

    await expect(escrow.connect(buyer).withdraw()).to.changeEtherBalances(
      [escrow, buyer],
      [-amount, amount]
    );
  });

  it("a ruling executed twice is rejected (terminal states)", async () => {
    const { escrow, arbiter, outsider } = await fundedAndDisputedFixture();

    await escrow.connect(arbiter).arbiterRule(true);
    await escrow.connect(outsider).executeRuling();

    await expect(escrow.connect(outsider).executeRuling())
      .to.be.revertedWithCustomError(escrow, "InvalidState")
      .withArgs(State.ArbiterDecided, State.Released);
  });

  it("timeout refund pays the buyer when the arbiter stays silent", async () => {
    const { escrow, buyer, arbiter, outsider, amount } = await fundedAndDisputedFixture();
    const escrowAddress = await escrow.getAddress();

    await expect(escrow.connect(outsider).timeoutRefund())
      .to.be.revertedWithCustomError(escrow, "DisputeStillOpen")
      .withArgs(await escrow.disputeDeadline());

    await time.increaseTo(await escrow.disputeDeadline());

    // The arbiter window expired: ruling is no longer accepted.
    await expect(escrow.connect(arbiter).arbiterRule(true))
      .to.be.revertedWithCustomError(escrow, "RulingExpired");

    await expect(escrow.connect(outsider).timeoutRefund()).to.emit(escrow, "Refunded");
    expect(await escrow.state()).to.equal(State.Refunded);

    await expect(escrow.connect(buyer).withdraw()).to.changeEtherBalances(
      [escrow, buyer],
      [-amount, amount]
    );
  });

  it("the dispute window closes once the delivery deadline passes", async () => {
    const { escrow, buyer } = await loadFixture(nativeEscrowFixture);

    await escrow.connect(buyer).fund({ value: ONE_ETH });
    await time.increaseTo(await escrow.deliveryDeadline());

    await expect(escrow.connect(buyer).raiseDispute("too late"))
      .to.be.revertedWithCustomError(escrow, "DisputeWindowClosed");
  });
});

describe("Escrow (ERC20 settlement)", () => {
  it("funds, releases and settles in ERC20 tokens via SafeERC20", async () => {
    const { escrow, token, buyer, seller, amount } = await loadFixture(erc20EscrowFixture);
    const escrowAddress = await escrow.getAddress();

    await escrow.connect(buyer).fundERC20(amount);
    expect(await escrow.state()).to.equal(State.Funded);
    expect(await token.balanceOf(escrowAddress)).to.equal(amount);

    await escrow.connect(buyer).release();
    expect(await escrow.credits(seller.address)).to.equal(amount);

    // Pull payment in tokens.
    await expect(escrow.connect(seller).withdraw()).to.changeTokenBalances(
      token,
      [escrowAddress, seller],
      [-amount, amount]
    );
    expect(await token.balanceOf(escrowAddress)).to.equal(0n);
  });

  it("overfunding the remaining amount reverts", async () => {
    const { escrow, buyer, amount } = await loadFixture(erc20EscrowFixture);

    await escrow.connect(buyer).fundERC20(amount / 2n);

    await expect(escrow.connect(buyer).fundERC20(amount))
      .to.be.revertedWithCustomError(escrow, "ContributionTooHigh")
      .withArgs(amount, amount / 2n);
  });

  it("funding functions reject a mismatched settlement asset", async () => {
    const { escrow, buyer, amount } = await loadFixture(nativeEscrowFixture);
    const { escrow: erc20Escrow, buyer: erc20Buyer } = await loadFixture(erc20EscrowFixture);

    // ETH fund() on an ERC20 escrow
    await expect(erc20Escrow.connect(erc20Buyer).fund({ value: amount }))
      .to.be.revertedWithCustomError(erc20Escrow, "AssetMismatch");

    // fundERC20() on an ETH escrow
    await expect(escrow.connect(buyer).fundERC20(amount))
      .to.be.revertedWithCustomError(escrow, "AssetMismatch");
  });

  it("funding an ERC20 escrow without approval reverts", async () => {
    const { escrow, token, buyer } = await loadFixture(erc20EscrowFixture);

    await token.connect(buyer).approve(await escrow.getAddress(), 0n);

    await expect(escrow.connect(buyer).fundERC20(1n)).to.be.reverted;
  });
});

describe("Escrow (reentrancy)", () => {
  it("blocks a re-entrant withdraw from the seller's receive hook", async () => {
    const { escrow, attacker, buyer, amount } = await loadFixture(reentrancyFixture);
    const attackerAddress = await attacker.getAddress();

    // Buyer funds a normal escrow whose seller is the attacker contract.
    await escrow.connect(buyer).fund({ value: amount });
    await time.increaseTo(await escrow.deliveryDeadline());
    await escrow.timeoutRelease(); // anyone can push the funds after the deadline
    expect(await escrow.credits(attackerAddress)).to.equal(amount);

    await attacker.attack(escrow.getAddress());

    // The legitimate payout went through exactly once...
    expect(await ethers.provider.getBalance(attackerAddress)).to.equal(amount);
    expect(await escrow.credits(attackerAddress)).to.equal(0n);

    // ...and the re-entrant call was rejected.
    expect(await attacker.reentrancyBlocked()).to.equal(true);
    expect(await attacker.reentrancySucceeded()).to.equal(false);
  });

  it("a second legitimate withdraw finds an empty credit", async () => {
    const { escrow, attacker, buyer, amount } = await loadFixture(reentrancyFixture);

    await escrow.connect(buyer).fund({ value: amount });
    await time.increaseTo(await escrow.deliveryDeadline());
    await escrow.timeoutRelease();

    await attacker.attack(escrow.getAddress());

    await expect(attacker.attack(escrow.getAddress()))
      .to.be.revertedWithCustomError(escrow, "NothingToWithdraw")
      .withArgs(await attacker.getAddress());
  });
});
