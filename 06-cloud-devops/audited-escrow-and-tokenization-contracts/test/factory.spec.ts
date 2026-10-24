import { expect } from "chai";
import { ethers } from "hardhat";
import { loadFixture } from "@nomicfoundation/hardhat-toolbox/network-helpers";
import { anyValue } from "@nomicfoundation/hardhat-chai-matchers/withArgs";

const ONE_ETH = ethers.parseEther("1");
const DELIVERY_WINDOW = 7n * 24n * 3600n;
const DISPUTE_PERIOD = 3n * 24n * 3600n;
const ZERO = ethers.ZeroAddress;

async function factoryFixture() {
  const [deployer, buyerA, buyerB, seller, arbiter] = await ethers.getSigners();
  const factory = await ethers.deployContract("EscrowFactory");
  return { factory, deployer, buyerA, buyerB, seller, arbiter };
}

async function multiCreateFixture() {
  const ctx = await factoryFixture();
  const { factory, deployer, buyerA, buyerB, seller, arbiter } = ctx;

  const txA = await factory
    .connect(buyerA)
    .createEscrow(buyerA.address, seller.address, arbiter.address, ZERO, ONE_ETH, DELIVERY_WINDOW, DISPUTE_PERIOD);
  const receiptA = await txA.wait();

  const txB = await factory
    .connect(buyerB)
    .createEscrow(buyerB.address, seller.address, arbiter.address, ZERO, ONE_ETH * 2n, DELIVERY_WINDOW, DISPUTE_PERIOD);
  await txB.wait();

  const txC = await factory
    .connect(deployer)
    .createEscrow(buyerA.address, seller.address, arbiter.address, ZERO, ONE_ETH, DELIVERY_WINDOW, DISPUTE_PERIOD);
  await txC.wait();

  return { ...ctx, txA, receiptA };
}

describe("EscrowFactory", () => {
  it("deploys an escrow and emits EscrowCreated with full details", async () => {
    const { factory, buyerA, seller, arbiter } = await loadFixture(factoryFixture);

    const tx = await factory
      .connect(buyerA)
      .createEscrow(buyerA.address, seller.address, arbiter.address, ZERO, ONE_ETH, DELIVERY_WINDOW, DISPUTE_PERIOD);
    const receipt = await tx.wait();
    const escrowAddr = (receipt!.logs[0] as any).args[0] as string;

    await expect(tx)
      .to.emit(factory, "EscrowCreated")
      .withArgs(anyValue, 0n, buyerA.address, seller.address, arbiter.address, ZERO, ONE_ETH);

    expect(await factory.isEscrow(escrowAddr)).to.equal(true);
    expect(await factory.escrowCount()).to.equal(1n);
    expect(await factory.escrowAt(0n)).to.equal(escrowAddr);
  });

  it("rejects invalid parties up front (Escrow constructor validation)", async () => {
    const { factory, buyerA, seller, arbiter } = await loadFixture(factoryFixture);

    await expect(
      factory.createEscrow(buyerA.address, seller.address, arbiter.address, ZERO, 0n, DELIVERY_WINDOW, DISPUTE_PERIOD)
    ).to.be.revertedWithCustomError(
      await ethers.getContractAt("Escrow", await factory.getAddress()),
      "InvalidAmount"
    );

    await expect(
      factory.createEscrow(buyerA.address, buyerA.address, arbiter.address, ZERO, ONE_ETH, DELIVERY_WINDOW, DISPUTE_PERIOD)
    ).to.be.reverted;
  });

  it("records and queries escrows by index, pagination and parties", async () => {
    const { factory, buyerA, seller, txA, receiptA } = await loadFixture(multiCreateFixture);

    expect(await factory.escrowCount()).to.equal(3n);

    const escrowA = (receiptA!.logs[0] as any).args[0] as string;
    expect(await factory.escrowAt(0n)).to.equal(escrowA);

    // Paginated listing.
    const page1 = await factory.getEscrows(0n, 2n);
    expect(page1).to.have.lengthOf(2);
    expect(page1[0]).to.equal(escrowA);

    const page2 = await factory.getEscrows(2n, 10n);
    expect(page2).to.have.lengthOf(1);

    const overflow = await factory.getEscrows(10n, 10n);
    expect(overflow).to.have.lengthOf(0);

    // Party indexes.
    const buyerAEscrows = await factory.escrowsOfBuyer(buyerA.address);
    expect(buyerAEscrows).to.have.lengthOf(2);
    expect(buyerAEscrows).to.include(escrowA);

    const sellerEscrows = await factory.escrowsOfSeller(seller.address);
    expect(sellerEscrows).to.have.lengthOf(3);
  });

  it("escrowAt out of range reverts with IndexOutOfBounds", async () => {
    const { factory } = await loadFixture(multiCreateFixture);

    await expect(factory.escrowAt(3n))
      .to.be.revertedWithCustomError(factory, "IndexOutOfBounds")
      .withArgs(3n, 3n);
  });

  it("does not mark foreign addresses as escrows", async () => {
    const { factory, deployer } = await loadFixture(multiCreateFixture);

    expect(await factory.isEscrow(deployer.address)).to.equal(false);
  });

  it("produces a working escrow instance", async () => {
    const { factory, buyerA, seller, arbiter, receiptA } = await loadFixture(multiCreateFixture);

    const escrowAddr = (receiptA!.logs[0] as any).args[0] as string;
    const escrow = await ethers.getContractAt("Escrow", escrowAddr);

    expect(await escrow.buyer()).to.equal(buyerA.address);
    expect(await escrow.seller()).to.equal(seller.address);
    expect(await escrow.arbiter()).to.equal(arbiter.address);

    await escrow.connect(buyerA).fund({ value: ONE_ETH });
    expect(await escrow.state()).to.equal(1n); // Funded

    await escrow.connect(buyerA).release();
    expect(await escrow.credits(seller.address)).to.equal(ONE_ETH);
  });
});
